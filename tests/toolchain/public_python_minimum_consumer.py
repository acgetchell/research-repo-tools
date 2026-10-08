"""Native installed-release adoption and independently installable applications."""

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.specifiers import SpecifierSet

from research_repo_tools.changelog import template
from research_repo_tools.python_baseline import baseline


class TestPythonMinimumConsumer(unittest.TestCase):
    def test_malformed_target_tables_have_installed_cli_diagnostics(self):
        authority = baseline()
        for invalid, field in (
            ('[tool]\nruff="invalid"\n', "tool.ruff"),
            ("[tool]\nty=[]\n", "tool.ty"),
            ("[tool.ty]\nenvironment=false\n", "tool.ty.environment"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "pyproject.toml").write_bytes(
                    (
                        f"[project]\nrequires-python={json.dumps(authority.minimum_requirement)}\n"
                        f'[dependency-groups]\ntooling=["research-repo-tools=={authority.package_version}"]\n'
                        + invalid
                        + "[tool.uv]\npackage=false\n[tool.research-repo-tools.toolchain]\ninherit-python=true\n"
                    ).encode()
                )
                (root / ".python-version").write_bytes(authority.selected.encode() + b"\n")
                before = {path.name: path.read_bytes() for path in root.iterdir()}
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-X", "utf8", "-m", "research_repo_tools", "--root", str(root), "toolchain", "python-check"],
                    capture_output=True,
                    encoding="utf-8",
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{field} must be a table", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})

    def test_malformed_group_constraint_has_installed_cli_diagnostic(self):
        authority = baseline()
        with tempfile.TemporaryDirectory(prefix="invalid Python constraint ") as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_bytes(
                (
                    '[project]\nname="invalid-constraint"\nversion="1.0.0"\n'
                    f"requires-python={json.dumps(authority.minimum_requirement)}\n"
                    f'[dependency-groups]\ntooling=["research-repo-tools=={authority.package_version}"]\n'
                    '[tool.uv]\npackage=true\ndependency-groups={tooling="invalid"}\n'
                    "[tool.research-repo-tools.toolchain]\ninherit-python=true\n"
                ).encode()
            )
            (root / ".python-version").write_bytes(authority.selected.encode() + b"\n")
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-X", "utf8", "-m", "research_repo_tools", "--root", str(root), "toolchain", "python-check"],
                capture_output=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("tool.uv.dependency-groups.tooling must be a table", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})

    def test_standalone_bootstrap_adoption_and_application_installers(self):
        if os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1":
            self.skipTest("disposable Git initialization disabled")
        registry = os.environ.get("RRT_TEST_REGISTRY")
        if registry is None:
            self.fail("installed checks must supply the exact artifact registry")
        authority = baseline()
        with tempfile.TemporaryDirectory(prefix="minimum consumer café ") as directory:
            root = Path(directory).resolve()
            consumer = root / "application"
            consumer.mkdir()
            env = {key: value for key, value in os.environ.items() if key not in {"UV_PROJECT_ENVIRONMENT", "UV_PROJECT", "UV_PYTHON", "VIRTUAL_ENV"}}
            env["RESEARCH_REPO_TOOLS_HOME"] = str(root / "managed tools")
            env["UV_PYTHON_INSTALL_DIR"] = str(root / "managed Python")

            def run(arguments, *, cwd=consumer, check=True):
                result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, encoding="utf-8", timeout=300, check=False)
                if check:
                    self.assertEqual(result.returncode, 0, f"{arguments}:\n{result.stdout}\n{result.stderr}")
                return result

            uv_version = run(["uv", "--version"]).stdout.split()[1]
            manifest = (
                '[build-system]\nrequires=["hatchling==1.32.4"]\nbuild-backend="hatchling.build"\n'
                '[project]\nname="minimum-consumer"\nversion="1.0.0"\nrequires-python=">=3.12,<4,!=3.14.1"\n'
                f'[dependency-groups]\ntooling=["research-repo-tools=={authority.package_version}"]\n'
                f'[tool.uv]\nrequired-version="=={uv_version}"\nfind-links=[{json.dumps(registry)}]\n'
                '[tool.hatch.build.targets.wheel]\npackages=["minimum_consumer"]\n'
                "[tool.research-repo-tools.toolchain]\ninherit-python=false\n"
            )
            (consumer / "pyproject.toml").write_bytes(manifest.encode())
            (consumer / ".python-version").write_bytes(b"3.13\n")
            source = consumer / "minimum_consumer/__init__.py"
            source.parent.mkdir()
            source.write_bytes(b'CONTRACT = "unchanged scientific application"\n')
            (consumer / "python-bootstrap.py").write_bytes(template("python-bootstrap.py").encode())
            (consumer / "justfile").write_bytes(template("justfile").encode())
            metadata_file = root / "exact release.json"
            metadata_file.write_bytes(
                json.dumps({"info": {"name": "research-repo-tools", "version": authority.package_version, "requires_python": authority.requirement}}).encode()
            )
            env["UV_FIND_LINKS"] = registry
            run(["git", "init"])
            run(["git", "add", "."])

            def git_snapshot():
                return {path.relative_to(consumer / ".git").as_posix(): path.read_bytes() for path in (consumer / ".git").rglob("*") if path.is_file()}

            git_before = git_snapshot()
            before = {name: (consumer / name).read_bytes() for name in ("pyproject.toml", ".python-version", "minimum_consumer/__init__.py")}
            # Supply a genuinely older ambient interpreter. uvx must ignore the
            # consumer selector and resolve the installed release's own support.
            run(["uv", "venv", "--managed-python", "--python", "3.13", str(root / "old Python")])
            scripts = "Scripts" if os.name == "nt" else "bin"
            executable = "python.exe" if os.name == "nt" else "python"
            old_python = root / "old Python" / scripts / executable
            env["PATH"] = str(old_python.parent) + os.pathsep + env["PATH"]
            self.assertTrue(run([str(old_python), "--version"]).stdout.startswith("Python 3.13."))
            bootstrap = [
                "uvx",
                "--no-config",
                "--isolated",
                "--managed-python",
                "--python",
                authority.requirement,
                "--find-links",
                registry,
                "--from",
                f"research-repo-tools=={authority.package_version}",
                "research-repo-tools",
            ]
            stale = run([*bootstrap, "toolchain", "python-check"], check=False)
            self.assertEqual(stale.returncode, 1)
            self.assertIn("project.requires-python", stale.stderr)
            self.assertIn("toolchain adopt --dry-run", stale.stderr)
            # Run the published helper under the old interpreter itself, and
            # through uv's isolated script path used by the Just recipes.
            helper = [str(old_python), str(consumer / "python-bootstrap.py"), authority.package_version, "--metadata-file", str(metadata_file)]
            script = [
                "uv",
                "run",
                "--no-config",
                "--no-project",
                "--isolated",
                "--managed-python",
                "--script",
                "python-bootstrap.py",
                authority.package_version,
                "--metadata-file",
                str(metadata_file),
            ]
            preview = run([*helper, "--dry-run"])
            self.assertIn("Would update: pyproject.toml", preview.stdout)
            self.assertEqual(before, {name: (consumer / name).read_bytes() for name in before})
            self.assertFalse((consumer / ".venv").exists())
            self.assertFalse((consumer / "uv.lock").exists())
            self.assertEqual(git_snapshot(), git_before)
            run([*script, "--apply"])
            document = tomllib.loads((consumer / "pyproject.toml").read_bytes().decode())
            requirement = document["project"]["requires-python"]
            self.assertNotIn("3.13.99", SpecifierSet(requirement))
            self.assertIn("3.14.2", SpecifierSet(requirement))
            self.assertNotIn("3.14.1", SpecifierSet(requirement))
            self.assertNotIn("4.0", SpecifierSet(requirement))
            self.assertEqual(source.read_bytes(), before["minimum_consumer/__init__.py"])
            self.assertEqual(git_snapshot(), git_before)
            run([*bootstrap, "toolchain", "python-check"])
            applied = {name: (consumer / name).read_bytes() for name in ("pyproject.toml", ".python-version", "uv.lock")}
            environment = consumer / ".venv"
            environment_before = {
                path.relative_to(environment).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in environment.rglob("*") if path.is_file()
            }
            preview = run([*script, "--dry-run"])
            self.assertNotIn("Would update:", preview.stdout)
            run([*helper, "--apply"])
            self.assertEqual(applied, {name: (consumer / name).read_bytes() for name in applied})
            self.assertEqual(git_snapshot(), git_before)
            self.assertEqual(
                environment_before,
                {path.relative_to(environment).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in environment.rglob("*") if path.is_file()},
            )

            run(["uv", "build", "--out-dir", str(root / "distributions")])
            artifacts = sorted(path for path in (root / "distributions").iterdir() if path.name.endswith((".whl", ".tar.gz")))
            self.assertEqual(len(artifacts), 2)
            for artifact in artifacts:
                with self.subTest(artifact=artifact.name):
                    if artifact.suffix == ".whl":
                        with zipfile.ZipFile(artifact) as archive:
                            data = archive.read(next(name for name in archive.namelist() if name.endswith("/METADATA")))
                    else:
                        with tarfile.open(artifact) as archive:
                            member = next(item for item in archive.getmembers() if item.name.endswith("/PKG-INFO"))
                            stream = archive.extractfile(member)
                            if stream is None:
                                self.fail("sdist PKG-INFO is not a regular file")
                            with stream:
                                data = stream.read()
                    metadata = BytesParser().parsebytes(data)
                    self.assertEqual(SpecifierSet(metadata["Requires-Python"]), SpecifierSet(requirement))
                    self.assertIsNone(metadata.get("Requires-Dist"), "application acquired a shared-tool runtime dependency")
                    rejected = run(["uv", "pip", "install", "--python", str(old_python), "--no-deps", str(artifact)], check=False)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("current Python version", rejected.stderr)
                    self.assertIn("minimum-consumer", rejected.stderr)
                    independent = root / ("independent " + artifact.suffix)
                    run(["uv", "venv", "--managed-python", "--python", authority.selected, str(independent)])
                    python = independent / scripts / executable
                    run(["uv", "pip", "install", "--python", str(python), "--no-deps", str(artifact)])
                    result = run(
                        [
                            str(python),
                            "-I",
                            "-X",
                            "utf8",
                            "-c",
                            "import importlib.util, minimum_consumer; assert minimum_consumer.CONTRACT == 'unchanged scientific application'; assert importlib.util.find_spec('research_repo_tools') is None",
                        ]
                    )
                    self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
