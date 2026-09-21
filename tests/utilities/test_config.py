"""Configuration parsing retains typed values without mutable aliases."""

import operator
from dataclasses import FrozenInstanceError, replace

import pytest

from research_repo_tools import config


def test_parsed_settings_detach_nested_inputs_and_reject_mutation(tmp_path):
    tools = {"uv_version": "uv"}
    cargo = {"git-cliff": "2.14.1"}
    counts = {"shared.rule": 0}
    raw = {"deps": {"tools": tools}, "toolchain": {"cargo": cargo}, "semgrep": {"counts": {"fixture.py": counts}}}
    settings = config.parse(raw, root=tmp_path)
    fixture = (tmp_path / "fixture.py").resolve()
    tools.clear()
    cargo["git-cliff"] = "invalid"
    counts["shared.rule"] = -1
    assert settings.deps.tools == {"uv_version": "uv"}
    assert settings.toolchain.cargo == {"git-cliff": "2.14.1"}
    assert settings.semgrep.counts == {fixture: {"shared.rule": 0}}
    for mapping, key, value in (
        (settings.deps.tools, "uv_version", "invalid"),
        (settings.toolchain.cargo, "git-cliff", "invalid"),
        (settings.semgrep.counts, fixture, {}),
        (settings.semgrep.counts[fixture], "shared.rule", -1),
    ):
        with pytest.raises(TypeError):
            # Deliberately violate the read-only type to verify runtime protection too.
            operator.setitem(mapping, key, value)  # ty: ignore[no-matching-overload]
    with pytest.raises(FrozenInstanceError):
        setattr(settings.release, "date_policy", "invalid")


def test_loaded_settings_preserve_defaults_and_explicit_values(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'schema=1\n[release]\ndate-policy="declared"\nfinal-changelog=true\n'
        '[semgrep]\ntimeout=1\nconfig="rules.yaml"\nfixtures="tests"\n[changelog]\nowner="example"\nrepository="consumer"\n',
        newline="\n",
    )
    settings = config.load(path)
    assert settings.root == tmp_path.resolve()
    assert settings.release == config.ReleasePolicy(date_policy="declared", final_changelog=True)
    assert settings.semgrep.timeout == 1 and settings.semgrep.namespace == ""
    assert settings.semgrep.config == "rules.yaml" and settings.semgrep.fixtures == "tests"
    assert settings.changelog == config.ChangelogSettings(owner="example", repository="consumer")
    assert settings.deps == config.DependencySettings()
    updated = replace(settings, release=replace(settings.release, final_changelog=False))
    assert not updated.release.final_changelog and settings.release.final_changelog


@pytest.mark.parametrize(
    "raw,diagnostic",
    [
        ([], "table"),
        ({"schema": True}, "schema"),
        ({"unknown": {}}, "unknown"),
        ({"semgrep": []}, "semgrep"),
        ({"deps": {"unknown": "value"}}, "unknown fields"),
        ({"deps": {"uv": 42}}, "deps.uv"),
        ({"deps": {"tools": {"uv": False}}}, "deps.tools"),
        ({"semgrep": {"timeout": True}}, "timeout"),
        ({"semgrep": {"timeout": 0}}, "timeout"),
        ({"semgrep": {"counts": {"fixture.py": {}}}}, "rule counts"),
        ({"semgrep": {"counts": {"fixture.py": {"rule": True}}}}, "nonnegative integer"),
        ({"semgrep": {"counts": {"fixture.py": {"rule": -1}}}}, "nonnegative integer"),
        ({"release": {"date-policy": "invalid"}}, "date-policy"),
        ({"release": {"final-changelog": "false"}}, "boolean"),
    ],
)
def test_invalid_values_are_rejected_by_the_config_parser(tmp_path, raw, diagnostic):
    with pytest.raises(ValueError, match=diagnostic):
        config.parse(raw, root=tmp_path)
