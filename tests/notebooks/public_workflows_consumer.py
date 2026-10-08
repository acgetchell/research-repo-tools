"""Public launch/reset contracts for native checkout and isolated installations."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cli import main
from research_repo_tools.config import load, parse
from research_repo_tools.notebook_workflows import launch, reset
from research_repo_tools.process import run_command, run_git_bytes


class Consumer(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workflow consumer 分析 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--root", str(self.root), "notebooks", *args])
        return status, out.getvalue(), err.getvalue()


class TestLaunch(Consumer):
    def test_windows_reserved_scratch_names_fail_before_sync(self):
        if os.name != "nt":
            self.skipTest("native Windows path validation")
        for scratch in ("CON", "aux.txt", "file:stream", "tail.", "tail ", "new\nline"):
            with self.subTest(scratch=scratch), self.assertRaisesRegex(ValueError, "reserved on Windows"):
                launch(parse({}, root=self.root), scratch_dir=scratch)
        self.assertFalse((self.root / "target").exists())

    def test_launch_rejects_symlink_environment_overrides_before_sync(self):
        target = self.root / "environment"
        target.mkdir()
        alias = self.root / "alias"
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"native symlink privilege unavailable: {error}")
        with patch.dict(os.environ, {"UV_PROJECT_ENVIRONMENT": "alias"}):
            with self.assertRaisesRegex(ValueError, "symlink or junction"):
                launch(parse({}, root=self.root))
        self.assertTrue(alias.is_symlink())
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse((self.root / "target").exists())

    def test_launch_rejects_external_project_environments_before_sync(self):
        for environment in (str(self.root.parent / "outside-env"), str(self.root)):
            with self.subTest(environment=environment), patch.dict(os.environ, {"UV_PROJECT_ENVIRONMENT": environment}):
                with self.assertRaisesRegex(ValueError, "below the consumer root"):
                    launch(parse({}, root=self.root))
        self.assertFalse((self.root / "target").exists())

    def test_launch_cannot_use_the_managed_tool_cache_as_scratch(self):
        cache = self.root / "managed-tools"
        cache.mkdir()
        sentinel = cache / "tool"
        sentinel.write_bytes(b"managed tool")
        with patch.dict(os.environ, {"RESEARCH_REPO_TOOLS_HOME": str(cache)}):
            with self.assertRaisesRegex(ValueError, "protected project path"):
                launch(parse({}, root=self.root), scratch_dir="managed-tools")
        self.assertEqual(sentinel.read_bytes(), b"managed tool")

    def test_locked_launch_uses_selected_group_browser_private_caches_and_project_interpreter(self):
        uv = run_command("uv", ["--version"]).stdout.split()[1]
        (self.root / "pyproject.toml").write_text(
            '[project]\nname="launch-fixture"\nversion="0.0.0"\nrequires-python=">=3.14"\n'
            "[dependency-groups]\ndev=[]\nanalysis=[]\n[tool.uv]\npackage=false\n"
            f'required-version="=={uv}"\n[tool.research-repo-tools.notebooks]\ngroup="analysis"\n'
            '[tool.research-repo-tools.notebooks.lab]\nbrowser=true\nscratch-dir="scratch with spaces"\n',
            encoding="utf-8",
            newline="\n",
        )
        (self.root / ".python-version").write_bytes(b"3.14\n")
        # A stand-in server records the real uv/Python argv and native environment.
        # It requires no browser, socket, JupyterLab installation, or user settings.
        (self.root / "jupyterlab.py").write_text(
            "import json, os, sys\nfrom pathlib import Path\n"
            "keys = ('IPYTHONDIR', 'MPLCONFIGDIR', 'JUPYTER_CONFIG_DIR', 'JUPYTER_DATA_DIR', 'JUPYTER_RUNTIME_DIR')\n"
            "report = {'args': sys.argv[1:], 'prefix': sys.prefix, 'cwd': str(Path.cwd()), "
            "'caches': {k: os.environ[k] for k in keys}, 'group': os.environ.get('UV_PROJECT_ENVIRONMENT')}\n"
            "assert all(Path(p).is_dir() for p in report['caches'].values())\n"
            "Path('launch-report.json').write_text(json.dumps(report), encoding='utf-8', newline='\\n')\n"
            "raise SystemExit(int(os.environ.get('RRT_LAB_EXIT', '0')))\n",
            encoding="utf-8",
            newline="\n",
        )
        env = {key: value for key, value in os.environ.items() if not key.startswith("UV_") and key not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")}
        env["UV_PROJECT_ENVIRONMENT"] = "environment with spaces"
        run_command("uv", ["lock", "--managed-python"], cwd=self.root, env=env)
        run_command("uv", ["sync", "--locked", "--managed-python", "--group", "analysis"], cwd=self.root, env=env)
        original_lock = (self.root / "uv.lock").read_bytes()
        with patch.dict(os.environ, {**env, "UV_PROJECT": "/unrelated-project", "IPYTHONDIR": "parent-ipython"}, clear=True):
            before = dict(os.environ)
            self.assertEqual(launch(load(root=self.root)), 0)
            self.assertEqual(dict(os.environ), before)
            observed = json.loads((self.root / "launch-report.json").read_text(encoding="utf-8"))
            self.assertIn("--ServerApp.open_browser=True", observed["args"])
            self.assertEqual(Path(observed["prefix"]), self.root / "environment with spaces")
            self.assertEqual(Path(observed["cwd"]), self.root)
            self.assertEqual(Path(observed["group"]), self.root / "environment with spaces")
            for directory in observed["caches"].values():
                self.assertTrue(Path(directory).is_relative_to(self.root / "scratch with spaces"))
                self.assertFalse(Path(directory).exists())
            self.assertEqual((self.root / "uv.lock").read_bytes(), original_lock)
            with patch.dict(os.environ, {"RRT_LAB_EXIT": "7"}):
                status, _out, err = self.run_cli("launch", "--no-browser")
            self.assertEqual((status, err), (7, ""))
            observed = json.loads((self.root / "launch-report.json").read_text(encoding="utf-8"))
            self.assertIn("--ServerApp.open_browser=False", observed["args"])
        self.assertEqual(list((self.root / "scratch with spaces").iterdir()), [])


class TestReset(Consumer):
    def setUp(self):
        if os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1":
            self.skipTest("Git mutations disabled by RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS=1")
        super().setUp()
        self.git("init", "--template=")
        self.git("config", "user.name", "Notebook fixture")
        self.git("config", "user.email", "notebook@example.invalid")
        self.relative = "notebooks/analysis with spaces.ipynb"
        self.path = self.root / self.relative
        self.path.parent.mkdir()
        self.original = b"revision notebook\r\n"
        self.path.write_bytes(self.original)
        (self.root / "keep.txt").write_bytes(b"tracked other file\n")
        self.git("add", "--", self.relative, "keep.txt")
        self.git("commit", "-m", "fixture")
        self.revision = self.git("rev-parse", "HEAD").stdout.decode().strip()

    def git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        return run_git_bytes(
            ["--no-pager", "-c", "core.autocrlf=false", "-c", f"core.hooksPath={self.root / 'disabled-hooks'}", "-c", "commit.gpgsign=false", *args],
            cwd=self.root,
            env=env,
        )

    def test_preview_and_apply_restore_deleted_and_modified_index_notebooks_without_touching_index(self):
        self.path.write_bytes(b"staged notebook\r\n")
        self.git("add", "--", self.relative)
        index = self.git("ls-files", "--stage", "-z").stdout
        self.path.unlink()
        other = self.root / "keep.txt"
        other.write_bytes(b"local other file")
        untracked = self.path.parent / "untracked.ipynb"
        untracked.write_bytes(b"untracked source")
        checkpoint = self.path.parent / ".ipynb_checkpoints"
        checkpoint.mkdir()
        (checkpoint / "old.ipynb").write_bytes(b"checkpoint")
        scratch = self.root / "scratch with spaces"
        scratch.mkdir()
        (scratch / "report.json").write_bytes(b"artifact")
        settings = parse(
            {"notebooks": {"reset": {"sources": ["notebooks"], "scratch": [scratch.name], "checkpoints": ["notebooks/.ipynb_checkpoints"]}}}, root=self.root
        )
        plan = reset(settings)
        self.assertEqual(plan.sources, (self.path,))
        self.assertFalse(self.path.exists())
        self.assertTrue(checkpoint.is_dir() and scratch.is_dir())
        reset(settings, apply=True)
        self.assertEqual(self.path.read_bytes(), b"staged notebook\r\n")
        self.assertEqual(self.git("ls-files", "--stage", "-z").stdout, index)
        self.assertFalse(checkpoint.exists() or scratch.exists())
        self.assertEqual(other.read_bytes(), b"local other file")
        self.assertEqual(untracked.read_bytes(), b"untracked source")

    def test_revision_restore_pins_the_tree_and_preserves_index_and_unselected_sources(self):
        self.path.write_bytes(b"staged replacement")
        self.git("add", "--", self.relative)
        index = self.git("ls-files", "--stage", "-z").stdout
        self.path.write_bytes(b"local replacement")
        plan = reset(parse({}, root=self.root), [Path(self.relative)], revision=self.revision, apply=True)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(plan.revision, self.git("rev-parse", "HEAD^{tree}").stdout.decode().strip())
        self.assertEqual(self.git("ls-files", "--stage", "-z").stdout, index)

    def test_literal_git_magic_spaces_and_native_newline_names(self):
        names = ["notebooks/[literal].ipynb", "notebooks/colon : name.ipynb"] if os.name != "nt" else ["notebooks/[literal].ipynb"]
        if os.name != "nt":
            names.append("notebooks/new\nline.ipynb")
        for name in names:
            (self.root / name).write_bytes(b"literal original\r\n")
        self.git("add", "--", *[":(literal)" + name for name in names])
        for name in names:
            (self.root / name).unlink()
        reset(parse({}, root=self.root), [Path("notebooks")], apply=True)
        for name in names:
            self.assertEqual((self.root / name).read_bytes(), b"literal original\r\n")

    def test_declared_cleanup_cannot_delete_tracked_files_or_alias_sources(self):
        for cleanup in ("notebooks", self.relative, "keep.txt", ".git", ".", "../outside"):
            with self.subTest(cleanup=cleanup):
                settings = parse({"notebooks": {"reset": {"sources": ["notebooks"], "scratch": [cleanup]}}}, root=self.root)
                with self.assertRaises(ValueError):
                    reset(settings, apply=True)
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual((self.root / "keep.txt").read_bytes(), b"tracked other file\n")


if __name__ == "__main__":
    unittest.main()
