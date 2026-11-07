"""Policy rejection and universal lock downgrade boundaries."""

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from research_repo_tools import dependencies, python_locks, python_resolution


@pytest.mark.parametrize(
    "source",
    [
        '{path="../library"}',
        '{git="https://example.invalid/lib"}',
        '{git="https://example.invalid/lib", rev="main"}',
        '{git="https://example.invalid/lib", rev="abcdef0"}',
        '{git="https://example.invalid/lib", rev="' + "A" * 40 + '"}',
        '{git="http://example.invalid/lib", rev="' + "a" * 40 + '"}',
        '{git="https://user@example.invalid/lib", rev="' + "a" * 40 + '"}',
        '{git="https://example.invalid/lib", rev="' + "a" * 40 + '", branch="main"}',
        '{git="https://example.invalid/lib", rev="' + "a" * 40 + '", marker="sys_platform == \'win32\'"}',
        '[{git="https://example.invalid/lib", rev="' + "a" * 40 + '"}]',
        "{workspace=true}",
        '{url="https://example.invalid/lib.whl"}',
        '{index="private", group="dev"}',
    ],
)
def test_unsupported_source_policy_fails_before_uv_or_writes(tmp_path, monkeypatch, source):
    manifest = tmp_path / "pyproject.toml"
    original = (
        '[project]\nname="example"\nversion="1"\nrequires-python=">=3.15"\n[dependency-groups]\ndev=["tool==2"]\n[tool.uv.sources]\ntool=' + source + "\n"
    ).encode()
    manifest.write_bytes(original)
    monkeypatch.setattr(dependencies, "run_safe_command", lambda *args, **kwargs: pytest.fail("uv ran with unsupported source policy"))
    with pytest.raises(ValueError, match="unsupported tool.uv.sources.tool"):
        dependencies.update_dev_pins(manifest)
    constraints, pins = dependencies.parse_project(original.decode("utf-8"))
    with pytest.raises(ValueError, match="unsupported tool.uv.sources.tool"):
        dependencies.resolve_latest_pins(pins, constraints, tmp_path)
    assert dependencies.main(["--pyproject", str(manifest)]) == 1
    assert manifest.read_bytes() == original
    assert not (tmp_path / "uv.lock").exists()


@pytest.mark.parametrize("included", [False, True])
def test_exact_git_source_stays_fixed_in_disposable_resolution(tmp_path, monkeypatch, included):
    source = {"git": "https://github.com/example/tool.git", "rev": "a" * 40}
    groups = 'dev=["fixed-tool==1", "tool==2"]' if not included else 'dev=[{include-group="tooling"}, "tool==2"]\ntooling=["fixed-tool[tools]==1"]'
    text = (
        '[project]\nname="consumer"\nrequires-python=">=3.15"\n[dependency-groups]\n'
        + groups
        + '\n[tool.uv.sources]\nfixed-tool={git="'
        + source["git"]
        + '", rev="'
        + source["rev"]
        + '"}\n'
    )
    manifest = tmp_path / "pyproject.toml"
    manifest.write_bytes(text.encode("utf-8"))
    constraints, pins = dependencies.parse_project(text)
    assert pins == [dependencies.DevPin("tool", "2")]
    calls = []

    def export(command, args, **kwargs):
        assert args[:2] == ["export", "--script"]
        lines = Path(args[2]).read_text(encoding="utf-8").splitlines()
        metadata = tomllib.loads("\n".join(line.removeprefix("# ") for line in lines[1:-1]))
        assert metadata["tool"]["uv"]["sources"] == {"fixed-tool": source}
        assert metadata["dependencies"] == ["fixed-tool[tools]==1" if included else "fixed-tool==1", "tool>=2"]
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="tool==3\n", stderr="")

    monkeypatch.setattr(dependencies, "run_safe_command", export)
    assert dependencies.resolve_latest_pins(pins, constraints, tmp_path) == [dependencies.DevPin("tool", "3")]
    assert calls == ["uv"]
    assert manifest.read_bytes() == text.encode("utf-8")
    assert not (tmp_path / "uv.lock").exists()


def test_exact_git_source_rejects_ambiguous_normalized_aliases(tmp_path):
    text = '[tool.uv.sources]\nfixed-tool={git="https://example.invalid/lib",rev="' + "a" * 40 + '"}\nfixed_tool={index="private"}\n'
    with pytest.raises(ValueError, match="ambiguous source aliases"):
        python_resolution.policy(text, tmp_path)


def test_named_index_options_and_authentication_policy_are_retained(tmp_path):
    source = (
        '[tool.uv]\nconstraint-dependencies=["library<3"]\nbuild-constraint-dependencies=["builder==2"]\n'
        '[[tool.uv.index]]\nname="private"\nurl="../registry"\nexplicit=true\nauthenticate="always"\n'
        '[tool.uv.sources]\nlibrary={index="private"}\n'
    )
    result = python_resolution.policy(source, tmp_path)
    assert result["index"] == [{"name": "private", "url": (tmp_path.parent / "registry").as_uri(), "explicit": True, "authenticate": "always"}]
    assert result["sources"] == {"library": {"index": "private"}}
    assert result["constraint-dependencies"] == ["library<3"]
    assert result["build-constraint-dependencies"] == ["builder==2"]


def lock(*packages: tuple[str, str]) -> bytes:
    return (
        "version=1\n"
        + "".join(
            f'[[package]]\nname="fixture"\nversion="{version}"\nsource={{registry="https://example.invalid"}}\nresolution-markers={json.dumps([marker] if marker else [])}\n'
            for version, marker in packages
        )
    ).encode()


@pytest.mark.parametrize(
    "before,after,allowed",
    [
        (("2", ""), ("3", ""), True),
        (("2", ""), ("1", ""), False),
        (("2", "sys_platform == 'win32'"), ("1", "sys_platform == 'win32'"), False),
        (("2", "sys_platform == 'win32'"), ("1", "sys_platform != 'darwin'"), False),
    ],
)
def test_guard_compares_inactive_platform_versions_too(tmp_path: Path, before, after, allowed):
    path = tmp_path / "uv.lock"
    path.write_bytes(lock(after))
    if allowed:
        python_locks.require_no_downgrades(lock(before), path)
    else:
        with pytest.raises(ValueError, match="refusing dependency downgrade"):
            python_locks.require_no_downgrades(lock(before), path)


def test_distinct_forks_upgrade_independently(tmp_path):
    path = tmp_path / "uv.lock"
    path.write_bytes(lock(("2", "sys_platform == 'win32'"), ("4", "sys_platform != 'win32'")))
    python_locks.require_no_downgrades(lock(("1", "sys_platform == 'win32'"), ("3", "sys_platform != 'win32'")), path)


def test_overrides_cannot_force_managed_pin_below_its_floor():
    with pytest.raises(ValueError, match="refusing development-tool downgrade"):
        dependencies.parse_resolution("fixture==1\n", [dependencies.DevPin("fixture", "2")])


def test_included_group_python_constraints_preserve_their_own_scope():
    text = (
        '[project]\nname="example"\nrequires-python=">=3.14"\ndependencies=["runtime>=1"]\n'
        '[dependency-groups]\ndev=["tool==1",{include-group="tooling"}]\ntooling=["library==2; sys_platform == \'win32\'"]\n'
        '[tool.uv.dependency-groups]\ndev={requires-python=">=3.15"}\ntooling={requires-python="!=3.15.1"}\n'
    )
    requires, pins = dependencies.parse_project(text)
    assert str(requires) == ">=3.14"
    selected = dependencies._resolution_requirements(text, pins)
    from packaging.requirements import Requirement

    runtime, tool, library = map(Requirement, selected)
    assert str(runtime) == "runtime>=1"
    assert tool.marker is not None and library.marker is not None
    assert not tool.marker.evaluate({"python_full_version": "3.14.9"})
    assert tool.marker.evaluate({"python_full_version": "3.15.1"})
    assert not library.marker.evaluate({"python_full_version": "3.15.1", "sys_platform": "win32"})
    assert not library.marker.evaluate({"python_full_version": "3.15.2", "sys_platform": "linux"})
    assert library.marker.evaluate({"python_full_version": "3.15.2", "sys_platform": "win32"})
