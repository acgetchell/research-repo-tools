"""Configuration parsing and source editing preserve values and unrelated text."""

import operator
import tomllib
from dataclasses import FrozenInstanceError, replace

import pytest

from research_repo_tools import config, toml_source


def test_parsed_settings_detach_nested_inputs_and_reject_mutation(tmp_path):
    tools = {"uv_version": "uv"}
    cargo = {"git-cliff": "2.14.1"}
    counts = {"shared.rule": 0}
    projects = ["tooling/scanner"]
    raw = {"deps": {"tools": tools, "python-projects": projects}, "toolchain": {"cargo": cargo}, "semgrep": {"counts": {"fixture.py": counts}}}
    settings = config.parse(raw, root=tmp_path)
    fixture = (tmp_path / "fixture.py").resolve()
    tools.clear()
    projects.clear()
    cargo["git-cliff"] = "invalid"
    counts["shared.rule"] = -1
    assert settings.deps.tools == {"uv_version": "uv"}
    assert settings.deps.python_projects == ("tooling/scanner",)
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
        ({"deps": {"python-projects": "tooling/scanner"}}, "deps.python-projects"),
        ({"deps": {"python-projects": [42]}}, "deps.python-projects"),
        ({"deps": {"tools": {"uv": False}}}, "deps.tools"),
        ({"deps": {"tools": {"format_pin": "dprint"}}, "toolchain": {"binaries": {"dprint": "0.60.1"}}}, "ownership is ambiguous"),
        ({"deps": {"tools": {"format_pin": "dprint"}, "tool-owners": {"dprint": "path"}}}, "supports cargo and homebrew"),
        ({"deps": {"tools": {"format_pin": "dprint"}, "tool-owners": {"other": "cargo"}}}, "must name mapped tools"),
        ({"deps": {"tools": {"just_pin": "just"}, "tool-owners": {"just": "homebrew"}}}, "rust-just"),
        ({"semgrep": {"timeout": True}}, "timeout"),
        ({"semgrep": {"timeout": 0}}, "timeout"),
        ({"semgrep": {"counts": {"fixture.py": {}}}}, "rule counts"),
        ({"semgrep": {"counts": {"fixture.py": {"rule": True}}}}, "nonnegative integer"),
        ({"semgrep": {"counts": {"fixture.py": {"rule": -1}}}}, "nonnegative integer"),
        ({"release": {"date-policy": "invalid"}}, "date-policy"),
        ({"release": {"final-changelog": "false"}}, "boolean"),
        ({"changelog": {"dependency-bodies": "deps-dev"}}, "concise or preserve"),
        ({"changelog": {"cliff-config": "cliff.toml", "dependency-bodies": "preserve"}}, "remove cliff-config"),
        ({"publishing": {}}, "requires registry"),
        ({"publishing": {"unknown": True}}, "unknown"),
    ],
)
def test_invalid_values_are_rejected_by_the_config_parser(tmp_path, raw, diagnostic):
    with pytest.raises(ValueError, match=diagnostic):
        config.parse(raw, root=tmp_path)


@pytest.mark.parametrize("separator", ["\x85", "\u2028", "\u2029"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
@pytest.mark.parametrize("operation", ["string", "value", "array", "insert"])
def test_edits_use_physical_lines_and_leave_decoy_declarations_unchanged(separator, newline, final_newline, operation):
    declaration = 'version = "1.2.3"' if operation != "array" else 'versions = ["1.2.3", "keep"]'
    preamble = f'description = """a{separator}b{separator}c{separator}d\n{declaration}\n"""\n'
    original = (preamble + "[project]\n" + declaration + ("\n" if final_newline else "")).replace("\n", newline)
    assert toml_source.key_line(original, "project", "versions" if operation == "array" else "version") == 5
    if operation == "string":
        result = toml_source.replace_string(original, "project", "version", "2.0.0")
        changed = 'version = "2.0.0"'
    elif operation == "value":
        result = toml_source.set_value(original, "project", "version", '"2.0.0"')
        changed = 'version = "2.0.0"'
    elif operation == "array":
        result = toml_source.replace_array_strings(original, "project", "versions", {"1.2.3": "2.0.0"})
        changed = 'versions = ["2.0.0", "keep"]'
    else:
        result = toml_source.set_value(original, "project", "extra", "42")
        changed = "extra = 42\n" + declaration
    expected = (preamble + "[project]\n" + changed + ("\n" if final_newline else "")).replace("\n", newline)
    assert result == expected
    assert tomllib.loads(result)["description"] == tomllib.loads(original)["description"]
