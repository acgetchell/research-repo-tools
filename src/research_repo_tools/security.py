"""Native scanner gates with explicit inventory and fresh, validated reports."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast

import tomli_w

from research_repo_tools import config, files, toolchain, toolchain_config
from research_repo_tools.evidence import _load_json
from research_repo_tools.process import run_command_bytes, run_git_bytes
from research_repo_tools.scanner_output import FindingOutput, _print, _text
from research_repo_tools.selection import select_files

__all__ = ["check_secret_exceptions", "scan_osv", "scan_secrets", "security_inventory", "show_secret_findings"]

GENERATED = tuple(f"**/{name}/**" for name in (".venv", "venv", ".tox", ".nox", "__pycache__", "target", "dist", "build", "node_modules"))
REPORT_LIMIT = 128 * 1024 * 1024
GITLEAKS_FINDINGS_EXIT = 3
GITLEAKS_TIMEOUT = 300
_GIT_REPOSITORY_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)
# The pinned Gitleaks follows at most two local extensions.
GITLEAKS_EXTEND_DEPTH = 2


def security_inventory(root: Path, *, include: tuple[str, ...] = (), exclude: tuple[str, ...] = (), env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Tracked and nonignored files; reject selected links and submodules.

    Standard environment/build directories are excluded even if tracked.
    Consumers supply additional exclusions; ignored untracked files stay out.
    """
    return select_files(root, include=include, exclude=(*GENERATED, *exclude), env=env)


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


def _redact_gitleaks_report(value: object, format_name: str, *, scope: str) -> tuple[object, bool]:
    """Validate the private scan result, redact its metadata in place, and return the report and whether it has findings."""
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
        return value, bool(results)
    for record, _ in _gitleaks_records(value, scope):
        # Native redaction covers the matched secret. Adjacent text can contain others.
        for key in ("Match", "Message", "Fragment"):
            if record.get(key):
                record[key] = None if key == "Fragment" else "REDACTED"
    return value, bool(value)


def _load_report(path: Path) -> object:
    if not path.is_file() or path.is_symlink() or path.is_junction():
        raise ValueError(f"scanner report is missing or is not a regular file: {path}")
    with path.open("rb") as stream:
        payload = stream.read(REPORT_LIMIT + 1)
    if len(payload) > REPORT_LIMIT:
        raise ValueError(f"scanner report exceeds size limit: {path}")
    try:
        return _load_json(payload, "scanner report")
    except ValueError:
        raise ValueError(f"invalid JSON in scanner report {path}") from None


def _report_run(
    binary: Path,
    args: list[str],
    destination: Path,
    format_name: str,
    *,
    root: Path,
    env: dict[str, str],
    prepare_report: Callable[[object, str], tuple[object, bool]],
    present=None,
    findings_exit: int | None = None,
    timeout: int = 600,
) -> int:
    """Remove stale output, then publish the prepared report after checking the scanner exit status."""
    if destination.is_symlink():
        raise ValueError("report destination must not be a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A failed scan must never leave yesterday's successful report at this name.
    destination.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix=".scanner-", dir=destination.parent) as directory:
        report = Path(directory) / destination.name
        arguments = [str(report) if value == "{report}" else value for value in args]
        try:
            result = run_command_bytes(binary, arguments, cwd=root, env=env, input=b"", timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            # Scanner output may contain raw metadata. Never echo captured logs.
            print(f"{binary.name}: scan timed out; no report published", file=sys.stderr)
            return 124
        code = result.returncode if result.returncode >= 0 else 128 - result.returncode
        if findings_exit is not None and code not in (0, findings_exit):
            print(f"{binary.name}: scan did not complete (exit {code}); no report published", file=sys.stderr)
            return 2 if code == 1 else code
        try:
            value, findings = prepare_report(_load_report(report), format_name)
            if findings_exit is not None and code != (findings_exit if findings else 0):
                raise ValueError("scanner exit status does not match report findings")
            payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(payload) > REPORT_LIMIT:
                raise ValueError("published scanner report exceeds size limit")
        except OSError, ValueError, TypeError, KeyError:
            print(f"{binary.name}: missing, malformed, incomplete, or unredacted {format_name} report (exit {code}); no report published", file=sys.stderr)
            return 2 if findings_exit is not None else code or 1
        files.replace_many({destination: payload})
        status = code or int(findings)
        if present is not None:
            present(value, format_name, destination, int(findings) if findings_exit is not None else status, findings)
        return status


def _ensure_report_directory(directory: Path) -> None:
    for component in (*reversed(directory.parents), directory):
        if component.is_symlink() or component.is_junction():
            raise ValueError(f"report directory must not contain symlinks or junctions: {component}")
    directory.mkdir(parents=True, exist_ok=True)


def _clear_numbered_reports(directory: Path, *, prefix: str = "") -> None:
    """Unlink this scanner's old reports, including links, without following them."""
    _ensure_report_directory(directory)
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
                prepare_report=lambda value, fmt: (value, _osv_report(value, {path}) if fmt == "json" else bool(_sarif(value))),
                present=present,
            )
            code = code or status
    return code


def _require_no_gitleaks_ignore(root: Path) -> None:
    path = root / ".gitleaksignore"
    # Gitleaks loads this from its target even with an explicit empty ignore file.
    if path.exists(follow_symlinks=False):
        raise ValueError(f"managed Gitleaks scans require {path} to be absent; place reviewed exceptions in .gitleaks.toml")


def _gitleaks_environment(env: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in env.items() if not key.upper().startswith("GITLEAKS_") and key.upper() not in _GIT_REPOSITORY_ENV}


def _prepare_gitleaks_scan(settings: config.Config, stage: Path, exclude: tuple[str, ...]) -> tuple[Path, dict[str, str]]:
    """Stage the working files and explicit scanner defaults, requiring accessible full Git history."""
    _require_no_gitleaks_ignore(settings.root)
    git_env = _gitleaks_environment(os.environ)
    shallow = run_git_bytes(["--no-pager", "rev-parse", "--is-shallow-repository"], cwd=settings.root, env=git_env).stdout.strip()
    if shallow != b"false":
        raise ValueError("full-history secret scanning requires a non-shallow checkout (fetch-depth: 0)")
    # Also reject an unborn or inaccessible history.
    run_git_bytes(["--no-pager", "rev-parse", "--verify", "HEAD"], cwd=settings.root, env=git_env)
    names = security_inventory(settings.root, exclude=exclude, env=git_env)
    if not names:
        raise ValueError("no working files selected for secret scanning")
    binary, env = _binary(settings, "gitleaks")
    snapshot = stage / "working"
    snapshot.mkdir()
    for name in names:
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(settings.root / name, destination)
    _require_no_gitleaks_ignore(snapshot)
    _require_no_gitleaks_ignore(settings.root)
    # Explicit defaults avoid ambient config and .gitleaksignore bypasses.
    (stage / "default.toml").write_bytes(b"[extend]\nuseDefault = true\n")
    (stage / "empty-ignore").write_bytes(b"")
    return binary, _gitleaks_environment(env)


def _secret_config(settings: config.Config, stage: Path, configuration: str | None) -> Path:
    selected = settings.path(configuration) if configuration is not None else settings.root / ".gitleaks.toml"
    if selected.is_file():
        return selected
    if configuration is not None or selected.exists(follow_symlinks=False):
        raise ValueError(f"Gitleaks configuration is missing or is not a regular file: {selected}")
    return stage / "default.toml"


@dataclass
class _GitleaksPolicySnapshot:
    root: Path
    stage: Path
    observed: dict[Path, bytes] = field(default_factory=dict)
    retained: dict[tuple[Path, int], Path] = field(default_factory=dict)

    def retain(self, path: Path, depth: int = 0) -> Path:
        """Copy a scanner policy and its supported local extensions into private storage."""
        key = (path, depth)
        if key in self.retained:
            return self.retained[key]
        if path not in self.observed:
            if not path.is_file():
                raise ValueError(f"Gitleaks policy or local extension is missing or is not a file: {path}")
            self.observed[path] = path.read_bytes()
        payload = self.observed[path]
        try:
            document = tomllib.loads(payload.decode("utf-8"))
        except ValueError:
            raise ValueError(f"Gitleaks policy must be valid UTF-8 TOML: {path}") from None
        if depth < GITLEAKS_EXTEND_DEPTH:
            if any(key.casefold().startswith("extend.") for key in document):
                raise ValueError(f"Gitleaks policy must place extension keys in the [extend] table: {path}")
            extend_key = _gitleaks_field(document, "extend", path)
            extend = document.get(extend_key, {})
            if not isinstance(extend, dict):
                raise ValueError(f"Gitleaks extend must be a table: {path}")
            path_key = _gitleaks_field(extend, "path", path)
            default_key = _gitleaks_field(extend, "usedefault", path)
            extension = extend.get(path_key, "")
            if not isinstance(extension, str):
                raise ValueError(f"Gitleaks extend.path must be a string: {path}")
            if extension:
                if extend.get(default_key) is True:
                    raise ValueError(f"Gitleaks extend.path and extend.useDefault cannot both be set: {path}")
                # Native extension paths resolve from the original invocation
                # directory, even in nested configuration files.
                extend[path_key] = str(self.retain(self.root / extension, depth + 1))
                payload = tomli_w.dumps(document).encode("utf-8")
        output = self.stage / f"policy-{len(self.retained)}.toml"
        output.write_bytes(payload)
        self.retained[key] = output
        return output

    def verify(self) -> None:
        """Check that the original policy files still match their captured bytes."""
        for path, payload in self.observed.items():
            try:
                unchanged = path.is_file() and path.read_bytes() == payload
            except OSError:
                unchanged = False
            if not unchanged:
                raise ValueError(f"Gitleaks configuration changed during policy comparison: {path}")


def _gitleaks_field(table: dict[str, object], field_name: str, path: Path) -> str:
    keys = [key for key in table if key.casefold() == field_name]
    if len(keys) > 1:
        raise ValueError(f"Gitleaks configuration has ambiguous {field_name} keys: {path}")
    return keys[0] if keys else field_name


def _gitleaks_scan_targets(root: Path, stage: Path) -> tuple[tuple[str, str, Path], ...]:
    return (("history", "git", root), ("working", "dir", stage / "working"))


def _gitleaks_args(mode: str, source: Path, stage: Path, selected: Path, format_name: str) -> list[str]:
    args = [
        mode,
        "." if mode == "dir" else str(source),
        "--config",
        str(selected),
        "--redact=100",
        "--no-banner",
        "--no-color",
        "--ignore-gitleaks-allow",
        "--gitleaks-ignore-path",
        str(stage / "empty-ignore"),
        f"--exit-code={GITLEAKS_FINDINGS_EXIT}",
        "--report-format",
        format_name,
        "--report-path",
        "{report}",
    ]
    if mode == "git":
        args.append("--log-opts=--all --full-history --no-renames")
    return args


def scan_secrets(settings: config.Config, *, output: str = "target/security", configuration: str | None = None, exclude: tuple[str, ...] = ()) -> int:
    """Scan full reachable history and a private snapshot of the working inventory.

    Links/submodules in selected working paths fail rather than silently lose
    coverage. History follows native Gitleaks patch semantics, not submodule
    contents, ignored files, binary blobs, archives or unreachable Git objects.
    """
    destination = settings.path(output)
    _ensure_report_directory(destination)
    for label in ("history", "working"):
        for format_name in ("json", "sarif"):
            (destination / f"gitleaks-{label}.{format_name}").unlink(missing_ok=True)
    code = 0
    found = False
    with tempfile.TemporaryDirectory(prefix="research-secret-scan-") as directory:
        stage = Path(directory)
        binary, env = _prepare_gitleaks_scan(settings, stage, exclude)
        selected = _GitleaksPolicySnapshot(settings.root, stage).retain(_secret_config(settings, stage, configuration))
        history, _ = _capture_gitleaks_history(settings.root, stage, env)
        for label, mode, source in _gitleaks_scan_targets(history, stage):
            present = FindingOutput("Gitleaks", "history" if label == "history" else "working tree", settings.root, snapshot=stage / "working")
            for format_name in ("json", "sarif"):
                _require_no_gitleaks_ignore(source)
                status = _report_run(
                    binary,
                    _gitleaks_args(mode, source, stage, selected, format_name),
                    destination / f"gitleaks-{label}.{format_name}",
                    format_name,
                    root=source,
                    env=env,
                    prepare_report=partial(_redact_gitleaks_report, scope=label),
                    present=present,
                    findings_exit=GITLEAKS_FINDINGS_EXIT,
                    timeout=GITLEAKS_TIMEOUT,
                )
                _require_no_gitleaks_ignore(source)
                if status == GITLEAKS_FINDINGS_EXIT:
                    found = True
                else:
                    code = code or status
    return code or int(found)


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


def _gitleaks_records(value: object, scope: str) -> Iterator[tuple[dict[str, Any], _SecretFinding]]:
    """Yield each original JSON record and its validated finding identity."""
    if not isinstance(value, list):
        raise ValueError("invalid Gitleaks JSON report")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"invalid Gitleaks {scope} finding")
        if item.get("Secret") != "REDACTED":
            raise ValueError("Gitleaks JSON contains an unredacted secret")
        path, rule, commit = item.get("File"), item.get("RuleID"), item.get("Commit", "")
        position = tuple(item.get(field) for field in ("StartLine", "StartColumn", "EndLine", "EndColumn"))
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(rule, str)
            or not rule
            or not isinstance(commit, str)
            or (scope == "history" and not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit))
            or (scope == "working" and commit != "")
            or any(type(part) is not int or part < 0 for part in position)
        ):
            raise ValueError(f"invalid Gitleaks {scope} finding identity")
        yield item, _SecretFinding(scope, path, cast(tuple[int, int, int, int], position), rule, commit)


def _secret_findings(report: Path, scope: str, source: Path) -> Counter[_SecretFinding]:
    return Counter(
        _SecretFinding(item.scope, _secret_path(item.path, scope, source), item.position, item.rule, item.commit)
        for _, item in _gitleaks_records(_load_report(report), scope)
    )


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


def _history_tips(root: Path, env: Mapping[str, str]) -> frozenset[bytes]:
    # Like git log --all, this includes detached HEADs in linked worktrees.
    tips = frozenset(run_git_bytes(["--no-pager", "rev-list", "--no-walk=unsorted", "--all"], cwd=root, env=env).stdout.splitlines())
    if not tips or any(not re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", tip) for tip in tips):
        raise ValueError("Git returned invalid or empty history tips for Gitleaks policy comparison")
    return tips


def _capture_gitleaks_history(root: Path, stage: Path, env: Mapping[str, str]) -> tuple[Path, Callable[[], None]]:
    """Mirror the scan's Git refs into private storage, including other worktrees' detached tips.

    Return a guard that rejects changes to the source refs or tips during the scan.
    """

    def history_refs(repository: Path) -> bytes:
        try:
            return run_git_bytes(["--no-pager", "show-ref", "--head"], cwd=repository, env=env).stdout
        except subprocess.CalledProcessError as error:
            # A clone with every ref hidden, including HEAD, has an empty inventory.
            if error.returncode == 1 and not error.stderr:
                return b""
            raise

    refs, tips = history_refs(root), _history_tips(root, env)
    history = stage / "history.git"
    # Retain selected refs privately while reusing the source object store.
    run_git_bytes(["--no-pager", "clone", "--mirror", "--shared", "--reject-shallow", "--template=", "--", str(root), str(history)], cwd=root, env=env)
    copied = history_refs(history)
    if copied != refs:
        # Local cloning shares the objects even when upload-pack hides their refs.
        expected, observed = set(refs.splitlines()), set(copied.splitlines())
        if any(not line.endswith(b" HEAD") for line in observed - expected):
            raise ValueError("Git refs changed while capturing history for Gitleaks")
        missing_refs = bytearray()
        for line in sorted(expected - observed):
            oid, ref = line.split(b" ", 1)
            if ref == b"HEAD":
                # Updating a symbolic HEAD and its referent needs separate transactions.
                run_git_bytes(["--no-pager", "update-ref", "--no-deref", "HEAD", oid.decode("ascii")], cwd=history, env=env)
            else:
                missing_refs.extend(b"create " + ref + b" " + oid + b"\n")
        if missing_refs:
            run_git_bytes(["--no-pager", "update-ref", "--stdin"], cwd=history, env=env, input=bytes(missing_refs))
        if history_refs(history) != refs:
            raise ValueError("Git refs changed while capturing history for Gitleaks")
    mirror_tips = _history_tips(history, env)
    if mirror_tips - tips:
        raise ValueError("Git history tips changed while capturing history for Gitleaks")
    # A bare mirror omits detached HEADs from other linked worktrees.
    if missing := tips - mirror_tips:
        commands = b"".join(b"create refs/rrt-history/" + tip + b" " + tip + b"\n" for tip in sorted(missing))
        run_git_bytes(["--no-pager", "update-ref", "--stdin"], cwd=history, env=env, input=commands)
        if _history_tips(history, env) != tips:
            raise ValueError("Git history tips changed while capturing history for Gitleaks")

    def verify() -> None:
        if history_refs(root) != refs or _history_tips(root, env) != tips:
            raise ValueError("Git refs changed during Gitleaks scanning")

    verify()
    return history, verify


def check_secret_exceptions(settings: config.Config, candidate: str, *, baseline: str | None = None, exclude: tuple[str, ...] = ()) -> None:
    """Compare two Gitleaks policies against the same captured files and history."""
    with tempfile.TemporaryDirectory(prefix="research-secret-compare-") as directory:
        stage = Path(directory)
        binary, env = _prepare_gitleaks_scan(settings, stage, exclude)
        original = _secret_config(settings, stage, baseline)
        proposed = _secret_config(settings, stage, candidate)
        if original.samefile(proposed):
            raise ValueError("baseline and candidate must be separate Gitleaks configuration files")
        policy_snapshots = _GitleaksPolicySnapshot(settings.root, stage)
        retained = (policy_snapshots.retain(original), policy_snapshots.retain(proposed))

        history, verify_history = _capture_gitleaks_history(settings.root, stage, env)
        results: list[Counter[_SecretFinding]] = []
        for policy in retained:
            findings: Counter[_SecretFinding] = Counter()
            for scope, mode, source in _gitleaks_scan_targets(history, stage):
                report = stage / f"gitleaks-{scope}.json"
                args = _gitleaks_args(mode, source, stage, policy, "json")
                _require_no_gitleaks_ignore(source)
                status = _report_run(
                    binary,
                    args,
                    report,
                    "json",
                    root=source,
                    env=env,
                    prepare_report=partial(_redact_gitleaks_report, scope=scope),
                    findings_exit=GITLEAKS_FINDINGS_EXIT,
                    timeout=GITLEAKS_TIMEOUT,
                )
                _require_no_gitleaks_ignore(source)
                if status not in (0, GITLEAKS_FINDINGS_EXIT) or not report.is_file():
                    raise ValueError(f"Gitleaks {scope} scan failed during policy comparison (exit {status})")
                findings.update(_secret_findings(report, scope, source))
            _require_no_gitleaks_ignore(settings.root)
            verify_history()
            default_path = settings.root / ".gitleaks.toml"
            if baseline is None and original == stage / "default.toml" and default_path.exists(follow_symlinks=False):
                raise ValueError(f"Gitleaks configuration changed during policy comparison: {default_path}")
            policy_snapshots.verify()
            results.append(findings)
    before, after = results
    _print_secret_findings("Suppressed", before - after)
    _print_secret_findings("Introduced", after - before)
    _print_secret_findings("Matching locations", before & after)
    _print("Review matching rule IDs and locations whenever a detection rule changes.")
