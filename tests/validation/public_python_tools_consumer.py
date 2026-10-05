"""Public CLI contract against each installed distribution and its tool extra."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from importlib.metadata import requires, version
from pathlib import Path
from unittest.mock import patch

from packaging.requirements import Requirement

from research_repo_tools.cli import main


class TestPythonTools(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="Python tools café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pins = {}
        for raw in requires("research-repo-tools") or []:
            item = Requirement(raw)
            if item.marker and item.marker.evaluate({"extra": "python-tools"}) and item.name in {"ruff", "ty", "pytest"}:
                self.pins[item.name] = next(iter(item.specifier)).version
        self.assertEqual(set(self.pins), {"ruff", "ty", "pytest"})
        self.manifest = (
            '[project]\nname="consumer"\nversion="1.0"\nrequires-python=">=3.12"\n'
            f'[dependency-groups]\ntooling=["research-repo-tools[python-tools]=={version("research-repo-tools")}"]\n'
            'dev=[{include-group="tooling"}, "ruff", "ty", "pytest"]\n'
            "[tool.research-repo-tools.toolchain]\ninherit-python-tools=true\n"
        )
        (self.root / "pyproject.toml").write_bytes(self.manifest.encode())
        self.lock = "\n".join(
            f'[[package]]\nname="{name}"\nversion="{pin}"\n' for name, pin in {"research-repo-tools": version("research-repo-tools"), **self.pins}.items()
        )
        (self.root / "uv.lock").write_bytes(self.lock.encode())

    def check(self) -> tuple[int, str]:
        diagnostics = io.StringIO()
        with contextlib.redirect_stderr(diagnostics):
            status = main(["--root", str(self.root), "toolchain", "python-tools-check"])
        return status, diagnostics.getvalue()

    def snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_installed_profile_executes_exact_native_versions_without_changes(self) -> None:
        before = self.snapshot()
        self.assertEqual(self.check(), (0, ""))
        self.assertEqual(self.snapshot(), before)
        for name, pin in self.pins.items():
            self.assertEqual(version(name), pin)

    def test_drift_is_actionable_and_read_only(self) -> None:
        for contents, file, expected in (
            (self.manifest.replace('"ruff"', '"ruff==0.1"'), "pyproject.toml", "competes"),
            (self.manifest.replace("[python-tools]", ""), "pyproject.toml", "must request"),
            (self.manifest.replace(f"=={version('research-repo-tools')}", "==0.0.1"), "pyproject.toml", "must request"),
            (self.lock.replace(f'version="{self.pins["ruff"]}"', 'version="0.1"'), "uv.lock", "must resolve"),
            (self.lock.replace(f'version="{version("research-repo-tools")}"', 'version="0.0.1"'), "uv.lock", "must resolve research-repo-tools"),
        ):
            with self.subTest(expected=expected):
                (self.root / "pyproject.toml").write_bytes(self.manifest.encode())
                (self.root / "uv.lock").write_bytes(self.lock.encode())
                (self.root / file).write_bytes(contents.encode())
                before = self.snapshot()
                status, diagnostics = self.check()
                self.assertEqual(status, 1)
                self.assertIn(expected, diagnostics)
                self.assertIn("toolchain adopt --dry-run", diagnostics)
                self.assertEqual(self.snapshot(), before)

    def test_malformed_tables_and_lock_records_fail_cleanly_without_changes(self) -> None:
        for file, contents, expected in (
            ("pyproject.toml", self.manifest + '[tool.uv]\nconstraint-dependencies="ruff"\n', "tool.uv.constraint-dependencies"),
            ("pyproject.toml", self.manifest + "[tool.uv]\nsources=[]\n", "tool.uv.sources"),
            ("pyproject.toml", self.manifest + '[project.optional-dependencies]\ntest="pytest"\n', "project.optional-dependencies.test"),
            ("pyproject.toml", self.manifest.replace('[project]\nname="consumer"', 'project=[]\n[unrelated]\nname="consumer"'), "project"),
            ("pyproject.toml", "dependency-groups=[]\n" + self.manifest.replace("[dependency-groups]", "[unrelated-groups]"), "dependency-groups"),
            ("uv.lock", "package=1\n", "uv.lock package"),
            ("uv.lock", "package=[1]\n", "uv.lock package"),
            ("uv.lock", '[[package]]\nversion="1"\n', "uv.lock package"),
            ("uv.lock", "[[package]]\nname=1\n", "uv.lock package"),
            ("uv.lock", '[[package]]\nname="ruff"\nversion=[]\n', "uv.lock package"),
        ):
            with self.subTest(contents=contents):
                (self.root / "pyproject.toml").write_bytes(self.manifest.encode())
                (self.root / "uv.lock").write_bytes(self.lock.encode())
                (self.root / file).write_bytes(contents.encode())
                before = self.snapshot()
                status, diagnostics = self.check()
                self.assertEqual(status, 1)
                self.assertIn(expected, diagnostics)
                self.assertIn("toolchain adopt --dry-run", diagnostics)
                self.assertEqual(self.snapshot(), before)

    def test_path_drift_cannot_pass_based_only_on_installed_metadata(self) -> None:
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"pytest 0.1\n", b"")):
            status, diagnostics = self.check()
        self.assertEqual(status, 1)
        self.assertIn("PATH must select pytest", diagnostics)

    def test_adoption_rejects_malformed_groups_before_resolution(self) -> None:
        (self.root / "pyproject.toml").write_bytes(b"dependency-groups=[]\n[tool.research-repo-tools.toolchain]\ninherit-python-tools=true\n")
        before = self.snapshot()
        diagnostics = io.StringIO()
        with patch("subprocess.run", side_effect=AssertionError("malformed manifest reached a subprocess")), contextlib.redirect_stderr(diagnostics):
            status = main(["--root", str(self.root), "toolchain", "adopt", "--dry-run"])
        self.assertEqual(status, 1)
        self.assertIn("dependency-groups must be a table", diagnostics.getvalue())
        self.assertEqual(self.snapshot(), before)

    def test_public_runtime_constraints_and_uv_overrides_remain_owned_by_consumer(self) -> None:
        for extra in (
            '[tool.uv]\nconstraint-dependencies=["ruff<1"]\n',
            '[tool.uv]\noverride-dependencies=["ty==0.1"]\n',
            '[tool.uv.sources]\npytest={path="custom-pytest"}\n',
            '[project.optional-dependencies]\ntest=["pytest>=8"]\n',
        ):
            with self.subTest(extra=extra):
                (self.root / "pyproject.toml").write_bytes((self.manifest + extra).encode())
                before = self.snapshot()
                self.assertEqual(self.check()[0], 1)
                self.assertEqual(before, self.snapshot())

    def test_opt_out_leaves_tool_ownership_to_consumer(self) -> None:
        (self.root / "pyproject.toml").write_bytes(self.manifest.replace("inherit-python-tools=true", "inherit-python-tools=false").encode())
        (self.root / "uv.lock").unlink()
        with patch("subprocess.run", side_effect=AssertionError("opt-out ran a command")):
            self.assertEqual(self.check(), (0, ""))

    def test_adoption_resolves_and_syncs_real_candidate_before_publication(self) -> None:
        # The install checker supplies a local registry with the tested artifact.
        # Only Git inventory is modeled. Resolution, candidate installation,
        # final environment sync, and CLI tool-version checks run natively.
        import os

        registry = os.environ.get("RRT_TEST_REGISTRY")
        if registry is None:
            self.skipTest("real adoption resolution runs in the installed wheel/sdist checks")
        uv_version = subprocess.run(["uv", "--version"], capture_output=True, check=True, text=True).stdout.split()[1]
        manifest = self.manifest.replace(
            "[tool.research-repo-tools.toolchain]",
            f'[tool.uv]\nrequired-version="=={uv_version}"\npackage=false\nfind-links=[{json.dumps(registry)}]\n[tool.research-repo-tools.toolchain]',
        )
        manifest = manifest.replace('"ruff"', '"ruff==0.1.0"')
        manifest = manifest.replace("[tool.research-repo-tools.toolchain]\ninherit-python-tools=true\n", "")
        (self.root / "pyproject.toml").write_bytes(manifest.encode())
        (self.root / ".python-version").write_bytes(b"3.14\n")
        settings = self.root / "settings.toml"
        settings.write_bytes(b"[toolchain]\ninherit-python-tools=true\n")
        command = ["--root", str(self.root), "--config", str(settings), "toolchain", "adopt"]
        (self.root / "uv.lock").unlink()
        native = subprocess.run

        def run(command, **kwargs):
            if command[1:3] == ["--no-pager", "ls-files"]:
                names = (".python-version", "pyproject.toml", "settings.toml", *(["uv.lock"] if (self.root / "uv.lock").exists() else []))
                return subprocess.CompletedProcess(command, 0, b"".join(name.encode() + b"\0" for name in names), b"")
            return native(command, **kwargs)

        before = self.snapshot()
        output = io.StringIO()
        with patch("subprocess.run", side_effect=run), contextlib.redirect_stdout(output):
            status = main([*command, "--dry-run"])
        self.assertEqual(status, 0)
        self.assertIn("--- pyproject.toml", output.getvalue())
        self.assertIn("+++ uv.lock", output.getvalue())
        self.assertEqual(before, self.snapshot())
        with patch("subprocess.run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([*command, "--apply"]), 0)
        self.assertTrue((self.root / ".venv/pyvenv.cfg").is_file())
        after = {name: (self.root / name).read_bytes() for name in ("pyproject.toml", ".python-version", "uv.lock")}
        self.assertNotIn(b"ruff==0.1.0", after["pyproject.toml"])
        self.assertIn(b'requires-python=">=3.12"', after["pyproject.toml"])
        self.assertEqual(after[".python-version"], b"3.14\n")
        with patch("subprocess.run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([*command, "--apply"]), 0)
        self.assertEqual(after, {name: (self.root / name).read_bytes() for name in after})


if __name__ == "__main__":
    unittest.main()
