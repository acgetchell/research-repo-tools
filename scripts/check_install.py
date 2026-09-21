"""Build-independent wheel/sdist consumer checks using uv.

Run after `uv build`. Every environment and consumer lives outside the checkout.
No user-wide tools, sibling repositories, or project lockfiles are modified.
"""

import argparse
import json
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
assert research_repo_tools.__version__ == importlib.metadata.version("research-repo-tools")
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
notebook = consumer / "minimal.ipynb"
notebook.write_text('{"nbformat":4,"nbformat_minor":5,"metadata":{},"cells":[]}')
assert main(["--root", str(consumer), "notebooks", "group"]) == 0
assert main(["--root", str(consumer), "notebooks", "check", str(notebook)]) == 1
"""


def check_python_update(consumer: Path, artifact: Path, version: str, uv: str, just: Path, env: dict[str, str]) -> None:
    """Prove full lock upgrades and explicit dev sync with the installed template."""
    consumer.mkdir()
    wheels = consumer / "wheels"
    wheels.mkdir()

    def fixture(release: str) -> None:
        info = f"update_fixture-{release}.dist-info"
        with zipfile.ZipFile(wheels / f"update_fixture-{release}-py3-none-any.whl", "w") as archive:
            archive.writestr(f"{info}/METADATA", f"Metadata-Version: 2.3\nName: update-fixture\nVersion: {release}\nRequires-Python: >=3.14\n")
            archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr(f"{info}/RECORD", "")

    fixture("1.0.0")
    uv_version = run([uv, "--version"], cwd=consumer, env=env).split()[1]
    manifest = consumer / "pyproject.toml"
    manifest.write_text(
        '[project]\nname="update-consumer"\nversion="0.1.0"\nrequires-python=">=3.14"\n'
        f'[dependency-groups]\ntooling=["research-repo-tools=={version}"]\ndev=[{{include-group="tooling"}}, "update-fixture>=1,<3"]\n'
        f'[tool.uv]\npackage=false\ndefault-groups=[]\nrequired-version="=={uv_version}"\nfind-links=["wheels"]\n'
        f"[tool.uv.sources]\nresearch-repo-tools={{path={json.dumps(str(artifact.resolve()))}}}\n",
        encoding="utf-8",
    )
    (consumer / ".python-version").write_text("3.14\n", encoding="utf-8")
    # Dependencies of this exact artifact are already cached by the other checks.
    offline = {**env, "UV_OFFLINE": "1"}
    run([uv, "sync", "--managed-python", "--group", "dev"], cwd=consumer, env=offline)
    cli = [uv, "run", "--locked", "--no-sync", "research-repo-tools"]
    run([*cli, "templates", "justfile", "--output", "justfile"], cwd=consumer, env=offline)
    probe = [uv, "run", "--locked", "--no-sync", "python", "-c", 'from importlib.metadata import version; print(version("update-fixture"))']
    assert run(probe, cwd=consumer, env=offline).strip() == "1.0.0"
    original = manifest.read_bytes()
    fixture("2.0.0")
    # No exact direct dev pin changes: only a full lock upgrade can select 2.0.0.
    run([str(just), "update-python-dependencies"], cwd=consumer, env=offline)
    assert manifest.read_bytes() == original, "update changed retained constraints or the shared package pin"
    assert run(probe, cwd=consumer, env=offline).strip() == "2.0.0", "updated lock was not synchronized into dev"
    locked = tomllib.loads((consumer / "uv.lock").read_text(encoding="utf-8"))
    assert next(package["version"] for package in locked["package"] if package["name"] == "update-fixture") == "2.0.0"


def check_update_bootstrap(consumer: Path, uv: str, just: Path, env: dict[str, str]) -> None:
    """Keep a native build out of update launchers until the checked final sync."""
    run([uv, "run", "--locked", "--no-sync", "research-repo-tools", "templates", "justfile", "--output", "justfile"], cwd=consumer, env=env)
    cargo = consumer / "Cargo.toml"
    cargo.write_text("[workspace]\nmembers=[]\n", encoding="utf-8")
    # Execute real uv, Just, and toolchain run. Replace only the inner Cargo
    # workload: this boundary check needs no Rust installation or registry.
    recorder = consumer / "record_cargo.py"
    recorder.write_text(
        "import json, pathlib, sys\n"
        'cargo = pathlib.Path("Cargo.toml")\n'
        'if sys.argv[1] == "upgrade": cargo.write_text(cargo.read_text() + "# upgraded\\n")\n'
        'else: assert "# upgraded" in cargo.read_text()\n'
        'with pathlib.Path("cargo_calls.jsonl").open("a") as stream: stream.write(json.dumps(sys.argv[1:]) + "\\n")\n',
        encoding="utf-8",
    )
    justfile = consumer / "justfile"
    justfile.write_text(justfile.read_text(encoding="utf-8").replace("-- cargo ", "-- python record_cargo.py "), encoding="utf-8")
    # The empty Cargo tool table must also be usable before the project builds.
    run([str(just), "update-cargo-tools"], cwd=consumer, env=env)
    result = subprocess.run([str(just), "update-dependencies"], cwd=consumer, env=env, capture_output=True, encoding="utf-8", timeout=TIMEOUT)
    calls = consumer / "cargo_calls.jsonl"
    assert calls.is_file(), result.stderr
    assert [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] == [["upgrade", "--incompatible", "allow"], ["update"]]
    # The intentionally missing backend must be reached only by the final sync,
    # after both Cargo commands and the Python pin/lock updates have completed.
    assert result.returncode != 0, "the unavailable native backend unexpectedly built"
    assert "toolchain run -- uv sync --locked --managed-python --group dev" in result.stderr, result.stderr
    assert "intentionally_missing_native_backend" in result.stderr, result.stderr


def check(dist: Path) -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    expected_just = next(item.removeprefix("rust-just==") for item in metadata["project"]["dependencies"] if item.startswith("rust-just=="))
    notebook_checkers = [
        next(item for item in metadata["dependency-groups"]["dev"] if isinstance(item, str) and item.startswith(f"{tool}==")) for tool in ("ruff", "ty")
    ]
    wheel = dist / f"research_repo_tools-{version}-py3-none-any.whl"
    sdist = dist / f"research_repo_tools-{version}.tar.gz"
    artifacts = set(dist.glob("*.whl")) | set(dist.glob("*.tar.gz"))
    if artifacts != {wheel, sdist}:
        raise ValueError(f"Expected only {wheel.name} and {sdist.name}; found {sorted(path.name for path in artifacts)}")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "research_repo_tools/templates/cliff.toml" in names
        assert "research_repo_tools/toolchain_setup.py" in names
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
    # Keep network/cache settings, but never let the caller redirect these
    # temporary consumers into another project, environment, or working directory.
    external_locations = {
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "UV_ENV_FILE",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_WORKING_DIR",
        "UV_WORKING_DIRECTORY",
    }
    env = {key: value for key, value in os.environ.items() if key not in external_locations}
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
            # Run the same consumer suite against each installed artifact, using
            # only documented imports and no source checkout or pytest dependency.
            public_suite = consumer / "public_api_consumer.py"
            public_suite.write_bytes((ROOT / "tests/utilities/public_api_consumer.py").read_bytes())
            run([str(python), "-I", str(public_suite)], cwd=consumer, env=local_env)
            release_suite = consumer / "public_release_consumer.py"
            release_suite.write_bytes((ROOT / "tests/releases/public_release_consumer.py").read_bytes())
            run([str(python), "-I", str(release_suite)], cwd=consumer, env=local_env)
            command = scripts / ("research-repo-tools.exe" if os.name == "nt" else "research-repo-tools")
            assert run([str(command), "--version"], cwd=consumer, env=local_env).strip() == version
            assert "changelog" in run([str(command), "--help"], cwd=consumer, env=local_env)
            assert "setup" in run([str(command), "--help"], cwd=consumer, env=local_env)
            just = scripts / ("just.exe" if os.name == "nt" else "just")
            assert run([str(just), "--version"], cwd=consumer, env=local_env).strip() == f"just {expected_just}"
            # Exercise the installed recipe template after a default sync that
            # excludes tooling. Recipes must explicitly restore their own group.
            recipe_consumer = consumer / "recipe consumer"
            recipe_consumer.mkdir()
            uv_version = run([uv, "--version"], cwd=consumer, env=local_env).split()[1]
            notebook_group = "notebook" if artifact == wheel else "analysis"
            (recipe_consumer / "pyproject.toml").write_text(
                '[project]\nname="recipe-consumer"\nversion="0.1.0"\nrequires-python=">=3.14"\n'
                f'[dependency-groups]\ntooling=["research-repo-tools=={version}"]\ndev=[{{include-group="tooling"}}, {", ".join(map(json.dumps, notebook_checkers))}]\n'
                f'{notebook_group}=["research-repo-tools[notebooks]=={version}"]\n'
                f'[tool.uv]\npackage=false\ndefault-groups=[]\nrequired-version="=={uv_version}"\n'
                f"[tool.uv.sources]\nresearch-repo-tools={{path={json.dumps(str(artifact.resolve()))}}}\n"
                f'[tool.research-repo-tools.notebooks]\ngroup="{notebook_group}"\n',
                encoding="utf-8",
            )
            run([uv, "lock", "--python", str(python)], cwd=recipe_consumer, env=env)
            run([uv, "sync", "--locked", "--python", str(python)], cwd=recipe_consumer, env=env)
            recipe_cli = recipe_consumer / ".venv" / scripts.name / command.name
            assert not recipe_cli.exists(), "default sync unexpectedly installed the non-default tooling group"
            lock = (recipe_consumer / "uv.lock").read_bytes()
            run([str(command), "templates", "justfile", "--output", "justfile"], cwd=recipe_consumer, env=env)
            (recipe_consumer / "CHANGELOG.md").write_text("# Changelog\n\n## [0.1.0] - 2026-09-16\n\n- Recipe works.\n", encoding="utf-8")
            assert "release-notes" in run([str(just), "help"], cwd=recipe_consumer, env=local_env)
            assert run([str(just), "release-notes", "v0.1.0"], cwd=recipe_consumer, env=env).strip() == "- Recipe works."
            assert recipe_cli.is_file(), "recipe did not install its declared tooling group"
            assert (recipe_consumer / "uv.lock").read_bytes() == lock, "recipe changed the lockfile"
            (recipe_consumer / ".python-version").write_text("3.14\n", encoding="utf-8")
            # Install the extra from this same distribution, exercise locked sync
            # and a real project kernel without touching the user's kernels.
            run([str(just), "notebook-sync"], cwd=recipe_consumer, env=env)
            notebook = recipe_consumer / "notebooks" / "smoke.ipynb"
            notebook.parent.mkdir()
            notebook.write_text(
                json.dumps(
                    {
                        "nbformat": 4,
                        "nbformat_minor": 5,
                        "metadata": {},
                        "cells": [
                            {
                                "cell_type": "code",
                                "id": "smoke",
                                "metadata": {},
                                "source": 'print("installed notebook works")',
                                "outputs": [],
                                "execution_count": None,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            original_notebook = notebook.read_bytes()
            run([str(just), "notebook-check", "notebooks/smoke.ipynb"], cwd=recipe_consumer, env=env)
            run([str(just), "notebook-clear", "notebooks/smoke.ipynb"], cwd=recipe_consumer, env=env)
            run([str(just), "notebook-lint", "notebooks/smoke.ipynb"], cwd=recipe_consumer, env=env)
            run([str(just), "notebook-execute", "notebooks/smoke.ipynb"], cwd=recipe_consumer, env=env)
            report = json.loads((recipe_consumer / "target/notebooks/notebooks/smoke.report.json").read_text(encoding="utf-8"))
            assert report["status"] == "passed", report
            assert notebook.read_bytes() == original_notebook
            assert (recipe_consumer / "uv.lock").read_bytes() == lock
            assert (recipe_consumer / ".venv/share/jupyter/kernels/research-repo-tools/kernel.json").is_file()
            # A real locked tooling group must start before the consumer's native
            # build backend exists. A full installation of this project would fail.
            setup_consumer = consumer / "setup consumer"
            setup_consumer.mkdir()
            uv_version = run([uv, "--version"], cwd=consumer, env=local_env).split()[1]
            (setup_consumer / ".python-version").write_text("3.14\n", encoding="utf-8")
            (setup_consumer / "pyproject.toml").write_text(
                '[project]\nname="setup-consumer"\nversion="0.1.0"\nrequires-python=">=3.14"\n'
                '[build-system]\nrequires=[]\nbuild-backend="intentionally_missing_native_backend"\n'
                f'[dependency-groups]\ntooling=["research-repo-tools=={version}"]\ndev=[{{include-group="tooling"}}]\n'
                f'[tool.uv]\nrequired-version="=={uv_version}"\ndefault-groups=[]\ncache-keys=[{{file="pyproject.toml"}}, {{file="Cargo.toml"}}]\n'
                f"[tool.uv.sources]\nresearch-repo-tools={{path={json.dumps(str(artifact.resolve()))}}}\n",
                encoding="utf-8",
            )
            # The artifact path is an isolated pre-publication test fixture only.
            setup_env = {key: value for key, value in local_env.items() if key != "VIRTUAL_ENV"}
            run([uv, "lock", "--python", str(python)], cwd=setup_consumer, env=setup_env)
            setup = [uv, "run", "--locked", "--only-group", "tooling", "research-repo-tools", "setup"]
            assert "setup" in run([*setup, "--help"], cwd=setup_consumer, env=setup_env)
            # Exercise the installed command's prerequisite failure without
            # installing user tools or modifying shell startup files.
            setup_scripts = setup_consumer / ".venv" / ("Scripts" if os.name == "nt" else "bin")
            prerequisite_env = {**setup_env, "PATH": str(setup_scripts)}
            result = subprocess.run(setup, cwd=setup_consumer, env=prerequisite_env, capture_output=True, encoding="utf-8", timeout=TIMEOUT)
            assert result.returncode == 1, result.stderr
            assert "must be installed and available on PATH" in result.stderr, result.stderr
            assert not (setup_consumer / "scripts").exists()
            check_update_bootstrap(setup_consumer, uv, just, setup_env)
            check_python_update(consumer / "update consumer", artifact, version, uv, just, env)
            print(f"PASS: installed {name} outside checkout; bundled just")


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
