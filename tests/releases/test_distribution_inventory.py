"""Reject stale or incomplete publication inputs before installation begins."""

import importlib.util
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


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


def test_install_check_does_not_forward_external_project_locations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("check_install", ROOT / "scripts/check_install.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    # Only the inventory is needed: intercept the first subprocess before it can
    # create an environment or honor any inherited project-directory overrides.
    with zipfile.ZipFile(tmp_path / f"research_repo_tools-{version}-py3-none-any.whl", "w") as archive:
        for name in ("templates/cliff.toml", "toolchain_setup.py", "py.typed", "LICENSE"):
            archive.writestr(f"research_repo_tools/{name}", "")
    with tarfile.open(tmp_path / f"research_repo_tools-{version}.tar.gz", "w:gz") as archive:
        for name in ("tests/changelog/test_contract.py", "LICENSE"):
            archive.addfile(tarfile.TarInfo(f"research_repo_tools-{version}/{name}"))
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
