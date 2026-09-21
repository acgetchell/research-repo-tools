"""Semgrep selection, paths, annotations, and subprocess contracts."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from research_repo_tools import cli, config, semgrep


def consumer(tmp_path: Path, content: str = "# ruleid: shared.rule\nbad()\n") -> tuple[config.Config, Path]:
    rule = {"id": "shared.rule", "languages": ["python"], "message": "bad", "severity": "WARNING", "pattern": "bad()", "paths": {"exclude": ["tests/**"]}}
    (tmp_path / "semgrep.yaml").write_text(yaml.safe_dump({"rules": [rule]}), newline="\n")
    fixture = tmp_path / "tests/semgrep/.hidden/named.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(content, newline="\n")
    return (config.parse({"semgrep": {"config": "semgrep.yaml", "fixtures": "tests/semgrep", "namespace": "shared."}}, root=tmp_path), fixture)


def test_generated_configuration_retains_rule_semantics_and_rejects_unknown_annotation(tmp_path: Path) -> None:
    cfg, fixture = consumer(tmp_path)
    selected = yaml.safe_load(semgrep.build_fixture_config(fixture, cfg.root / "semgrep.yaml"))
    assert selected["rules"][0]["paths"] == {"exclude": ["tests/**"]}
    fixture.write_text("# ruleid: unknown\nbad()\n", newline="\n")
    with pytest.raises(ValueError, match="unknown annotated rules"):
        semgrep.build_fixture_config(fixture, cfg.root / "semgrep.yaml")


@pytest.mark.parametrize("payload", ["rules: [", "rules: []\nrules: []\n", "rules:\n- id: duplicate\n  id: replacement\n"])
def test_bad_yaml_produces_path_aware_failure(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(payload, newline="\n")
    with pytest.raises(ValueError, match="rules.yaml: invalid Semgrep configuration"):
        semgrep.rules(path)


def test_span_scan_keeps_original_filename_and_uses_fixture_path_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, fixture = consumer(tmp_path)
    calls = []

    def scan(command: str, arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(arguments)
        assert command == "semgrep"
        assert Path(arguments[-1]) == fixture.relative_to(tmp_path)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["OTEL_SDK_DISABLED"] == "true"
        generated = yaml.safe_load(Path(arguments[arguments.index("--config") + 1]).read_text())
        assert "paths" not in generated["rules"][0]
        assert "--no-rewrite-rule-ids" in arguments
        result = {"results": [{"check_id": "shared.rule", "path": str(fixture), "start": {"line": 2}, "end": {"line": 3}}]}
        return subprocess.CompletedProcess(arguments, 0, json.dumps(result), "")

    monkeypatch.setattr(semgrep, "run_safe_command", scan)
    assert semgrep.check(cfg) == 0
    assert len(calls) == 1
    assert fixture.read_text() == "# ruleid: shared.rule\nbad()\n"


def test_todo_annotations_and_fixed_files_cannot_satisfy_positive_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, fixture = consumer(tmp_path, "# todoruleid: shared.rule\n# ok: shared.rule\ngood()\n")
    fixture.with_suffix(".py.fixed").write_text("# ruleid: shared.rule\nbad()\n", newline="\n")
    assert semgrep.fixtures(fixture.parent) == [fixture]
    monkeypatch.setattr(semgrep, "run_safe_command", lambda *a, **kw: pytest.fail("spawned before validating coverage"))
    with pytest.raises(ValueError, match="without positive fixtures"):
        semgrep.check(cfg)


def test_count_contracts_preserve_production_path_filters_and_zero_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, fixture = consumer(tmp_path, "bad()\n")
    good = fixture.with_name("good.py")
    good.write_text("good()\n", newline="\n")
    cfg = replace(cfg, semgrep=replace(cfg.semgrep, cwd="tests/semgrep", counts={fixture: {"shared.rule": 1}, good: {"shared.rule": 0}}))

    def scan(command: str, arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
        assert kwargs["cwd"] == tmp_path / "tests/semgrep"
        generated = yaml.safe_load(Path(arguments[arguments.index("--config") + 1]).read_text())
        assert generated["rules"][0]["paths"] == {"exclude": ["tests/**"]}
        results = [] if arguments[-1].endswith("good.py") else [{"check_id": "shared.rule", "path": arguments[-1], "start": {"line": 1}, "end": {"line": 1}}]
        return subprocess.CompletedProcess(arguments, 0, json.dumps({"results": results}), "")

    monkeypatch.setattr(semgrep, "run_safe_command", scan)
    assert semgrep.check(cfg) == 0


def test_namespace_filter_does_not_require_foreign_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, fixture = consumer(tmp_path, "# ruleid: shared.rule, foreign.rule\nbad()\n")
    monkeypatch.setattr(
        semgrep,
        "run_safe_command",
        lambda command, arguments, **kwargs: subprocess.CompletedProcess(
            arguments, 0, json.dumps({"results": [{"check_id": "shared.rule", "path": str(fixture), "start": {"line": 2}, "end": {"line": 2}}]}), ""
        ),
    )
    assert semgrep.check(cfg) == 0


@pytest.mark.parametrize(
    "result",
    [
        {"results": [], "errors": [{"type": "ParseError"}]},
        {"results": [], "errors": {}},
        {"results": [], "errors": None},
        {"results": [1]},
        {"results": [{"check_id": "shared.rule", "start": {"line": True}, "end": {"line": 2}}]},
    ],
)
def test_partial_or_malformed_scan_cannot_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: dict) -> None:
    cfg, _fixture = consumer(tmp_path)
    monkeypatch.setattr(semgrep, "run_safe_command", lambda command, arguments, **kwargs: subprocess.CompletedProcess(arguments, 0, json.dumps(result), ""))
    with pytest.raises(ValueError, match="malformed"):
        semgrep.check(cfg)


def test_empty_namespace_selection_cannot_pass_without_scanning(tmp_path, monkeypatch):
    cfg, fixture = consumer(tmp_path, "bad()\n")
    cfg = replace(cfg, semgrep=replace(cfg.semgrep, namespace="typo.", counts={fixture: {"shared.rule": 1}}))
    monkeypatch.setattr(semgrep, "run_safe_command", lambda *a, **kw: pytest.fail("must reject selection before scanning"))
    with pytest.raises(ValueError, match="no rules.*namespace"):
        semgrep.check(cfg)


def test_count_scan_rejects_unexpected_rules(tmp_path, monkeypatch):
    cfg, fixture = consumer(tmp_path, "bad()\n")
    cfg = replace(cfg, semgrep=replace(cfg.semgrep, counts={fixture: {"shared.rule": 1}}))
    findings = [{"check_id": name, "path": str(fixture), "start": {"line": 1}, "end": {"line": 1}} for name in ("shared.rule", "unexpected.rule")]
    monkeypatch.setattr(semgrep, "run_safe_command", lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps({"results": findings}), ""))
    with pytest.raises(ValueError, match="unexpected.rule"):
        semgrep.check(cfg)


@pytest.mark.parametrize("reported_path", [None, "", "another-fixture.py"])
def test_scan_rejects_findings_without_the_selected_fixture_path(tmp_path, monkeypatch, reported_path):
    cfg, _fixture = consumer(tmp_path)
    finding = {"check_id": "shared.rule", "path": reported_path, "start": {"line": 2}, "end": {"line": 2}}
    monkeypatch.setattr(semgrep, "run_safe_command", lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps({"results": [finding]}), ""))
    with pytest.raises(ValueError, match="finding path"):
        semgrep.check(cfg)


@pytest.mark.parametrize("alias", ["dot", "absolute", "symlink"])
def test_conflicting_fixture_aliases_are_rejected_before_scanning(tmp_path, monkeypatch, capsys, alias):
    _cfg, fixture = consumer(tmp_path, "bad()\nbad()\n")
    relative = fixture.relative_to(tmp_path).as_posix()
    second = f"./{relative}" if alias == "dot" else str(fixture)
    if alias == "symlink":
        link = tmp_path / "alias.py"
        try:
            link.symlink_to(fixture)
        except OSError:
            pytest.skip("symlinks unavailable")
        second = link.name
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        '[tool.research-repo-tools.semgrep]\nconfig="semgrep.yaml"\nfixtures="tests/semgrep"\nnamespace="shared."\n'
        f'[tool.research-repo-tools.semgrep.counts.{json.dumps(relative)}]\n"shared.rule"=1\n'
        f'[tool.research-repo-tools.semgrep.counts.{json.dumps(second)}]\n"shared.rule"=2\n',
        newline="\n",
    )
    original = manifest.read_bytes()
    monkeypatch.setattr(semgrep, "run_safe_command", lambda *a, **kw: pytest.fail("must reject aliases before scanning"))
    assert cli.main(["--root", str(tmp_path), "semgrep", "check-fixtures"]) == 1
    assert "duplicate" in capsys.readouterr().err
    assert manifest.read_bytes() == original
