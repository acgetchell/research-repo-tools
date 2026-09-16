"""Fixture-specific Semgrep configuration and fail-closed finding checks."""

import os
import re
import tempfile
from collections import Counter
from pathlib import Path

import yaml

from research_repo_tools.config import Config
from research_repo_tools.process import run_safe_command
from research_repo_tools.semgrep_findings import _actual_findings, _expected_findings, _finding_mismatches, parse_results

ANNOTATION = re.compile(r"(?<![A-Za-z0-9_])(?P<kind>ruleid|ok):\s*(?P<ids>[A-Za-z0-9_.-]+(?:\s*,\s*[A-Za-z0-9_.-]+)*)")


class _RuleLoader(yaml.SafeLoader):
    """Semgrep configuration mappings must not silently overwrite duplicate keys."""

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
    return tuple((match["kind"], name.strip()) for match in ANNOTATION.finditer(text) for name in match["ids"].split(",") if name.strip().startswith(namespace))


def fixtures(path: Path) -> list[Path]:
    if not path.exists():
        raise ValueError(f"Semgrep fixtures not found: {path}")
    candidates = [path] if path.is_file() else path.rglob("*")
    return sorted(
        (item for item in candidates if item.is_file() and not item.name.endswith(".fixed") and ".fixed." not in item.name), key=lambda item: item.as_posix()
    )


def rules(path: Path) -> dict[str, dict]:
    try:
        parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=_RuleLoader)
    except (yaml.YAMLError, UnicodeError, ValueError) as error:
        raise ValueError(f"{path}: invalid Semgrep configuration: {error}") from error
    if not isinstance(parsed, dict) or not isinstance(parsed.get("rules"), list) or not parsed["rules"]:
        raise ValueError(f"{path}: Semgrep config must contain a rules array")
    result: dict[str, dict] = {}
    for rule in parsed["rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"]:
            raise ValueError(f"{path}: every rule requires a nonempty ID")
        if rule["id"] in result:
            raise ValueError(f"{path}: duplicate rule ID: {rule['id']}")
        result[rule["id"]] = rule
    return result


def fixture_rule_ids(path: Path, source: dict[str, dict], namespace: str = "") -> list[str]:
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
    return yaml.safe_dump({"rules": [source[name] for name in fixture_rule_ids(path, source, namespace)]}, sort_keys=False)


def check(config: Config) -> int:
    section = config.section("semgrep")
    if not {"config", "fixtures"} <= section.keys():
        raise ValueError("semgrep.config and semgrep.fixtures must be explicit")
    source = rules(config.path(section["config"]))
    paths = fixtures(config.path(section["fixtures"]))
    cwd = config.path(section.get("cwd", "."))
    counts = {config.path(path).resolve(): values for path, values in section.get("counts", {}).items()}
    missing_paths = counts.keys() - {path.resolve() for path in paths}
    if missing_paths:
        raise ValueError(f"Semgrep count expectations reference missing fixtures: {', '.join(map(str, sorted(missing_paths)))}")
    namespace = section.get("namespace", "")
    positives: set[str] = set()
    selected: list[tuple[Path, list[str], bool]] = []
    for path in paths:
        annotated = annotations(path.read_text(encoding="utf-8"), namespace)
        positives.update(name for kind, name in annotated if kind == "ruleid")
        expected_counts = counts.get(path.resolve(), {})
        if not annotated and not expected_counts:
            raise ValueError(f"fixture has no Semgrep annotations or configured count expectations: {path}")
        if annotated:
            selected.append((path, fixture_rule_ids(path, source, namespace), False))
        if expected_counts:
            names = [name for name in expected_counts if name.startswith(namespace)]
            if set(names) - source.keys():
                raise ValueError(f"{path}: unknown rules in count expectations")
            if names:
                selected.append((path, names, True))
                positives.update(name for name in names if expected_counts[name] > 0)
    missing = {name for name in source if name.startswith(namespace)} - positives
    if missing:
        raise ValueError(f"Semgrep rules without positive fixtures: {', '.join(sorted(missing))}")
    with tempfile.TemporaryDirectory(prefix="research-repo-semgrep-") as directory:
        temporary = Path(directory)
        for index, (path, names, count_mode) in enumerate(selected):
            generated = temporary / str(index) / path.with_suffix(".yaml").name
            generated.parent.mkdir(parents=True)
            generated.write_text(yaml.safe_dump({"rules": [source[name] for name in names]}, sort_keys=False), encoding="utf-8", newline="\n")
            # Semgrep's fixture test mode ignores production path filters.
            # Apply that same rule to the span-aware scan: several production
            # rules intentionally exclude tests/semgrep while testing their
            # match logic there. Keep the original fixture filename/location.
            scan_config = generated.with_name("scan.yaml")
            scan_config.write_text(
                yaml.safe_dump(
                    {"rules": [source[name] if count_mode else {key: value for key, value in source[name].items() if key != "paths"} for name in names]},
                    sort_keys=False,
                ),
                encoding="utf-8",
                newline="\n",
            )
            env = {
                **os.environ,
                "SEMGREP_SEND_METRICS": "off",
                "SEMGREP_SETTINGS_FILE": str(generated.parent / "settings.yml"),
                "SEMGREP_VERSION_CACHE_PATH": str(generated.parent / "version-cache"),
                "SEMGREP_LOG_FILE": str(generated.parent / "semgrep.log"),
                "OTEL_SDK_DISABLED": "true",
            }
            target = str(path.relative_to(cwd)) if path.is_relative_to(cwd) else str(path)
            # Scan original fixture paths so filename/directory rules retain meaning.
            result = run_safe_command(
                "semgrep",
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
                    target,
                ],
                cwd=cwd,
                env=env,
                timeout=section.get("timeout", 300),
            )
            try:
                parsed = parse_results(result.stdout)
            except ValueError as error:
                raise ValueError(f"{path}: {error}") from error
            actual = _actual_findings(parsed)
            if actual is None:
                raise ValueError(f"{path}: malformed Semgrep findings")
            if count_mode:
                found = Counter(name for name, _start, _end in actual)
                mismatches = tuple(
                    f"{name}: expected {counts[path.resolve()][name]} findings, got {found[name]}"
                    for name in names
                    if found[name] != counts[path.resolve()][name]
                )
            else:
                expected = Counter({finding: count for finding, count in _expected_findings(path).items() if finding[0].startswith(namespace)})
                mismatches = _finding_mismatches(expected, actual)
            if mismatches:
                raise ValueError(f"{path}: " + "; ".join(mismatches))
            # Native test mode also verifies autofix output when a companion exists.
            fixed_paths = (Path(str(path) + ".fixed"), path.with_name(f"{path.stem}.fixed{path.suffix}"))
            if not count_mode and any(fixed.exists() for fixed in fixed_paths):
                run_safe_command(
                    "semgrep",
                    ["scan", "--disable-version-check", "--metrics", "off", "--test", "--strict", "--config", str(generated), target],
                    cwd=cwd,
                    env=env,
                    timeout=section.get("timeout", 300),
                )
    return 0
