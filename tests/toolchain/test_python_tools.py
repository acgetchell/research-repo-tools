"""Release-owned tool pins, adoption, and ordinary update ownership."""

import subprocess
import tomllib
from importlib.metadata import version

import pytest

from research_repo_tools import config, dependencies, python_tools
from research_repo_tools import python_adoption as adoption
from tests.toolchain.test_python_adoption import consumer as consumer
from tests.toolchain.test_python_adoption import snapshot


def opt_in(root, *, python=True):
    path = root / "pyproject.toml"
    text = path.read_bytes().decode().replace("inherit-python = true", f"inherit-python = {str(python).lower()}\r\ninherit-python-tools = true")
    path.write_bytes(text.encode())


@pytest.fixture
def tools_consumer(consumer, monkeypatch):
    root, calls = consumer
    opt_in(root)
    # Both authorities come from the same installed distribution in production.
    monkeypatch.setattr(python_tools, "version", lambda _: "0.1.7")
    return root, calls


def test_profile_comes_from_installed_metadata():
    assert set(python_tools.versions()) == {"pytest", "ruff", "ty"}
    assert all(pin == version(name) for name, pin in python_tools.versions().items())


def test_adoption_retires_stale_pins_preserves_policy_and_repeats(tools_consumer):
    root, _ = tools_consumer
    manifest = root / "pyproject.toml"
    manifest.write_bytes(manifest.read_bytes().replace(b"ruff==0.16.9", b"ruff==0.15.0"))
    before = snapshot(root)
    plan = adoption.plan_python_adoption(config.load(root=root))
    assert snapshot(root) == before
    data = tomllib.loads(dict(plan.replacements)["pyproject.toml"].decode())
    assert data["dependency-groups"]["dev"] == [{"include-group": "tooling"}, "ruff"]
    assert data["dependency-groups"]["notebook"] == ["research-repo-tools[notebooks,python-tools]==0.1.7"]
    assert data["tool"]["ruff"]["line-length"] == 100
    assert data["tool"]["ruff"]["lint"]["per-file-ignores"] == {"negative.py": ["F821"]}
    adoption.apply_python_adoption(plan)
    assert not adoption.plan_python_adoption(config.load(root=root)).changed_paths


def test_tools_only_preserve_python_selector_targets_and_runtime(tools_consumer):
    root, _ = tools_consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes().replace(b"inherit-python = true", b"inherit-python = false").replace(b"package = false", b"package = true"))
    path.write_bytes(path.read_bytes().replace(b">=3.14", b">=3.12"))
    # Shared package requires >=3.14 today, independently of the test's future
    # interpreter baseline used in the common adoption fixture.
    from research_repo_tools import python_baseline

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(python_baseline, "baseline", lambda: python_baseline.PythonBaseline(">=3.14", "3.14", "0.1.7"))
        plan = adoption.plan_python_adoption(config.load(root=root))
    data = tomllib.loads(dict(plan.replacements)["pyproject.toml"].decode())
    assert data["project"]["requires-python"] == ">=3.12"
    assert data["tool"]["ruff"]["target-version"] == "py314"
    assert data["tool"]["ty"]["environment"]["python-version"] == "3.14"
    assert ".python-version" not in dict(plan.replacements)
    assert plan.tools.python == "3.14"


@pytest.mark.parametrize("requirement", ["ruff<0.1", "ruff==0.1; sys_platform == 'win32'", "ruff[something]==0.1", "ruff @ https://example.invalid/ruff.whl"])
def test_conflicting_constraints_fail_without_edits_or_resolution(tools_consumer, requirement):
    root, calls = tools_consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes().replace(b"ruff==0.16.9", requirement.encode()))
    before = snapshot(root)
    with pytest.raises(ValueError, match="conflicts with shared"):
        adoption.plan_python_adoption(config.load(root=root))
    assert not calls and snapshot(root) == before


@pytest.mark.parametrize("phase", ["lock", "candidate-sync", "final-sync", "tool-verification"])
def test_failure_preserves_prior_files_and_environment(tools_consumer, monkeypatch, phase):
    root, _ = tools_consumer
    (root / ".venv").mkdir()
    (root / ".venv/pyvenv.cfg").write_bytes(b"original environment")
    before = snapshot(root)
    execute = adoption.run_safe_command

    def fail(command, args, **kwargs):
        selected = (
            phase == "lock"
            and args[0] == "lock"
            or phase == "candidate-sync"
            and args[0] == "sync"
            and kwargs["cwd"] != root
            or phase == "final-sync"
            and args[0] == "sync"
            and kwargs["cwd"] == root
            or phase == "tool-verification"
            and any("research_repo_tools.python_tools" in arg for arg in args)
            and kwargs["cwd"] == root
        )
        if selected:
            raise subprocess.CalledProcessError(9, [command, *args], stderr="fixture failure")
        return execute(command, args, **kwargs)

    monkeypatch.setattr(adoption, "run_safe_command", fail)
    with pytest.raises(subprocess.CalledProcessError):
        plan = adoption.plan_python_adoption(config.load(root=root))
        adoption.apply_python_adoption(plan)
    assert snapshot(root) == before


def test_ordinary_updates_preserve_extra_and_only_advance_unrelated_tools(tools_consumer):
    root, _ = tools_consumer
    plan = adoption.plan_python_adoption(config.load(root=root))
    text = dict(plan.replacements)["pyproject.toml"].decode().replace('"ruff"', '"ruff", "unrelated==1.0"')
    _, pins = dependencies.parse_project(text)
    assert pins == [dependencies.DevPin("unrelated", "1.0")]
    requirements = dependencies._resolution_requirements(text, pins)
    assert "research-repo-tools[python-tools]==0.1.7" in requirements
    assert "ruff" in requirements and "unrelated" in requirements


def test_stale_direct_tool_pin_blocks_ordinary_update(tools_consumer):
    root, _ = tools_consumer
    plan = adoption.plan_python_adoption(config.load(root=root))
    text = dict(plan.replacements)["pyproject.toml"].decode().replace('"ruff"', '"ruff==0.15.0"')
    with pytest.raises(ValueError, match="competes"):
        dependencies.parse_project(text)


def test_maintainer_resolves_local_extra_pins_instead_of_published_self_dependency():
    text = """[project]
name="research-repo-tools"
requires-python=">=3.14"
[project.optional-dependencies]
python-tools=["pytest==9.1.1", "ruff==0.16.9", "ty==0.0.84"]
[dependency-groups]
dev=["research-repo-tools[python-tools]", "unrelated==1.0"]
"""
    _, pins = dependencies.parse_project(text)
    assert dependencies._resolution_requirements(text, pins) == ["pytest==9.1.1", "ruff==0.16.9", "ty==0.0.84", "unrelated"]


@pytest.mark.parametrize("value", [1, "true", None])
def test_opt_in_requires_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="inherit-python-tools must be a boolean"):
        config.parse({"toolchain": {"inherit-python-tools": value}}, root=tmp_path)


def test_adoption_retains_explicit_settings_through_candidate_and_apply(tools_consumer):
    root, calls = tools_consumer
    manifest = root / "pyproject.toml"
    manifest.write_bytes(manifest.read_bytes().replace(b"inherit-python-tools = true", b"inherit-python-tools = false"))
    settings = config.parse({"toolchain": {"inherit-python": True, "inherit-python-tools": True, "binaries": {"gitleaks": "8.30.0"}}}, root=root)
    plan = adoption.plan_python_adoption(settings)
    assert [tool.name for tool in plan.tools.binaries] == ["gitleaks"]
    adoption.apply_python_adoption(plan)
    checks = [(args, cwd) for _, args, cwd, _ in calls if any("python_tools" in arg or "python-tools-check" == arg for arg in args)]
    assert len(checks) == 2
    assert checks[-1][1] == root


def test_update_uses_explicit_configuration_for_the_development_python_range(tmp_path, monkeypatch):
    from research_repo_tools.cli import main

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="consumer"\nrequires-python=">=3.12"\n'
        f'[dependency-groups]\ndev=["research-repo-tools[python-tools]=={version("research-repo-tools")}", "unrelated==1.0"]\n',
        encoding="utf-8",
        newline="\n",
    )
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("[toolchain]\ninherit-python-tools=true\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(python_tools, "check", lambda *args, **kwargs: None)
    observed = []

    def resolve(pins, requires_python, root, **kwargs):
        observed.append(requires_python)
        return pins

    monkeypatch.setattr(dependencies, "resolve_latest_pins", resolve)
    assert main(["--root", str(tmp_path), "--config", str(settings_file), "deps", "update-python"]) == 0
    assert len(observed) == 1
    assert "3.12" not in observed[0] and "3.14" in observed[0]
