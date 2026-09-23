"""Installed CLI checks with native validators and small consumer fixtures."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from research_repo_tools.cli import main


def template(name: str) -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(["templates", name]) == 0
    return output.getvalue()


class TestPythonPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="python validation ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = template("python-validation.toml") + '\n[tool.ruff.lint.per-file-ignores]\n"tests/semgrep/negative.py"=["ANN001", "ANN201"]\n'
        self.write("pyproject.toml", self.policy)
        self.write("support.py", "def increment(value: int) -> int:\n    return value + 1\n")
        self.write("tests/test_consumer.py", "def test_example() -> None:\n    assert 1 + 1 == 2\n")
        self.write("tests/semgrep/negative.py", "def missing_annotation(value):\n    return value\n")
        self.write("tests/semgrep/typed.py", 'count: int = "deliberate"  # ty: ignore[invalid-assignment]\n')
        self.write("types.pyi", "def value() -> int: ...\n")
        self.commands: list[list[str]] = []
        self.diagnostics: list[str] = []

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def check(self, tool: str) -> int:
        native = subprocess.run

        def inventory_or_native(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            if command[1:3] == ["--no-pager", "ls-files"]:
                # Model only Git's NUL inventory; execute the public file runner
                # and the native checker, with their actual consumer config.
                self.assertEqual(command[3:8], ["--cached", "--others", "--exclude-standard", "-z", "--"])
                self.assertEqual(command[8:], ["*.py", "*.pyi"])
                names = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.suffix in {".py", ".pyi"})
                return subprocess.CompletedProcess(command, 0, b"".join(name.encode() + b"\0" for name in names), b"")
            self.commands.append(list(command))
            options: dict[str, Any] = {**kwargs, "capture_output": True, "check": False}
            result = native(command, **options)
            self.diagnostics.append((result.stdout + result.stderr).decode("utf-8"))
            if kwargs.get("check"):
                result.check_returncode()
            return result

        args = [tool, "check", "--no-force-exclude", *(["--no-fix"] if tool == "ruff" else [])]
        with patch("subprocess.run", side_effect=inventory_or_native), contextlib.redirect_stderr(io.StringIO()):
            return main(["--root", str(self.root), "files", "run", "--include", "*.py", "--include", "*.pyi", "--", *args])

    def test_complete_inventory_and_precise_exceptions(self) -> None:
        for tool in ("ruff", "ty"):
            with self.subTest(tool=tool):
                self.assertEqual(self.check(tool), 0, "\n".join(self.diagnostics))
                self.assertIn("./tests/semgrep/negative.py", self.commands[-1])
                self.assertIn("./tests/semgrep/typed.py", self.commands[-1])
                self.assertIn("./support.py", self.commands[-1])
                self.assertIn("./tests/test_consumer.py", self.commands[-1])
                self.assertIn("./types.pyi", self.commands[-1])
                self.assertNotIn("--select", self.commands[-1])

    def test_new_python_files_and_annotation_only_imports_fail(self) -> None:
        self.write("new area/added.py", "def added(value):\n    return value\n")
        self.write("tests/semgrep/imports.py", "from pathlib import Path\n\n\ndef name(path: Path) -> str:\n    return path.name\n")
        self.write("tests/semgrep/quoted.py", 'def double(value: "int") -> int:\n    return value * 2\n')
        self.assertEqual(self.check("ruff"), 1)
        diagnostics = "\n".join(self.diagnostics)
        for code in ("ANN001", "ANN201", "TC003", "UP037"):
            self.assertIn(code, diagnostics)
        self.assertIn("./new area/added.py", self.commands[-1])

    def test_exception_does_not_hide_other_rules_or_other_files(self) -> None:
        self.write("tests/semgrep/negative.py", "def missing_annotation(value):\n    return undefined_name\n")
        self.write("tests/semgrep/another.py", "def unannotated(value):\n    return value\n")
        self.assertEqual(self.check("ruff"), 1)
        for code in ("F821", "ANN001", "ANN201"):
            self.assertIn(code, "\n".join(self.diagnostics))

    def test_type_suppression_does_not_hide_neighboring_errors(self) -> None:
        self.write("tests/semgrep/typed.py", 'count: int = "deliberate"  # ty: ignore[invalid-assignment]\nother: int = "unexpected"\n')
        self.assertEqual(self.check("ty"), 1)
        self.assertIn("invalid-assignment", "\n".join(self.diagnostics))
        self.assertIn("unexpected", "\n".join(self.diagnostics))

    def test_forced_directory_exclusions_cannot_drop_selected_fixtures(self) -> None:
        policy = self.policy.replace("[tool.ruff]\n", '[tool.ruff]\nforce-exclude=true\nexclude=["tests/semgrep/**"]\n')
        self.write("pyproject.toml", policy + '\n[tool.ty.src]\nexclude=["tests/semgrep/**"]\n')
        self.write("tests/semgrep/added.py", 'def added(value):\n    return value\n\ncount: int = "wrong"\n')
        for tool, rule in (("ruff", "ANN001"), ("ty", "invalid-assignment")):
            with self.subTest(tool=tool):
                self.assertEqual(self.check(tool), 1)
                self.assertIn(rule, "\n".join(self.diagnostics))

    def test_all_requested_missing_annotation_rules_are_active(self) -> None:
        self.write(
            "annotations.py",
            "def public(value, *args, **kwargs):\n    return value\n\n"
            "def _private():\n    return 1\n\n"
            "class Example:\n"
            "    def __init__(self):\n        pass\n"
            "    @staticmethod\n    def static():\n        return 1\n"
            "    @classmethod\n    def create(cls):\n        return cls()\n",
        )
        self.assertEqual(self.check("ruff"), 1)
        for code in ("ANN001", "ANN002", "ANN003", "ANN201", "ANN202", "ANN204", "ANN205", "ANN206"):
            self.assertIn(code, "\n".join(self.diagnostics))

    def test_python314_type_checking_import_needs_no_future_import(self) -> None:
        self.write(
            "annotations.py",
            "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from pathlib import Path\n\ndef name(path: Path) -> str:\n    return path.name\n",
        )
        for tool in ("ruff", "ty"):
            self.assertEqual(self.check(tool), 0, "\n".join(self.diagnostics))

    def test_packaged_gate_retains_fixture_validation(self) -> None:
        recipes = template("justfile")
        self.write("justfile", recipes)
        just = shutil.which("just")
        self.assertIsNotNone(just)
        result = subprocess.run([str(just), "--dry-run", "ci"], cwd=self.root, capture_output=True, text=True, check=True)
        for command in (
            "ruff check --no-fix --no-force-exclude",
            "ruff format --check --no-force-exclude",
            "ty check --no-force-exclude",
            "semgrep check-fixtures",
            "zizmor check",
        ):
            self.assertIn(command, result.stderr)


@unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "Git mutations disabled by agent policy")
class TestGitDiscovery(unittest.TestCase):
    def test_tracked_and_new_nonignored_python_files_are_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            (root / ".gitignore").write_bytes(b"ignored.py\n")
            (root / "tracked.py").write_bytes(b"")
            subprocess.run(["git", "add", "tracked.py"], cwd=root, check=True)
            (root / "nested").mkdir()
            (root / "nested/new.py").write_bytes(b"")
            (root / "ignored.py").write_bytes(b"")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--root", directory, "files", "list", "--include", "*.py"]), 0)
            self.assertEqual(output.getvalue().splitlines(), ["nested/new.py", "tracked.py"])


class TestOfflineAudit(unittest.TestCase):
    def test_installed_cli_produces_sarif_and_propagates_native_findings(self) -> None:
        # Real pinned scanner, offline; this is not evidence of online audits.
        scanner = shutil.which("zizmor")
        self.assertIsNotNone(scanner)
        version = subprocess.run([str(scanner), "--version"], capture_output=True, text=True, check=True).stdout.split()[1]
        with tempfile.TemporaryDirectory(prefix="audit consumer ") as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                f'[dependency-groups]\ndev=["zizmor=={version}"]\n[tool.research-repo-tools.zizmor]\npersona="regular"\n', encoding="utf-8", newline="\n"
            )
            workflow = root / ".github/workflows/unsafe.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_bytes(
                b"name: Test\non: pull_request\npermissions: {}\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                b"      - run: echo '${{ github.event.pull_request.title }}'\n"
            )
            command = [sys.executable, "-m", "research_repo_tools", "--root", directory, "zizmor", "check", "--offline"]
            plain = subprocess.run(command, capture_output=True, check=False)
            self.assertNotEqual(plain.returncode, 0)
            self.assertIn(b"template-injection", plain.stdout)
            self.assertIn(b"offline audits only", plain.stderr)
            sarif = subprocess.run([*command, "--format", "sarif"], capture_output=True, check=True)
            report = json.loads(sarif.stdout)
            self.assertEqual(report["version"], "2.1.0")
            self.assertTrue(report["runs"][0]["results"])
            self.assertIn(b"offline audits only", sarif.stderr)
            self.assertIn("required online", template("VALIDATING_WORKFLOWS.md"))


if __name__ == "__main__":
    unittest.main()
