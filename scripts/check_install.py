"""Build-independent wheel/sdist consumer and optional-extra checks using uv.

Run after `uv build`. Every environment and consumer lives outside the checkout.
No user-wide tools, sibling repositories, or project lockfiles are modified.
"""

import argparse
import os
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 300


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True, encoding="utf-8", timeout=TIMEOUT)
    return result.stdout


BASE_SMOKE = r"""
import importlib, importlib.metadata, importlib.util, pathlib, pkgutil, socket, sys
import research_repo_tools
root = pathlib.Path(sys.argv[1]).resolve()
assert not pathlib.Path(research_repo_tools.__file__).is_relative_to(root)
def no_network(*args, **kwargs):
    raise AssertionError("imports attempted network access")
socket.create_connection = no_network
before = sorted(pathlib.Path.cwd().rglob("*"))
for module in pkgutil.walk_packages(research_repo_tools.__path__, research_repo_tools.__name__ + "."):
    importlib.import_module(module.name)
assert sorted(pathlib.Path.cwd().rglob("*")) == before
for optional in ("nbformat", "nbclient", "matplotlib", "numpy", "pandas", "polars", "torch", "pytest"):
    assert importlib.util.find_spec(optional) is None, optional
from research_repo_tools.changelog import TEMPLATES, template
for name in TEMPLATES:
    assert template(name).strip()
assert len(importlib.metadata.distribution("research-repo-tools").entry_points) == 1
from research_repo_tools.cli import main
consumer = pathlib.Path.cwd() / "minimal consumer"
consumer.mkdir()
changelog = consumer / "CHANGELOG.md"
changelog.write_text("# Changelog\n\n## [1.1.0] - 2026-09-07\n\n- Current.\n\n## [1.0.0] - 2026-08-01\n\n- Prior.\n")
assert main(["--root", str(consumer), "changelog", "archive"]) == 0
assert (consumer / "docs/archives/changelog/1.0.md").is_file()
assert main(["--root", str(consumer), "changelog", "notes", "v1.0.0"]) == 0
"""


def check(dist: Path) -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    wheel = dist / f"research_repo_tools-{version}-py3-none-any.whl"
    sdist = dist / f"research_repo_tools-{version}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "research_repo_tools/templates/cliff.toml" in names
        assert "research_repo_tools/py.typed" in names
        assert not any("/compat/" in name or name.startswith("tests/") for name in names)
        assert sum(name.endswith("/LICENSE") for name in names) == 1
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        assert any(name.endswith("/tests/changelog/test_contract.py") for name in names)
        assert sum(name.endswith("/LICENSE") for name in names) == 1
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv must be installed to check distributions")
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="research-repo-tools-installed-") as directory:
        temporary = Path(directory)
        for artifact in (wheel, sdist):
            name = "wheel" if artifact == wheel else "sdist"
            consumer = temporary / name
            consumer.mkdir()
            environment = consumer / "environment"
            run([uv, "venv", "--python", "3.14", str(environment)], cwd=consumer, env=env)
            scripts = environment / ("Scripts" if os.name == "nt" else "bin")
            python = scripts / ("python.exe" if os.name == "nt" else "python")
            run([uv, "pip", "install", "--python", str(python), str(artifact)], cwd=consumer, env=env)
            local_env = {**env, "PATH": str(scripts) + os.pathsep + env.get("PATH", "")}
            run([str(python), "-c", BASE_SMOKE, str(ROOT)], cwd=consumer, env=local_env)
            command = scripts / ("research-repo-tools.exe" if os.name == "nt" else "research-repo-tools")
            assert run([str(command), "--version"], cwd=consumer, env=local_env).strip() == version
            assert "changelog" in run([str(command), "--help"], cwd=consumer, env=local_env)
            if artifact == wheel:
                run([uv, "pip", "install", "--python", str(python), f"{artifact}[just]"], cwd=consumer, env=env)
                expected_just = metadata["project"]["optional-dependencies"]["just"][0].split("==")[1]
                just = scripts / ("just.exe" if os.name == "nt" else "just")
                assert run([str(just), "--version"], cwd=consumer, env=local_env).strip() == f"just {expected_just}"
            print(f"PASS: installed {name} outside checkout" + ("; just extra" if artifact == wheel else "; minimal dependencies"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        check(args.dist.resolve())
    except subprocess.CalledProcessError as error:
        print(error.stdout or "")
        print(error.stderr or "")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
