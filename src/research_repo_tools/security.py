"""Native scanner gates with explicit inventory and fresh, validated reports."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast

from research_repo_tools import config, files, toolchain, toolchain_config
from research_repo_tools.evidence import _load_json
from research_repo_tools.process import run_command_bytes, run_git_bytes
from research_repo_tools.scanner_output import FindingOutput, _print, _text
from research_repo_tools.selection import select_files

__all__ = ["check_secret_exceptions", "scan_osv", "scan_secrets", "security_inventory", "show_secret_findings"]

GENERATED = tuple(f"**/{name}/**" for name in (".venv", "venv", ".tox", ".nox", "__pycache__", "target", "dist", "build", "node_modules"))
REPORT_LIMIT = 128 * 1024 * 1024
GITLEAKS_FINDINGS_EXIT = 3


def security_inventory(root: Path, *, include: tuple[str, ...] = (), exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Tracked and nonignored files; reject selected links and submodules.

    Standard environment/build directories are excluded even if tracked.
    Consumers supply additional exclusions; ignored untracked files stay out.
    """
    return select_files(root, include=include, exclude=(*GENERATED, *exclude))


def _binary(settings: config.Config, name: str) -> tuple[Path, dict[str, str]]:
    runtime = toolchain.Runtime(toolchain_config.load(settings))
    selected = next((tool for tool in runtime.plan.binaries if tool.name == name), None)
    if selected is None:
        raise ValueError(f"declare an exact {name} pin in toolchain.binaries and run just setup")
    status = runtime.binary_status(selected)
    if not status.ok:
        raise ValueError(f"managed {name} {selected.version} is unavailable; run just setup")
    return runtime.binary_path(selected), runtime.environment()


def _sarif(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or value.get("version") != "2.1.0" or not isinstance(runs := value.get("runs"), list) or not runs:
        raise ValueError("invalid SARIF report")
    results = []
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("tool"), dict) or not isinstance(findings := run.get("results"), list):
            raise ValueError("invalid SARIF run")
        if any(not isinstance(finding, dict) or not isinstance(finding.get("message"), dict) for finding in findings):
            raise ValueError("invalid SARIF finding")
        invocations = run.get("invocations", [])
        if not isinstance(invocations, list) or any(
            not isinstance(invocation, dict) or type(invocation.get("executionSuccessful")) is not bool for invocation in invocations
        ):
            raise ValueError("invalid SARIF invocation")
        if any(invocation["executionSuccessful"] is False for invocation in invocations):
            raise ValueError("SARIF reports an unsuccessful invocation")
        results.extend(findings)
    return results


def _osv_report(value: object, lockfiles: set[Path]) -> bool:
    if not isinstance(value, dict) or not isinstance(results := value.get("results"), list):
        raise ValueError("invalid OSV JSON report")
    scanned = set()
    found = False
    for item in results:
        if not isinstance(item, dict) or not isinstance(source := item.get("source"), dict) or not isinstance(path := source.get("path"), str):
            raise ValueError("invalid OSV source")
        if not Path(path).is_absolute():
            raise ValueError("OSV must report absolute source paths")
        scanned.add(Path(path).resolve())
        if not isinstance(packages := item.get("packages"), list) or not packages:
            raise ValueError("OSV source has no scanned packages")
        for package in packages:
            if not isinstance(package, dict) or not isinstance(package.get("package"), dict):
                raise ValueError("invalid OSV package")
            vulnerabilities = package.get("vulnerabilities", [])
            if not isinstance(vulnerabilities, list) or any(not isinstance(v, dict) or not isinstance(v.get("id"), str) for v in vulnerabilities):
                raise ValueError("invalid OSV vulnerabilities")
            found |= bool(vulnerabilities)
    if scanned != lockfiles:
        raise ValueError("OSV did not cover exactly the requested lockfiles")
    return found


def _gitleaks_report(value: object, format_name: str) -> bool:
    if format_name == "sarif":
        results = _sarif(value)
        for finding in results:
            locations = finding.get("locations")
            if not isinstance(locations, list) or not locations:
                raise ValueError("Gitleaks SARIF finding has no locations")
            for location in locations:
                if not isinstance(location, dict):
                    raise ValueError("invalid Gitleaks SARIF location")
                physical = location.get("physicalLocation")
                region = physical.get("region") if isinstance(physical, dict) else None
                snippet_object = region.get("snippet") if isinstance(region, dict) else None
                snippet = snippet_object.get("text") if isinstance(snippet_object, dict) else None
                if snippet != "REDACTED":
                    raise ValueError("Gitleaks SARIF contains an unredacted snippet")
            # Native redaction covers detected secrets, but not commit messages.
            fingerprints = finding.get("partialFingerprints", {})
            if not isinstance(fingerprints, dict):
                raise ValueError("invalid Gitleaks SARIF fingerprints")
            if fingerprints.get("commitMessage"):
                fingerprints["commitMessage"] = "REDACTED"
        return bool(results)
    if not isinstance(value, list):
        raise ValueError("invalid Gitleaks JSON report")
    for finding in value:
        if not isinstance(finding, dict) or not isinstance(finding.get("RuleID"), str) or not isinstance(finding.get("File"), str):
            raise ValueError("invalid Gitleaks finding")
        if finding.get("Secret") != "REDACTED":
            raise ValueError("Gitleaks JSON contains an unredacted secret")
        # Adjacent source text can contain other secrets, so omit it wholesale.
        for key in ("Match", "Message", "Fragment"):
            if finding.get(key):
                finding[key] = None if key == "Fragment" else "REDACTED"
    return bool(value)


def _load_report(path: Path) -> object:
    if not path.is_file() or path.is_symlink() or path.is_junction():
        raise ValueError("scanner report is missing or is not a regular file")
    with path.open("rb") as stream:
        payload = stream.read(REPORT_LIMIT + 1)
    if len(payload) > REPORT_LIMIT:
        raise ValueError("scanner report exceeds size limit")
    return _load_json(payload, "scanner report")


def _report_run(binary: Path, args: list[str], destination: Path, format_name: str, *, root: Path, env: dict[str, str], validate, present=None) -> int:
    if destination.is_symlink():
        raise ValueError("report destination must not be a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A failed scan must never leave yesterday's successful report at this name.
    destination.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix=".scanner-", dir=destination.parent) as directory:
        report = Path(directory) / destination.name
        arguments = [str(report) if value == "{report}" else value for value in args]
        try:
            result = run_command_bytes(binary, arguments, cwd=root, env=env, input=b"", timeout=600, check=False)
        except subprocess.TimeoutExpired:
            # Scanner output may contain raw metadata. Never echo captured logs.
            print(f"{binary.name}: scan timed out; no report published", file=sys.stderr)
            return 124
        code = result.returncode if result.returncode >= 0 else 128 - result.returncode
        try:
            value = _load_report(report)
            findings = validate(value, format_name)
        except OSError, ValueError, TypeError, KeyError:
            print(f"{binary.name}: missing, malformed, incomplete, or unredacted {format_name} report (exit {code}); no report published", file=sys.stderr)
            return code or 1
        # Keep the native schema and diagnostics; Gitleaks metadata redactions
        # above are the only changes to finding content.
        files.replace_many({destination: (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")})
        status = code or int(findings)
        if present is not None:
            present(value, format_name, destination, status, findings)
        return status


def _clear_numbered_reports(directory: Path, *, prefix: str = "") -> None:
    """Unlink this scanner's old reports, including links, without following them."""
    for component in (*reversed(directory.parents), directory):
        if component.is_symlink() or component.is_junction():
            raise ValueError(f"report directory must not contain symlinks or junctions: {component}")
    directory.mkdir(parents=True, exist_ok=True)
    for report in directory.iterdir():
        if re.fullmatch(re.escape(prefix) + r"[0-9]+\.(?:json|sarif)", report.name):
            report.unlink(missing_ok=True)


def scan_osv(settings: config.Config, lockfiles: tuple[str, ...], *, output: str = "target/security", configuration: str | None = None) -> int:
    """Audit explicit uv.lock/Cargo.lock inputs; no dependency call analysis.

    Report each lock separately to bound command length on Windows. Advisory
    queries require network access. Return the first nonzero scanner status;
    findings, missing inputs and invalid coverage also block.
    """
    if not lockfiles or len(set(lockfiles)) != len(lockfiles):
        raise ValueError("select at least one unique lockfile")
    inventory = set(security_inventory(settings.root))
    if any(name not in inventory or Path(name).name not in {"uv.lock", "Cargo.lock"} for name in lockfiles):
        raise ValueError("OSV inputs must be existing tracked/nonignored uv.lock or Cargo.lock files")
    binary, env = _binary(settings, "osv-scanner")
    _clear_numbered_reports(settings.path(output), prefix="osv-")
    code = 0
    for index, name in enumerate(sorted(lockfiles)):
        path = (settings.root / name).resolve()
        present = FindingOutput("OSV", "dependencies", settings.root)
        for format_name in ("json", "sarif"):
            args = [
                "scan",
                "source",
                "--no-call-analysis=go",
                "--no-call-analysis=rust",
                "--all-packages",
                "--format",
                format_name,
                "--output-file",
                "{report}",
                "--lockfile",
                ":" + str(path),
            ]
            if configuration:
                args += ["--config", str(settings.path(configuration))]
            status = _report_run(
                binary,
                args,
                settings.path(output) / f"osv-{index}.{format_name}",
                format_name,
                root=settings.root,
                env=env,
                validate=lambda value, fmt: _osv_report(value, {path}) if fmt == "json" else bool(_sarif(value)),
                present=present,
            )
            code = code or status
    return code


def _prepare_gitleaks_scan(settings: config.Config, stage: Path, exclude: tuple[str, ...]) -> tuple[Path, dict[str, str]]:
    shallow = run_git_bytes(["--no-pager", "rev-parse", "--is-shallow-repository"], cwd=settings.root).stdout.strip()
    if shallow != b"false":
        raise ValueError("full-history secret scanning requires a non-shallow checkout (fetch-depth: 0)")
    # Also reject an unborn or inaccessible history.
    run_git_bytes(["--no-pager", "rev-parse", "--verify", "HEAD"], cwd=settings.root)
    names = security_inventory(settings.root, exclude=exclude)
    if not names:
        raise ValueError("no working files selected for secret scanning")
    binary, env = _binary(settings, "gitleaks")
    snapshot = stage / "working"
    snapshot.mkdir()
    for name in names:
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(settings.root / name, destination)
    # Explicit defaults avoid ambient config and .gitleaksignore bypasses.
    (stage / "default.toml").write_bytes(b"[extend]\nuseDefault = true\n")
    (stage / "empty-ignore").write_bytes(b"")
    return binary, {key: value for key, value in env.items() if not key.startswith("GITLEAKS_")}


def _secret_config(settings: config.Config, stage: Path, configuration: str | None) -> Path:
    selected = settings.path(configuration) if configuration is not None else settings.root / ".gitleaks.toml"
    if selected.is_file():
        return selected
    if configuration is not None:
        raise ValueError("requested Gitleaks configuration is missing")
    return stage / "default.toml"


def _gitleaks_scan_targets(root: Path, stage: Path) -> tuple[tuple[str, str, Path], ...]:
    return (("history", "git", root), ("working", "dir", stage / "working"))


def _gitleaks_args(mode: str, source: Path, stage: Path, selected: Path, format_name: str) -> list[str]:
    args = [
        mode,
        str(source),
        "--config",
        str(selected),
        "--redact=100",
        "--no-banner",
        "--no-color",
        "--ignore-gitleaks-allow",
        "--gitleaks-ignore-path",
        str(stage / "empty-ignore"),
        "--timeout=300",
        "--report-format",
        format_name,
        "--report-path",
        "{report}",
    ]
    if mode == "git":
        args.append("--log-opts=--all --full-history")
    return args


def scan_secrets(settings: config.Config, *, output: str = "target/security", configuration: str | None = None, exclude: tuple[str, ...] = ()) -> int:
    """Scan full reachable history and a private snapshot of the working inventory.

    Links/submodules in selected working paths fail rather than silently lose
    coverage. History follows native Gitleaks patch semantics, not submodule
    contents, ignored files, binary blobs, archives or unreachable Git objects.
    """
    code = 0
    with tempfile.TemporaryDirectory(prefix="research-secret-scan-") as directory:
        stage = Path(directory)
        binary, env = _prepare_gitleaks_scan(settings, stage, exclude)
        selected = _secret_config(settings, stage, configuration)
        for label, mode, source in _gitleaks_scan_targets(settings.root, stage):
            present = FindingOutput("Gitleaks", "history" if label == "history" else "working tree", settings.root, snapshot=stage / "working")
            for format_name in ("json", "sarif"):
                status = _report_run(
                    binary,
                    _gitleaks_args(mode, source, stage, selected, format_name),
                    settings.path(output) / f"gitleaks-{label}.{format_name}",
                    format_name,
                    root=settings.root,
                    env=env,
                    validate=_gitleaks_report,
                    present=present,
                )
                code = code or status
    return code


@dataclass(frozen=True, order=True, slots=True)
class _SecretFinding:
    scope: str
    path: str
    position: tuple[int, int, int, int]
    rule: str
    commit: str


def _secret_path(path: str, scope: str, source: Path) -> str:
    native = Path(path)
    if native.is_relative_to(source):
        return native.relative_to(source).as_posix()
    if scope == "working":
        # Saved reports can originate from a deleted snapshot on another host.
        portable = PurePosixPath(path) if path.startswith("/") else PureWindowsPath(path)
        if portable.is_absolute():
            parts = portable.parts
            for index, part in enumerate(parts[:-2]):
                if part.startswith("research-secret-scan-") and parts[index + 1] == "working":
                    return PurePosixPath(*parts[index + 2 :]).as_posix()
    return native.as_posix()


def _secret_findings(report: Path, scope: str, source: Path) -> Counter[_SecretFinding]:
    value = _load_report(report)
    _gitleaks_report(value, "json")
    findings: Counter[_SecretFinding] = Counter()
    for item in cast(list[dict[str, Any]], value):
        path, rule, commit = item["File"], item["RuleID"], item.get("Commit", "")
        position = tuple(item.get(field) for field in ("StartLine", "StartColumn", "EndLine", "EndColumn"))
        if (
            not path
            or not rule
            or not isinstance(commit, str)
            or (scope == "history" and not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit))
            or (scope == "working" and commit != "")
            or any(type(part) is not int or part < 0 for part in position)
        ):
            raise ValueError(f"invalid Gitleaks {scope} finding identity")
        findings[_SecretFinding(scope, _secret_path(path, scope, source), cast(tuple[int, int, int, int], position), rule, commit)] += 1
    return findings


def _print_secret_findings(label: str, findings: Counter[_SecretFinding]) -> None:
    _print(f"{label}: {findings.total()} finding(s)")
    for finding, count in sorted(findings.items()):
        line, column, end_line, end_column = finding.position
        commit = f" at commit {finding.commit}" if finding.commit else ""
        instances = f" ({count} matches)" if count > 1 else ""
        _print(f"  {finding.scope} {_text(finding.path)}:{line}:{column}-{end_line}:{end_column} {_text(finding.rule)}{commit}{instances}")


def show_secret_findings(settings: config.Config, *, reports: str = "target/security") -> None:
    """Summarize existing redacted Gitleaks history and working JSON reports."""
    directory = settings.path(reports)
    findings = {scope: _secret_findings(directory / f"gitleaks-{scope}.json", scope, settings.root) for scope in ("history", "working")}
    for scope, records in findings.items():
        _print_secret_findings(scope.capitalize(), records)


def check_secret_exceptions(settings: config.Config, candidate: str, *, baseline: str | None = None, exclude: tuple[str, ...] = ()) -> None:
    """Compare two Gitleaks policies using one working snapshot and unchanged Git refs."""
    with tempfile.TemporaryDirectory(prefix="research-secret-compare-") as directory:
        stage = Path(directory)
        binary, env = _prepare_gitleaks_scan(settings, stage, exclude)
        original = _secret_config(settings, stage, baseline)
        proposed = _secret_config(settings, stage, candidate)
        if original.samefile(proposed):
            raise ValueError("baseline and candidate must be separate Gitleaks configuration files")
        policy_snapshots = {path: path.read_bytes() for path in (original, proposed)}

        def history_refs() -> bytes:
            return run_git_bytes(["--no-pager", "show-ref", "--head"], cwd=settings.root).stdout

        refs = history_refs()
        results: list[Counter[_SecretFinding]] = []
        for policy in (original, proposed):
            findings: Counter[_SecretFinding] = Counter()
            for scope, mode, source in _gitleaks_scan_targets(settings.root, stage):
                report = stage / f"gitleaks-{scope}.json"
                args = _gitleaks_args(mode, source, stage, policy, "json")
                args.append(f"--exit-code={GITLEAKS_FINDINGS_EXIT}")
                status = _report_run(binary, args, report, "json", root=settings.root, env=env, validate=_gitleaks_report)
                if status not in (0, GITLEAKS_FINDINGS_EXIT) or not report.is_file():
                    raise ValueError(f"Gitleaks {scope} scan failed during policy comparison (exit {status})")
                observed = _secret_findings(report, scope, source)
                if status != (GITLEAKS_FINDINGS_EXIT if observed else 0):
                    raise ValueError(f"Gitleaks {scope} scan returned an inconsistent findings status")
                findings.update(observed)
            if history_refs() != refs:
                raise ValueError("Git refs changed during Gitleaks policy comparison")
            if any(path.read_bytes() != payload for path, payload in policy_snapshots.items()):
                raise ValueError("Gitleaks configuration changed during policy comparison")
            results.append(findings)
    before, after = results
    _print_secret_findings("Suppressed", before - after)
    _print_secret_findings("Introduced", after - before)
    _print_secret_findings("Remaining", before & after)
