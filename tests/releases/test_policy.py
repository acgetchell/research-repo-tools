"""Fail closed on ambiguous release selectors and unsafe policy boundaries."""

from dataclasses import replace
from pathlib import Path

import pytest

from research_repo_tools import cli, config
from research_repo_tools.releases import ReleaseAdapter, ReleasePolicy, ReleaseRule, check_release, discover_release, plan_release
from tests.releases.test_contract import snapshot
from tests.releases.test_metadata import _write_project


@pytest.mark.parametrize(
    "rule",
    [
        {"path": "../README.md", "pattern": "(?P<value>.+)", "value": "x"},
        {"path": "C:/README.md", "pattern": "(?P<value>.+)", "value": "x"},
        {"path": "README.md", "pattern": "(?P<value>.+)", "value": "x", "source": "version"},
        {"path": "README.md", "pattern": "(?P<value>.+)"},
        {"path": "README.md", "pattern": "missing capture", "source": "version"},
        {"path": "README.md", "pattern": "(?P<value>.+)", "source": "unknown"},
        {"path": "README.md", "pattern": "(?P<value>.+)", "source": "version", "count": True},
        {"path": "README.md", "pattern": "(?P<value>.+)", "source": "version", "count": 0},
        {"path": "README.md", "pattern": "[", "value": "x"},
        {"path": "README.md", "pattern": "(?P<value>.+)", "value": "x", "exclude": "["},
        {"path": "README.md", "pattern": "(?P<value>.+)", "value": "x", "command": "hook"},
    ],
)
def test_invalid_policy_is_rejected_at_configuration_boundary(tmp_path: Path, rule) -> None:
    with pytest.raises(ValueError):
        config.parse({"release": {"rules": [rule]}}, root=tmp_path)


@pytest.mark.parametrize("value", [{"rules": {}}, {"rules": [False]}, {"required-files": "README.md"}, {"exclude": ["../history/**"]}])
def test_invalid_policy_containers_are_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        config.parse({"release": value}, root=tmp_path)


def test_selected_historical_file_is_not_silently_reactivated():
    with pytest.raises(ValueError, match="excluded historical"):
        ReleasePolicy(exclude=("docs/history/**",), rules=(ReleaseRule("docs/history/old.md", "(?P<value>.+)", source="version"),))


def test_overlapping_and_optional_captures_fail_check_and_preview(tmp_path):
    _write_project(tmp_path)
    rule = ReleaseRule("README.md", r"cargo add consumer@(?P<value>\S+)", source="version")
    for rules, error in [((rule, rule), "overlapping"), ((replace(rule, pattern=r"(?P<value>absent)?cargo add"),), "empty or missing")]:
        policy = ReleasePolicy(rules=rules)
        with pytest.raises(ValueError, match=error):
            check_release(tmp_path, policy=policy)
        with pytest.raises(ValueError, match=error):
            plan_release(tmp_path, "1.2.4", previous_tag="1.2.3", policy=policy)


def test_parent_symlinks_cannot_alias_adapter_or_rule_inputs(tmp_path):
    _write_project(tmp_path)
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "report.json").write_bytes(b"{}")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symbolic link"):
        discover_release(tmp_path, adapter=ReleaseAdapter(("alias/report.json",)))


def test_unexpected_active_versions_fail_preview_and_apply_equally(tmp_path, capsys):
    _write_project(tmp_path)
    (tmp_path / "selected.md").write_text("target v9.9.9\n")
    config_path = tmp_path / "release.toml"
    config_path.write_text("[[release.rules]]\npath='selected.md'\npattern='target (?P<value>v[^\\s]+)'\nsource='tag'\n")
    before = snapshot(tmp_path)
    command = ["--root", str(tmp_path), "--config", str(config_path), "release", "update", "1.2.4", "--previous-release", "v1.2.3"]
    errors = []
    for extra in (["--dry-run"], []):
        assert cli.main([*command, *extra]) == 1
        errors.append(capsys.readouterr().err)
        assert snapshot(tmp_path) == before
    assert errors[0] == errors[1]
    assert "unexpected active release version" in errors[0]


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_explicit_revision_selector_can_promote_main_without_touching_old_evidence(tmp_path, newline):
    _write_project(tmp_path)
    (tmp_path / "active.md").write_bytes(b"source main" + newline + b"measured v0.1.0" + newline)
    policy = ReleasePolicy(rules=(ReleaseRule("active.md", r"source (?P<value>main|[0-9a-f]{7,40}|v\d+\.\d+\.\d+)", source="tag"),))
    plan = plan_release(tmp_path, "1.2.4", previous_tag="1.2.3", policy=policy)
    assert next(edit.after for edit in plan.edits if edit.path == Path("active.md")) == b"source v1.2.4" + newline + b"measured v0.1.0" + newline
