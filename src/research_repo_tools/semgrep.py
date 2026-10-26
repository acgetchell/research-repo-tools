"""Fixture-specific Semgrep configuration and fail-closed finding checks."""

import re
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast, override

import yaml

from research_repo_tools import semgrep_tool
from research_repo_tools.config import Config
from research_repo_tools.process import run_safe_command
from research_repo_tools.semgrep_docs import FENCE, RustBlock, _documentation, documentation_blocks
from research_repo_tools.semgrep_findings import _expected_findings, _finding_mismatches, parse_results

ANNOTATION = re.compile(r"(?<![A-Za-z0-9_])(?P<kind>ruleid|ok):\s*(?P<ids>[A-Za-z0-9_.-]+(?:\s*,\s*[A-Za-z0-9_.-]+)*)")


class _RuleLoader(yaml.SafeLoader):
    """Semgrep configuration mappings must not silently overwrite duplicate keys."""

    @override
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, bool, int, float)):
                raise ValueError("Semgrep YAML mapping keys must be scalar")
            if key in mapping:
                raise ValueError(f"duplicate Semgrep YAML key: {key!r}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def annotations(text: str, namespace: str = "") -> tuple[tuple[str, str], ...]:
    return tuple(
        (cast(str, match["kind"]), cast(str, name).strip())
        for match in ANNOTATION.finditer(text)
        for name in match["ids"].split(",")
        if name.strip().startswith(namespace)
    )


def fixtures(path: Path) -> list[Path]:
    if not path.exists():
        raise ValueError(f"Semgrep fixtures not found: {path}")
    candidates = [path] if path.is_file() else path.rglob("*")
    return sorted(
        (item for item in candidates if item.is_file() and not item.name.endswith(".fixed") and ".fixed." not in item.name), key=lambda item: item.as_posix()
    )


def rules(path: Path) -> dict[str, dict[str, Any]]:
    try:
        parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=_RuleLoader)
    except (yaml.YAMLError, UnicodeError, ValueError) as error:
        raise ValueError(f"{path}: invalid Semgrep configuration: {error}") from error
    if not isinstance(parsed, dict) or not isinstance(parsed.get("rules"), list) or not parsed["rules"]:
        raise ValueError(f"{path}: Semgrep config must contain a rules array")
    result: dict[str, dict[str, Any]] = {}
    for rule in parsed["rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"]:
            raise ValueError(f"{path}: every rule requires a nonempty ID")
        if rule["id"] in result:
            raise ValueError(f"{path}: duplicate rule ID: {rule['id']}")
        result[rule["id"]] = rule
    return result


def fixture_rule_ids(path: Path, source: dict[str, dict[str, Any]], namespace: str = "") -> list[str]:
    annotated = annotations(path.read_text(encoding="utf-8"), namespace)
    names = list(dict.fromkeys(name for _, name in annotated))
    if not names:
        raise ValueError(f"fixture has no Semgrep annotations in namespace {namespace!r}: {path}")
    missing = set(names) - source.keys()
    if missing:
        raise ValueError(f"{path}: unknown annotated rules: {', '.join(sorted(missing))}")
    return names


def build_fixture_config(path: Path, configuration: Path, *, namespace: str = "") -> str:
    """Build a minimal standalone config without relying on YAML indentation."""
    source = rules(configuration)
    return cast(str, yaml.safe_dump({"rules": [source[name] for name in fixture_rule_ids(path, source, namespace)]}, sort_keys=False))


@dataclass(frozen=True, slots=True)
class FixtureCase:
    source: Path
    names: list[str]
    count_mode: bool = False
    block: RustBlock | None = None
    expected: Counter[tuple[str, int]] | None = None

    @property
    def label(self) -> str:
        if self.block is None:
            return str(self.source)
        return f"{self.source} (Rust block {self.block.index + 1}, lines {self.block.start_line}-{self.block.end_line})"


def _documentation_cases(path: Path, source: dict[str, dict[str, Any]], namespace: str) -> list[FixtureCase]:
    blocks = documentation_blocks(path) if path.suffix in {".rs", ".md"} else ()
    lines = _documentation(path, preserve_source=True)
    cases: dict[int | None, FixtureCase] = {}
    for number, line in enumerate(lines, start=1):
        for kind, name in annotations(line, namespace):
            if name not in source:
                raise ValueError(f"{path}: unknown annotated rule: {name}")
            target = number + 1
            while target <= len(lines) and (not lines[target - 1].strip() or FENCE.match(lines[target - 1])):
                target += 1
            block = (
                next((block for block in blocks if block.start_line <= target <= block.end_line), None) if "rust" in source[name].get("languages", []) else None
            )
            key = None if block is None else block.index
            case = cases.setdefault(key, FixtureCase(path, [], block=block, expected=Counter()))
            if name not in case.names:
                case.names.append(name)
            if kind == "ruleid":
                assert case.expected is not None
                case.expected[name, target] += 1
    for block in blocks:
        if block.index not in cases:
            raise ValueError(f"{path} (Rust block {block.index + 1}): fixture block requires Rust-rule annotations")
    if path.suffix == ".rs" and blocks:
        names = list(dict.fromkeys(name for case in cases.values() if case.block is not None for name in case.names))
        native = cases.setdefault(None, FixtureCase(path, [], expected=Counter()))
        native.names.extend(name for name in names if name not in native.names)
    return list(cases.values())


def check(config: Config, *, rust_docs: bool = False) -> int:
    section = config.semgrep
    if rust_docs and section.counts:
        raise ValueError("documentation fixtures use source annotations; keep count-based fixtures in a separate fixture gate")
    if section.config is None or section.fixtures is None:
        raise ValueError("semgrep.config and semgrep.fixtures must be explicit")
    source = rules(config.path(section.config))
    paths = fixtures(config.path(section.fixtures))
    cwd = config.path(section.cwd)
    counts = section.counts
    missing_paths = counts.keys() - {path.resolve() for path in paths}
    if missing_paths:
        raise ValueError(f"Semgrep count expectations reference missing fixtures: {', '.join(map(str, sorted(missing_paths)))}")
    namespace = section.namespace
    selected_rules = {name for name in source if name.startswith(namespace)}
    if not selected_rules:
        raise ValueError(f"Semgrep configuration has no rules in namespace {namespace!r}")
    positives: set[str] = set()
    selected: list[FixtureCase] = []
    for path in paths:
        if rust_docs and path.is_symlink():
            raise ValueError(f"fixture symlinks are unsupported: {path}")
        annotated = annotations(path.read_text(encoding="utf-8"), namespace)
        positives.update(name for kind, name in annotated if kind == "ruleid")
        expected_counts = counts.get(path.resolve(), {})
        if not annotated and not expected_counts:
            raise ValueError(f"fixture has no Semgrep annotations or configured count expectations: {path}")
        if annotated:
            if rust_docs:
                selected.extend(_documentation_cases(path, source, namespace))
            else:
                selected.append(FixtureCase(path, fixture_rule_ids(path, source, namespace)))
        if expected_counts:
            names = [name for name in expected_counts if name.startswith(namespace)]
            if set(names) - source.keys():
                raise ValueError(f"{path}: unknown rules in count expectations")
            if names:
                selected.append(FixtureCase(path, names, count_mode=True))
                positives.update(name for name in names if expected_counts[name] > 0)
    missing = selected_rules - positives
    if missing:
        raise ValueError(f"Semgrep rules without positive fixtures: {', '.join(sorted(missing))}")
    binary = str(semgrep_tool.setup(config.root))
    with tempfile.TemporaryDirectory(prefix="research-repo-semgrep-") as directory:
        temporary = Path(directory)
        for index, case in enumerate(selected):
            path, names, count_mode = case.source, case.names, case.count_mode
            if case.block is not None:
                path = temporary / "blocks" / str(index) / path.name
                path.parent.mkdir(parents=True)
                path.write_text(case.block.code, encoding="utf-8", newline="\n")
            generated = temporary / str(index) / path.with_suffix(".yaml").name
            generated.parent.mkdir(parents=True)
            case_rules = [{**source[name], "languages": ["rust"]} if case.block is not None else source[name] for name in names]
            generated.write_text(yaml.safe_dump({"rules": case_rules}, sort_keys=False), encoding="utf-8", newline="\n")
            # Semgrep's fixture test mode ignores production path filters.
            # Apply that same rule to the span-aware scan: several production
            # rules intentionally exclude tests/semgrep while testing their
            # match logic there. Keep the original fixture filename/location.
            scan_config = generated.with_name("scan.yaml")
            scan_config.write_text(
                yaml.safe_dump(
                    {"rules": [rule if count_mode else {key: value for key, value in rule.items() if key != "paths"} for rule in case_rules]},
                    sort_keys=False,
                ),
                encoding="utf-8",
                newline="\n",
            )
            env = {
                **semgrep_tool._environment(),
                "SEMGREP_SEND_METRICS": "off",
                "SEMGREP_SETTINGS_FILE": str(generated.parent / "settings.yml"),
                "SEMGREP_VERSION_CACHE_PATH": str(generated.parent / "version-cache"),
                "SEMGREP_LOG_FILE": str(generated.parent / "semgrep.log"),
                "OTEL_SDK_DISABLED": "true",
            }
            target = str(path.relative_to(cwd)) if path.is_relative_to(cwd) else str(path)
            # Scan original fixture paths so filename/directory rules retain meaning.
            result = run_safe_command(
                binary,
                [
                    "scan",
                    "--disable-version-check",
                    "--metrics",
                    "off",
                    "--json",
                    "--quiet",
                    "--strict",
                    "--no-rewrite-rule-ids",
                    "--config",
                    str(scan_config),
                    *(["--scan-unknown-extensions"] if case.block is not None else []),
                    target,
                ],
                cwd=cwd,
                env=env,
                timeout=section.timeout,
            )
            try:
                parsed = parse_results(result.stdout)
            except ValueError as error:
                raise ValueError(f"{case.label}: {error}") from error
            actual = parsed.results
            if case.block is not None:
                offset = case.block.start_line - 1
                actual = tuple(replace(finding, start_line=finding.start_line + offset, end_line=finding.end_line + offset) for finding in actual)
            if rust_docs and case.block is None:
                owned = [other for other in selected if other.source == case.source and other.block is not None]
                actual = tuple(
                    finding
                    for finding in actual
                    if not any(
                        finding.check_id in other.names
                        and other.block is not None
                        and other.block.start_line <= finding.start_line <= finding.end_line <= other.block.end_line
                        for other in owned
                    )
                )
            for finding in parsed.results:
                reported_path = finding.path
                if (cwd / reported_path).resolve() != path.resolve():
                    raise ValueError(f"{case.label}: Semgrep finding path does not identify the selected fixture: {reported_path!r}")
            unexpected = {finding.check_id for finding in actual} - set(names)
            if unexpected:
                raise ValueError(f"{case.label}: unexpected Semgrep rules: {', '.join(sorted(unexpected))}")
            if count_mode:
                found = Counter(finding.check_id for finding in actual)
                mismatches = tuple(
                    f"{name}: expected {counts[path.resolve()][name]} findings, got {found[name]}"
                    for name in names
                    if found[name] != counts[path.resolve()][name]
                )
            else:
                expected = (
                    case.expected
                    if case.expected is not None
                    else Counter({finding: count for finding, count in _expected_findings(path).items() if finding[0].startswith(namespace)})
                )
                mismatches = _finding_mismatches(expected, actual)
            if mismatches:
                raise ValueError(f"{case.label}: " + "; ".join(mismatches))
            # Native test mode also verifies autofix output when a companion exists.
            fixed_paths = (Path(str(path) + ".fixed"), path.with_name(f"{path.stem}.fixed{path.suffix}"))
            if not count_mode and any(fixed.exists() for fixed in fixed_paths):
                run_safe_command(
                    binary,
                    ["scan", "--disable-version-check", "--metrics", "off", "--test", "--strict", "--config", str(generated), target],
                    cwd=cwd,
                    env=env,
                    timeout=section.timeout,
                )
    return 0
