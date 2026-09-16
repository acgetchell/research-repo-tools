"""Verify the native install harness cannot run against an ordinary user account."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def harness():
    spec = importlib.util.spec_from_file_location("check_setup", ROOT / "scripts/check_setup.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("actions,runner", [(None, None), ("true", None), ("true", "self-hosted"), ("false", "github-hosted")])
def test_native_check_refuses_local_and_self_hosted_execution(harness, tmp_path, monkeypatch, actions, runner):
    for name, value in (("GITHUB_ACTIONS", actions), ("RUNNER_ENVIRONMENT", runner)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    def unexpected(*args, **kwargs):
        pytest.fail("native check attempted an operation before rejecting a non-disposable runner")

    monkeypatch.setattr(harness.subprocess, "run", unexpected)
    monkeypatch.setattr(harness.tempfile, "TemporaryDirectory", unexpected)
    with pytest.raises(RuntimeError, match="disposable GitHub-hosted runner"):
        harness.check(tmp_path / "nonexistent distributions")


def test_native_environment_replaces_inherited_installation_and_project_overrides(harness, tmp_path, monkeypatch):
    overrides = (
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_WORKING_DIR",
        "UV_ENV_FILE",
        "UV_TOOL_BIN_DIR",
        "UV_TOOL_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "UV_CACHE_DIR",
        "RUSTUP_HOME",
        "CARGO_HOME",
        "RESEARCH_REPO_TOOLS_HOME",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "ZDOTDIR",
        "BASH_ENV",
        "ENV",
    )
    for name in overrides:
        monkeypatch.setenv(name, "outside-fixture")
    compiler_bin = tmp_path / "native-compiler"
    monkeypatch.setenv("PATH", os.pathsep.join([str(Path(sys.executable).parent), str(compiler_bin), str(ROOT / ".venv" / "Scripts")]))
    monkeypatch.setenv("SDKROOT", "native-sdk")
    original = dict(os.environ)
    env = harness.isolated_environment(tmp_path)
    assert dict(os.environ) == original
    assert env["PATH"] == str(compiler_bin)
    assert env["SDKROOT"] == "native-sdk"
    assert all(value != "outside-fixture" for name, value in env.items() if name in overrides)
    for name in ("UV_TOOL_BIN_DIR", "UV_TOOL_DIR", "UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR", "RESEARCH_REPO_TOOLS_HOME"):
        assert Path(env[name]).is_relative_to(tmp_path)


def test_native_shell_check_rejects_system_just_even_when_version_matches(harness, tmp_path, monkeypatch):
    env = {"UV_TOOL_BIN_DIR": str(tmp_path / "user-bin"), "PATH": "", "SHELL": "/bin/bash"}
    monkeypatch.setattr(harness, "run", lambda *args, **kwargs: "/system/just\njust 1.58.0\n")
    with pytest.raises(AssertionError):
        harness.verify_shell(tmp_path, env, "1.58.0")


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell selection; Windows setup updates the user PATH registry")
@pytest.mark.parametrize("platform,expected_shell", [("linux", "/bin/bash"), ("darwin", "/bin/zsh")])
def test_native_environment_removes_markers_that_override_the_selected_shell(harness, tmp_path, monkeypatch, platform, expected_shell):
    markers = ("NU_VERSION", "FISH_VERSION", "BASH_VERSION", "ZSH_VERSION", "KSH_VERSION", "PSModulePath")
    for name in markers:
        monkeypatch.setenv(name, "inherited-shell")
    monkeypatch.setattr(harness.sys, "platform", platform)
    env = harness.isolated_environment(tmp_path)
    assert env["SHELL"] == expected_shell
    assert not set(markers).intersection(env)
