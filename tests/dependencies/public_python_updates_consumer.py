"""Real uv source-policy and monotonic update contracts for installed consumers."""

import io
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import cli, dependencies, python_updates
from research_repo_tools.process import run_command_live


def wheel(directory: Path, name: str, version: str, requires: tuple[str, ...] = (), *, requires_python: str = ">=3.15") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = name.replace("-", "_") + "-" + version
    path = directory / (stem + "-py3-none-any.whl")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            stem + ".dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\nRequires-Python: {requires_python}\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requires),
        )
        archive.writestr(stem + ".dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(stem + ".dist-info/RECORD", "")
    return path


class TestPythonUpdates(unittest.TestCase):
    @classmethod
    @override
    def setUpClass(cls):
        # CI may run the suite on system Python even when setup-uv has selected
        # a managed installation directory. Establish the offline prerequisite.
        cls.interpreter_environment = {
            key: value for key, value in os.environ.items() if (not key.startswith("UV_") or key == "UV_PYTHON_INSTALL_DIR") and key != "VIRTUAL_ENV"
        }
        find = ["uv", "python", "find", "--no-config", "--no-project", "--system", "--managed-python", "--offline", "3.15"]
        found = subprocess.run(find, env=cls.interpreter_environment, capture_output=True, text=True, check=False, timeout=30)
        if found.returncode:
            temporary = tempfile.TemporaryDirectory(prefix="update managed Python ")
            cls.addClassCleanup(temporary.cleanup)
            cls.interpreter_environment["UV_PYTHON_INSTALL_DIR"] = str(Path(temporary.name).resolve() / "python")
            subprocess.run(
                ["uv", "python", "install", "--no-config", "--no-bin", "--no-registry", "3.15"],
                env=cls.interpreter_environment,
                capture_output=True,
                text=True,
                check=True,
                timeout=300,
            )
            found = subprocess.run(find, env=cls.interpreter_environment, capture_output=True, text=True, check=True, timeout=30)
        cls.wrapper_python = found.stdout.strip()

    @override
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="python update café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "consumer"
        self.project.mkdir()
        self.wheels = self.root / "wheels"
        wheel(self.wheels, "fixture-tool", "1.0")
        self.env = self.interpreter_environment.copy()
        self.env.update(UV_CACHE_DIR=str(self.root / "cache"), UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never", UV_PYTHON=self.wrapper_python)
        uv_version = self.uv("--version").stdout.split()[1]
        self.manifest = self.project / "pyproject.toml"
        self.manifest.write_text(
            '[project]\nname="consumer"\nversion="0.1"\nrequires-python=">=3.15"\n'
            '[dependency-groups]\ndev=["fixture-tool==1.0", {include-group="tooling"}]\ntooling=["fixture-tool<3"]\n'
            f'[tool.uv]\nrequired-version="=={uv_version}"\nno-index=true\nfind-links=["../wheels"]\n',
            encoding="utf-8",
            newline="\n",
        )
        (self.project / ".python-version").write_text("3.15\n", encoding="utf-8", newline="\n")
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def uv(self, *args: str):
        result = subprocess.run(
            ["uv", *args, "--directory", str(self.project), "--project", str(self.project)],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def preview(self):
        constraints, pins = dependencies.parse_project(self.manifest.read_text(encoding="utf-8"))
        return dependencies.resolve_latest_pins(pins, constraints, self.project, env=self.env)

    def test_override_policy_keeps_new_tool_compatible_and_preview_read_only(self):
        wheel(self.wheels, "fixture-tool", "2.0", ("fixture-lib==1.0",))
        wheel(self.wheels, "fixture-lib", "2.0")
        with self.manifest.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write('override-dependencies=["fixture-lib==2.0"]\nconstraint-dependencies=["fixture-tool<3"]\n')
        before = self.manifest.read_bytes()
        self.assertEqual(self.preview(), [dependencies.DevPin("fixture-tool", "2.0")])
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse((self.project / "uv.lock").exists())
        self.assertFalse((self.project / ".venv").exists())
        self.assertEqual(dependencies.update_dev_pins(self.manifest, env=self.env), {"fixture-tool": ("1.0", "2.0")})
        self.uv("sync", "--locked", "--group", "dev")
        locked = tomllib.loads((self.project / "uv.lock").read_text(encoding="utf-8"))
        self.assertIn(("fixture-lib", "2.0"), [(p["name"], p["version"]) for p in locked["package"]])

    def test_explicit_marker_split_indexes_preserve_runtime_sources(self):
        wheel(self.wheels, "fixture-tool", "2.0")
        # A higher dev-tool version in explicit runtime indexes must stay isolated.
        for index in ("cpu", "cuda"):
            directory = self.root / index
            for name, version in (("fixture-runtime", "2.15.0"), ("fixture-tool", "2.9")):
                package = directory / name
                path = wheel(package, name, version)
                (package / "index.html").write_text(f'<a href="{path.name}">{path.name}</a>\n', encoding="utf-8", newline="\n")
        source = self.manifest.read_text(encoding="utf-8").replace("[dependency-groups]", 'dependencies=["fixture-runtime==2.15.0"]\n[dependency-groups]')
        source = source.replace("no-index=true\n", "")
        source += (
            "[tool.uv.sources]\nfixture-runtime=["
            '{index="cpu", marker="sys_platform == \'darwin\'"},'
            '{index="cuda", marker="sys_platform != \'darwin\'"}]\n'
            + "".join(f'[[tool.uv.index]]\nname="{name}"\nurl={json.dumps((self.root / name).as_uri())}\nexplicit=true\n' for name in ("cpu", "cuda"))
        )
        self.manifest.write_text(source, encoding="utf-8", newline="\n")
        self.uv("lock")  # The original policy already supports ordinary resolution.
        before = self.manifest.read_bytes(), (self.project / "uv.lock").read_bytes()
        self.assertEqual(self.preview(), [dependencies.DevPin("fixture-tool", "2.0")])
        self.assertEqual((self.manifest.read_bytes(), (self.project / "uv.lock").read_bytes()), before)
        self.assertEqual(dependencies.update_dev_pins(self.manifest, env=self.env), {"fixture-tool": ("1.0", "2.0")})
        self.uv("sync", "--locked", "--group", "dev")
        after = tomllib.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(after["tool"]["uv"], tomllib.loads(source)["tool"]["uv"])
        lock = tomllib.loads((self.project / "uv.lock").read_text(encoding="utf-8"))
        runtime = [p for p in lock["package"] if p["name"] == "fixture-runtime"]
        self.assertEqual(len(runtime), 2)
        self.assertEqual({p["version"] for p in runtime}, {"2.15.0"})
        self.assertTrue(all(p["resolution-markers"] for p in runtime))

    def test_older_satisfiable_candidate_fails_without_writes(self):
        self.manifest.write_text(self.manifest.read_text(encoding="utf-8").replace("fixture-tool==1.0", "fixture-tool==2.0"), encoding="utf-8", newline="\n")
        before = self.manifest.read_bytes()
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            dependencies.update_dev_pins(self.manifest, env=self.env)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse((self.project / "uv.lock").exists())

    def test_absolute_artifact_source_is_retained_while_registry_tools_upgrade(self):
        artifact = wheel(self.wheels, "fixed-tool", "1.0")
        wheel(self.wheels, "fixed-tool", "2.0")
        wheel(self.wheels, "fixture-tool", "2.0")
        source = self.manifest.read_text(encoding="utf-8").replace("dev=[", 'dev=["fixed-tool==1.0",')
        source += "[tool.uv.sources]\nfixed-tool={path=" + json.dumps(str(artifact)) + "}\n"
        self.manifest.write_text(source, encoding="utf-8", newline="\n")
        self.assertEqual(self.preview(), [dependencies.DevPin("fixture-tool", "2.0")])
        self.assertEqual(dependencies.update_dev_pins(self.manifest, env=self.env), {"fixture-tool": ("1.0", "2.0")})
        updated = tomllib.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(updated["dependency-groups"]["dev"][0], "fixed-tool==1.0")
        self.assertEqual(updated["tool"]["uv"]["sources"], {"fixed-tool": {"path": str(artifact)}})
        self.uv("sync", "--locked", "--group", "dev")
        self.assertEqual(self.installed_version(self.project, "fixed-tool"), "1.0")

    def test_transitive_downgrade_restores_manifest_and_lock(self):
        wheel(self.wheels, "fixture-lib", "2.0")
        wheel(self.wheels, "fixture-lib", "1.0")
        wheel(self.wheels, "fixture-tool", "1.0", ("fixture-lib==2.0",))
        self.uv("lock")
        before = self.manifest.read_bytes(), (self.project / "uv.lock").read_bytes()
        wheel(self.wheels, "fixture-tool", "2.0", ("fixture-lib==1.0",))
        with self.assertRaisesRegex(ValueError, "fixture-lib 2.0 -> 1.0"):
            dependencies.update_dev_pins(self.manifest, env=self.env)
        self.assertEqual((self.manifest.read_bytes(), (self.project / "uv.lock").read_bytes()), before)
        self.assertFalse((self.project / ".venv").exists())

    def independent_project(self):
        scanner = self.project / "tooling/scanner"
        scanner.mkdir(parents=True)
        (scanner / "pyproject.toml").write_text(
            '[project]\nname="scanner"\nversion="0.1"\nrequires-python=">=3.15"\ndependencies=["fixture-library>=1"]\n'
            '[tool.uv]\nno-index=true\nfind-links=["../../../wheels"]\n',
            encoding="utf-8",
            newline="\n",
        )
        with self.manifest.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write('[tool.research-repo-tools.deps]\npython-projects=["tooling/scanner"]\n')
        wheel(self.wheels, "fixture-library", "1.0")
        self.uv("sync", "--group", "dev")
        result = subprocess.run(["uv", "sync", "--project", str(scanner)], env=self.env, capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return scanner

    def all_projects(self):
        output, errors = io.StringIO(), io.StringIO()
        # Tool discovery is independent of resolution. Use the real uv lock and
        # sync processes, with no external managed Rust tools needed by wheels.
        with (
            patch.object(python_updates.toolchain.Runtime, "inspect", return_value=[]),
            patch.object(python_updates.toolchain.Runtime, "project_environment", return_value=self.env),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            result = cli.main(["--root", str(self.project), "deps", "update-python", "--all-projects"])
        return result, errors.getvalue()

    def installed_version(self, project: Path, name: str) -> str:
        python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        result = subprocess.run(
            [str(python), "-I", "-X", "utf8", "-c", "import importlib.metadata,sys; print(importlib.metadata.version(sys.argv[1]))", name],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return result.stdout.strip()

    def test_all_projects_upgrade_keeps_independent_requirements_and_environments(self):
        scanner = self.independent_project()
        original = (scanner / "pyproject.toml").read_bytes()
        wheel(self.wheels, "fixture-tool", "2.0")
        wheel(self.wheels, "fixture-library", "2.0")
        self.assertEqual(self.all_projects(), (0, ""))
        self.assertEqual((scanner / "pyproject.toml").read_bytes(), original)
        self.assertEqual(self.installed_version(self.project, "fixture-tool"), "2.0")
        self.assertEqual(self.installed_version(scanner, "fixture-library"), "2.0")

    def test_late_sync_failure_restores_all_projects_and_original_environments(self):
        scanner = self.independent_project()
        paths = [project / name for project in (self.project, scanner) for name in ("pyproject.toml", "uv.lock")]
        original = {path: path.read_bytes() for path in paths}
        wheel(self.wheels, "fixture-tool", "2.0")
        wheel(self.wheels, "fixture-library", "2.0")
        injected = False

        def sync_then_fail(command, args, **kwargs):
            nonlocal injected
            result = run_command_live(command, args, **kwargs)
            if args[0] == "sync" and kwargs["cwd"] == scanner and not injected:
                injected = True
                raise subprocess.CalledProcessError(23, [command, *args], stderr="injected late synchronization failure")
            return result

        with patch.object(python_updates, "run_command_live", side_effect=sync_then_fail):
            code, diagnostic = self.all_projects()
        self.assertEqual(code, 1)
        self.assertIn("injected late synchronization failure", diagnostic)
        self.assertEqual({path: path.read_bytes() for path in paths}, original)
        self.assertEqual(self.installed_version(self.project, "fixture-tool"), "1.0")
        self.assertEqual(self.installed_version(scanner, "fixture-library"), "1.0")
        self.assertEqual(self.all_projects(), (0, ""))  # Recovery permits a normal retry.

    def test_independent_lock_downgrade_rolls_back_earlier_root_changes(self):
        scanner = self.independent_project()
        # Establish an independently constrained transitive at the higher version.
        wheel(self.wheels, "fixture-lib", "2.0")
        wheel(self.wheels, "fixture-lib", "1.0")
        wheel(self.wheels, "fixture-library", "1.1", ("fixture-lib==2.0",))
        self.assertEqual(self.all_projects(), (0, ""))
        paths = [project / name for project in (self.project, scanner) for name in ("pyproject.toml", "uv.lock")]
        before = {path: path.read_bytes() for path in paths}
        wheel(self.wheels, "fixture-library", "2.0", ("fixture-lib==1.0",))
        wheel(self.wheels, "fixture-tool", "2.0")
        code, diagnostic = self.all_projects()
        self.assertEqual(code, 1)
        self.assertIn("fixture-lib 2.0 -> 1.0", diagnostic)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)
        self.assertEqual(self.installed_version(self.project, "fixture-tool"), "1.0")
        self.assertEqual(self.installed_version(scanner, "fixture-library"), "1.1")

    def test_nested_project_launches_ignore_inherited_environment_and_keep_interpreters_separate(self):
        scanner = self.project / "tooling/scanner"
        scanner.mkdir(parents=True)
        environment = {key: value for key, value in self.env.items() if key not in {"UV_PYTHON", "UV_PYTHON_DOWNLOADS", "UV_OFFLINE"}}
        find = ["uv", "python", "find", "--no-config", "--no-project", "--system", "--managed-python", "--offline", "3.14"]
        found = subprocess.run(find, env=environment, capture_output=True, text=True, check=False, timeout=30)
        if found.returncode:
            # Switching to a disposable managed store must retain both interpreters
            # required by the nested scanner and outer native-validation launches.
            environment["UV_PYTHON_INSTALL_DIR"] = str(self.root / "python")
            installed = subprocess.run(
                ["uv", "python", "install", "--no-config", "--no-bin", "--no-registry", "3.14", "3.15"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            found = subprocess.run(find, env=environment, capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(found.returncode, 0, found.stderr)
        scanner_python = found.stdout.strip()
        package = wheel(self.wheels, "fixture-scanner", "1.0", requires_python=">=3.14,<3.15")
        with zipfile.ZipFile(package, "a") as archive:
            archive.writestr("fixture_scanner-1.0.dist-info/entry_points.txt", "[console_scripts]\nfixture-scanner = fixture_scanner:main\n")
            archive.writestr(
                "fixture_scanner.py", "import json,sys\ndef main(): print(json.dumps({'prefix':sys.prefix,'version':list(sys.version_info[:2])}))\n"
            )
        (scanner / "pyproject.toml").write_text(
            '[project]\nname="scanner"\nversion="0.1"\nrequires-python=">=3.14,<3.15"\ndependencies=["fixture-scanner==1.0"]\n'
            '[tool.uv]\nno-index=true\nfind-links=["../../../wheels"]\n',
            encoding="utf-8",
            newline="\n",
        )
        environment.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
        self.uv("sync", "--group", "dev")
        synced = subprocess.run(
            ["uv", "sync", "--project", str(scanner), "--python", scanner_python], env=environment, capture_output=True, text=True, check=False, timeout=60
        )
        self.assertEqual(synced.returncode, 0, synced.stderr)
        probe = self.root / "probe.py"
        probe.write_text(
            "import json,shutil,subprocess,sys\n"
            "print(json.dumps({'prefix':sys.prefix,'version':list(sys.version_info[:2]),'scanner':shutil.which('fixture-scanner')}))\n"
            "subprocess.run(['fixture-scanner'],check=True)\n",
            encoding="utf-8",
            newline="\n",
        )
        environment["VIRTUAL_ENV"] = str(self.root / "unrelated active environment")
        args = [
            "uv",
            "run",
            "--locked",
            "--no-active",
            "--managed-python",
            "--project",
            str(scanner),
            "uv",
            "run",
            "--locked",
            "--no-active",
            "--project",
            str(self.project),
            "--group",
            "dev",
            "python",
            "-I",
            "-X",
            "utf8",
            str(probe),
        ]
        # Also cover a consumer's outer native-validation launch.
        for prefix in ([], ["uv", "run", "--locked", "--no-active", "--managed-python", "--project", str(self.project), "--group", "dev"]):
            with self.subTest(outer_native=bool(prefix)):
                result = subprocess.run([*prefix, *args], env=environment, capture_output=True, text=True, encoding="utf-8", check=False, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("VIRTUAL_ENV", result.stderr)
                self.assertNotIn("warning:", result.stderr.lower())
                records = [json.loads(line) for line in result.stdout.splitlines()]
                wrapper = next(record for record in records if "scanner" in record)
                native = next(record for record in records if "scanner" not in record)
                self.assertEqual(wrapper["version"], [3, 15])
                self.assertTrue(Path(wrapper["prefix"]).samefile(self.project / ".venv"))
                self.assertEqual(native["version"], [3, 14])
                self.assertTrue(Path(native["prefix"]).samefile(scanner / ".venv"))
                self.assertTrue(Path(wrapper["scanner"]).parent.samefile(scanner / ".venv" / ("Scripts" if os.name == "nt" else "bin")))


if __name__ == "__main__":
    unittest.main()
