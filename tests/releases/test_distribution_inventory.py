"""Reject stale or incomplete publication inputs before installation begins."""

import importlib.util
import os
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def install_checker():
    spec = importlib.util.spec_from_file_location("check_install", ROOT / "scripts/check_install.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("utf8_setting", ["0", "1"])
def test_isolated_consumers_use_utf8_transport(tmp_path: Path, install_checker, utf8_setting: str) -> None:
    env = {
        **os.environ,
        "PYTHONHOME": str(tmp_path / "absent interpreter"),
        "PYTHONPATH": str(tmp_path),
        "PYTHONUTF8": utf8_setting,
        "PYTHONIOENCODING": "cp1252:strict",
        "PYTHONDONTWRITEBYTECODE": "0",
    }
    child = (
        "import sys\n"
        "assert sys.flags.isolated == 1\n"
        "assert sys.flags.utf8_mode == 1\n"
        "assert sys.dont_write_bytecode\n"
        "assert sys.stdout.encoding == sys.stderr.encoding == 'utf-8'\n"
        "print('分析 café')\n"
        "print('分析 café', file=sys.stderr)\n"
    )
    assert install_checker.run_isolated(Path(sys.executable), ["-c", child], cwd=tmp_path, env=env) == "分析 café\n"


@pytest.mark.parametrize("inventory", ["empty", "wheel-only", "sdist-only", "stale-wheel", "stale-sdist"])
def test_install_check_rejects_unexpected_distribution_set(tmp_path: Path, inventory: str) -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    wheel = f"research_repo_tools-{version}-py3-none-any.whl"
    sdist = f"research_repo_tools-{version}.tar.gz"
    names = {
        "empty": [],
        "wheel-only": [wheel],
        "sdist-only": [sdist],
        "stale-wheel": [wheel, sdist, "research_repo_tools-0.0.0-py3-none-any.whl"],
        "stale-sdist": [wheel, sdist, "research_repo_tools-0.0.0.tar.gz"],
    }[inventory]
    for name in names:
        (tmp_path / name).touch()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_install.py"), "--dist", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert f"Expected only {wheel} and {sdist}" in result.stderr
    assert "BadZipFile" not in result.stderr
    assert "PASS:" not in result.stdout


@pytest.fixture
def distribution_inventory(tmp_path: Path) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with zipfile.ZipFile(tmp_path / f"research_repo_tools-{version}-py3-none-any.whl", "w") as archive:
        for name in ("templates/cliff.toml", "toolchain_setup.py", "py.typed", "LICENSE"):
            archive.writestr(f"research_repo_tools/{name}", "")
    with tarfile.open(tmp_path / f"research_repo_tools-{version}.tar.gz", "w:gz") as archive:
        for name in ("tests/changelog/test_contract.py", "LICENSE"):
            archive.addfile(tarfile.TarInfo(f"research_repo_tools-{version}/{name}"))
    return tmp_path


def test_changelog_only_requires_external_generator_before_installing(distribution_inventory: Path, monkeypatch: pytest.MonkeyPatch, install_checker) -> None:
    monkeypatch.setattr(install_checker.shutil, "which", lambda program: "uv" if program == "uv" else None)
    with pytest.raises(RuntimeError, match="git-cliff must be installed"):
        install_checker.check(distribution_inventory, changelog_only=True)


def test_changelog_only_runs_one_installed_generation_contract_per_artifact(
    distribution_inventory: Path, monkeypatch: pytest.MonkeyPatch, install_checker
) -> None:
    monkeypatch.setattr(install_checker.shutil, "which", lambda program: program)
    monkeypatch.setattr(install_checker, "run", lambda *args, **kwargs: "")
    calls = []
    monkeypatch.setattr(install_checker, "run_isolated", lambda python, args, **kwargs: calls.append((python, args)))
    install_checker.check(distribution_inventory, changelog_only=True)
    assert len(calls) == 2
    assert {python.parent.parent.parent.name for python, _ in calls} == {"wheel", "sdist"}
    for _, args in calls:
        assert Path(args[0]).name == "public_publishing_consumer.py"
        assert args[1:] == ["TestPublishingConsumer.test_installed_cli_generation_with_real_external_generator"]


def test_install_check_does_not_forward_external_project_locations(distribution_inventory: Path, monkeypatch: pytest.MonkeyPatch, install_checker) -> None:
    module = install_checker
    tmp_path = distribution_inventory
    # Intercept the first subprocess before it can create an environment or
    # honor inherited project-directory overrides.
    external = tmp_path / "external project"
    external.mkdir()
    sentinel = external / "uv.lock"
    sentinel.write_bytes(b"do not modify\n")
    selectors = ("UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_ENV_FILE")
    for name in selectors:
        monkeypatch.setenv(name, str(external))
    monkeypatch.setenv("UV_HTTP_TIMEOUT", "123")
    monkeypatch.setenv("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS", "1")
    monkeypatch.setattr(module.shutil, "which", lambda _: "uv")

    class CheckedEnvironment(Exception):
        pass

    def intercept(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
        assert command[:2] == ["uv", "venv"]
        assert not cwd.is_relative_to(external)
        assert not set(selectors).intersection(env)
        assert env["UV_HTTP_TIMEOUT"] == "123"
        assert env["RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS"] == "1"
        raise CheckedEnvironment

    monkeypatch.setattr(module, "run", intercept)
    with pytest.raises(CheckedEnvironment):
        module.check(tmp_path)
    assert sentinel.read_bytes() == b"do not modify\n"
