"""Fixture setup rejects inherited destinations before any native mutation."""

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.fixture_environment import git_environment


@pytest.mark.parametrize(
    "suite,class_name,method",
    [
        ("cargo_docs/public_cargo_docs_consumer.py", "TestCargoDocumentation", "test_consumer_config_and_explicit_cfg_reach_packaged_rustdoc"),
        ("cargo_examples/public_cargo_examples_consumer.py", "TestNativeCargo", "test_native_discovery_feature_builds_and_actual_artifact_paths"),
    ],
)
def test_native_cargo_fixtures_ignore_conflicting_ambient_settings(tmp_path: Path, suite: str, class_name: str, method: str):
    home = tmp_path / "ambient-cargo"
    home.mkdir()
    config = b'[build]\nrustc = "nonexistent-ambient-rustc"\n'
    (home / "config.toml").write_bytes(config)
    poisoned = {
        "CARGO_HOME": str(home),
        "CARGO_BUILD_TARGET": "nonexistent-ambient-target",
        "CARGO_ENCODED_RUSTDOCFLAGS": "--cfg\x1fdocsrs",
        "RUSTDOCFLAGS": "--cfg docsrs",
        "RUSTFLAGS": "--invalid-ambient-flag",
        "RUSTC_WRAPPER": "nonexistent-ambient-wrapper",
        "RUSTC_BOOTSTRAP": "1",
        "RUSTUP_TOOLCHAIN": "nonexistent-ambient-toolchain",
    }
    case_type = runpy.run_path(str(Path(__file__).resolve().parents[1] / suite))[class_name]
    with patch.dict(os.environ, poisoned):
        case = case_type(method)
        try:
            case.setUp()
            getattr(case, method)()
        finally:
            case.doCleanups()
        assert all(os.environ[name] == value for name, value in poisoned.items())
    assert (home / "config.toml").read_bytes() == config


def test_read_only_git_fixtures_ignore_redirected_environment(tmp_path: Path):
    tests = Path(__file__).resolve().parents[1]
    poisoned = {
        "GIT_DIR": str(tmp_path / "missing.git"),
        "GIT_WORK_TREE": str(tmp_path),
        "GIT_INDEX_FILE": str(tmp_path / "missing-index"),
        "GIT_DEFAULT_HASH": "sha256",
        "GIT_CONFIG_PARAMETERS": "invalid ambient configuration",
    }
    justfile_case = runpy.run_path(str(tests / "utilities/test_justfile_format.py"))["test_justfile_policy_keeps_crlf_out_of_formatter_input"]
    case_type = runpy.run_path(str(tests / "utilities/public_api_consumer.py"))["TestProcess"]
    with patch.dict(os.environ, poisoned):
        justfile_case("justfile")
        case = case_type("test_git_bytes_and_clean_filters")
        try:
            case.setUp()
            case.test_git_bytes_and_clean_filters()
        finally:
            case.doCleanups()
        assert all(os.environ[name] == value for name, value in poisoned.items())


def test_git_environment_removes_selectors_and_preserves_mutation_control():
    original = {
        "PATH": "fixture executables",
        "GIT_DIR": "external/.git",
        "GIT_WORK_TREE": "external",
        "GIT_INDEX_FILE": "external/index",
        "GIT_COMMON_DIR": "external/common",
        "GIT_CONFIG_PARAMETERS": "user hooks",
        "git_config_global": "external/config",
        "RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS": "1",
    }
    isolated = git_environment(original)
    assert isolated == {
        "PATH": "fixture executables",
        "RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TEMPLATE_DIR": "",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": os.devnull,
        "GIT_CONFIG_KEY_1": "core.attributesFile",
        "GIT_CONFIG_VALUE_1": os.devnull,
    }
    assert original["GIT_DIR"] == "external/.git"


@pytest.mark.parametrize("copied", [False, True], ids=["source", "installed-layout"])
def test_isolated_consumer_sanitizes_before_first_git_call(tmp_path: Path, copied: bool):
    tests = Path(__file__).resolve().parents[1]
    suite = tests / "validation/public_validation_consumer.py"
    if copied:
        shutil.copyfile(suite, tmp_path / suite.name)
        shutil.copyfile(tests / "fixture_environment.py", tmp_path / "fixture_environment.py")
        suite = tmp_path / suite.name
    child = """import os, runpy, sys
from pathlib import Path
from unittest.mock import patch
case = runpy.run_path(sys.argv[1])['TestGitDiscovery']()
case.setUp()
class Intercepted(Exception):
    pass
def intercept(command, **kwargs):
    assert command[:2] == ['git', 'init']
    assert Path(kwargs['cwd']).is_dir()
    assert not {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'} & os.environ.keys()
    assert os.environ['GIT_CONFIG_GLOBAL'] == os.devnull
    assert os.environ['GIT_TEMPLATE_DIR'] == ''
    assert os.environ['GIT_CONFIG_KEY_0'] == 'core.hooksPath'
    assert os.environ['GIT_CONFIG_VALUE_0'] == os.devnull
    assert os.environ['RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS'] == '1'
    raise Intercepted
with patch.dict(os.environ, dict.fromkeys(('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'), 'external')):
    before = dict(os.environ)
    try:
        with patch('subprocess.run', side_effect=intercept):
            try:
                case.test_tracked_and_new_nonignored_python_files_are_selected()
            except Intercepted:
                pass
            else:
                raise AssertionError('fixture did not reach the intercepted Git boundary')
    finally:
        case.doCleanups()
    assert dict(os.environ) == before
print('isolated fixture transport checked')
"""
    environment = {**os.environ, "RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS": "1"}
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", child, str(suite)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "isolated fixture transport checked\n"
    assert result.stderr == ""
