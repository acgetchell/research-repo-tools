"""Public notebook integration helpers, also exercised after wheel/sdist install."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import override
from unittest.mock import patch

import psutil

from research_repo_tools.notebook_testing import isolated_project
from research_repo_tools.process import run_command


class TestNotebookProject(unittest.TestCase):
    @override
    def setUp(self):
        self.enterContext(warnings.catch_warnings())
        warnings.filterwarnings("error", message="Proactor event loop does not implement add_reader", category=RuntimeWarning)
        self.temporary = tempfile.TemporaryDirectory(prefix="notebook integration 分析 ")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.source = self.parent / "source"
        self.source.mkdir()
        uv = run_command("uv", ["--version"]).stdout.split()[1]
        manifest = (
            '[project]\nname="notebook-fixture"\nversion="0.0.0"\nrequires-python=">=3.15"\n'
            "[dependency-groups]\nanalysis=[]\n"
            f'[tool.uv]\nrequired-version="=={uv}"\n'
            '[tool.research-repo-tools.notebooks]\ngroup="analysis"\ntimeout=30\n'
            '[tool.research-repo-tools.toolchain.cargo]\ngit-cliff="2.14.1"\n'
            '[tool.research-repo-tools.toolchain.binaries]\ngitleaks="8.30.1"\n'
        )
        self.metadata = {"pyproject.toml": manifest.replace("\n", "\r\n").encode(), ".python-version": b"3.15\r\n", "uv.lock": b"version=1\r\n"}
        for name, data in self.metadata.items():
            (self.source / name).write_bytes(data)
        # Even an irrelevant/unavailable native toolchain must not enter the fixture.
        (self.source / "rust-toolchain.toml").write_bytes(b"not parsed by notebook fixture\n")
        self.relative = Path("notebooks/analysis with spaces.ipynb")
        (self.source / self.relative).parent.mkdir()

    def write_notebook(self, source):
        node = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {"kernelspec": {"name": "unavailable", "display_name": "Ignored", "language": "python"}},
            "cells": [{"cell_type": "code", "id": "integration-cell", "metadata": {}, "source": source, "outputs": [], "execution_count": None}],
        }
        data = (json.dumps(node, ensure_ascii=False) + "\r\n").encode("utf-8")
        (self.source / self.relative).write_bytes(data)
        return data

    def project(self, paths=None, **kwargs):
        return isolated_project(self.source, [self.relative] if paths is None else paths, parent=self.parent, environment=Path(sys.prefix), **kwargs)

    def test_real_kernel_borrows_interpreter_preserves_sources_and_returns_artifacts(self):
        original = self.write_notebook(
            "import json, os, sys\nfrom pathlib import Path\n"
            "assert 'prior_run' not in globals()\nprior_run = True\n"
            "assert 'RRT_TEST_REMOVE' not in os.environ\n"
            "print(json.dumps({'python': sys.executable, 'cwd': str(Path.cwd()), 'input': Path('input.bin').read_bytes().hex(), "
            "'pid': os.getpid(), "
            "'value': os.environ['RRT_TEST_VALUE'], 'backend': os.environ['MPLBACKEND'], "
            "'history': str(get_ipython().history_manager.hist_file), "
            "'history_files': [p.name for p in Path(os.environ['IPYTHONDIR']).rglob('history.sqlite*')]}))\n"
        )
        inputs = self.source / "work/input.bin"
        inputs.parent.mkdir()
        inputs.write_bytes(b"exact\r\n\x00\xff")
        with patch.dict(os.environ, {"RRT_TEST_REMOVE": "parent-value", "UV_PROJECT_ENVIRONMENT": "unrelated-environment"}):
            before = dict(os.environ)
            with self.project([self.relative, Path("work/input.bin")]) as project:
                workspace = project.root.parent
                self.assertFalse((project.root / "rust-toolchain.toml").exists())
                self.assertFalse((project.root / ".venv").exists())
                for name, content in self.metadata.items():
                    self.assertEqual((project.root / name).read_bytes(), content)
                for _ in range(2):
                    removed = "rrt_test_remove" if os.name == "nt" else "RRT_TEST_REMOVE"
                    result = project.execute(self.relative, cwd=Path("work"), env={removed: None, "RRT_TEST_VALUE": "分析 value"})
                    self.assertEqual(result.returncode, 0, result.report)
                    self.assertEqual(json.loads(result.report_path.read_bytes()), result.report)
                    self.assertEqual(result.report["source_sha256"], hashlib.sha256(original).hexdigest())
                    self.assertEqual(result.report["lock_sha256"], hashlib.sha256(self.metadata["uv.lock"]).hexdigest())
                    self.assertTrue(result.notebook_path.is_relative_to(project.artifacts))
                    output = json.loads(result.notebook_path.read_bytes())["cells"][0]["outputs"][0]["text"]
                    observed = json.loads(output)
                    self.assertEqual(Path(observed["python"]), Path(sys.executable))
                    self.assertEqual(Path(observed["cwd"]), project.root / "work")
                    self.assertEqual(observed["input"], inputs.read_bytes().hex())
                    self.assertEqual((observed["value"], observed["backend"]), ("分析 value", "Agg"))
                    self.assertEqual(observed["history"], ":memory:")
                    self.assertEqual(observed["history_files"], [])
                    self.assertFalse(psutil.pid_exists(observed["pid"]), "kernel process survived successful execution")
                    self.assertEqual(list(project.artifacts.glob("research-notebook-*")), [])
                    self.assertEqual(result.source.read_bytes(), original)
                self.assertEqual(dict(os.environ), before)
            self.assertFalse(workspace.exists())
            self.assertEqual(dict(os.environ), before)
            with self.assertRaisesRegex(ValueError, "context has ended"):
                project.execute(self.relative)
        self.assertEqual((self.source / self.relative).read_bytes(), original)
        self.assertEqual({name: (self.source / name).read_bytes() for name in self.metadata}, self.metadata)

    def test_failed_execution_and_exceptional_cleanup_preserve_parent(self):
        original = self.write_notebook(
            "import os\nfrom pathlib import Path\nPath('kernel.pid').write_bytes(str(os.getpid()).encode('ascii'))\n"
            "raise ValueError('consumer assertion failed')\n"
        )
        before = dict(os.environ)
        workspace = None
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with self.project() as project:
                workspace = project.root.parent
                result = project.execute(self.relative, env={"RRT_TEST_VALUE": "child-only"})
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.report["failed_cell"], {"index": 1, "id": "integration-cell"})
                self.assertIn("consumer assertion failed", result.report["error"]["message"])
                self.assertEqual(json.loads(result.report_path.read_bytes()), result.report)
                self.assertTrue(result.notebook_path.is_file())
                self.assertFalse(psutil.pid_exists(int((project.root / "kernel.pid").read_bytes())), "kernel process survived cell error")
                self.assertEqual(list(project.artifacts.glob("research-notebook-*")), [])
                raise RuntimeError("test failure")
        self.assertIsNotNone(workspace)
        assert workspace is not None
        self.assertFalse(workspace.exists())
        self.assertEqual(dict(os.environ), before)
        self.assertEqual((self.source / self.relative).read_bytes(), original)
        self.assertTrue(Path(sys.executable).is_file())

    def test_timeout_returns_failed_report(self):
        original = self.write_notebook(
            "import os, time\nfrom pathlib import Path\nPath('kernel.pid').write_bytes(str(os.getpid()).encode('ascii'))\ntime.sleep(10)\n"
        )
        with self.project() as project:
            result = project.execute(self.relative, timeout=1)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.report["error"]["type"], "CellTimeoutError")
            self.assertEqual(result.report["timeout"], 1)
            self.assertEqual(list(project.artifacts.glob("research-notebook-*")), [])
            self.assertEqual(result.source.read_bytes(), original)
            self.assertFalse(psutil.pid_exists(int((project.root / "kernel.pid").read_bytes())), "kernel process survived timeout")

    def test_sync_execution_inside_running_loop_preserves_caller_subprocess_support(self):
        original = self.write_notebook("print('fresh kernel inside caller loop')\n")

        async def caller():
            loop = asyncio.get_running_loop()
            with self.project() as project:
                result = project.execute(self.relative)
                self.assertEqual(result.returncode, 0, result.report)
                self.assertEqual(result.source.read_bytes(), original)
                self.assertEqual(list(project.artifacts.glob("research-notebook-*")), [])
            self.assertIs(asyncio.get_running_loop(), loop)
            # Windows' default Proactor loop must retain subprocess support;
            # the selector used for Jupyter belongs only to the execution scope.
            process = await asyncio.create_subprocess_exec(sys.executable, "-c", "print('caller loop intact')", stdout=asyncio.subprocess.PIPE)
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(stdout.strip(), b"caller loop intact")

        with asyncio.Runner(loop_factory=asyncio.EventLoop) as runner:
            runner.run(caller())

    def test_partial_kernel_startup_is_cleaned_before_returning_a_failed_report(self):
        from jupyter_client import AsyncKernelManager
        from jupyter_client.provisioning.local_provisioner import LocalProvisioner

        original = self.write_notebook("print('successful retry')\n")
        processes = []

        class FailedStartup(AsyncKernelManager):
            @override
            async def start_kernel(self, **kwargs):
                await super().start_kernel(**kwargs)
                assert isinstance(self.provisioner, LocalProvisioner) and self.provisioner.process is not None
                processes.append(self.provisioner.process)
                raise RuntimeError("failure after native kernel launch")

        with self.project() as project:
            with patch("jupyter_client.manager.AsyncKernelManager", FailedStartup):
                result = project.execute(self.relative)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.report["error"]["message"], "failure after native kernel launch")
            self.assertIsNone(result.report["failed_cell"])
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll(), "kernel survived partial startup")
            self.assertFalse(psutil.pid_exists(processes[0].pid))
            self.assertEqual(list(project.artifacts.glob("research-notebook-*")), [])
            self.assertEqual(result.source.read_bytes(), original)
            result = project.execute(self.relative)
            self.assertEqual(result.returncode, 0, result.report)

    def test_interrupted_submission_cleans_the_real_kernel_and_workspace(self):
        from jupyter_client import AsyncKernelManager
        from jupyter_client.provisioning.local_provisioner import LocalProvisioner

        original = self.write_notebook("import time\nfrom pathlib import Path\ntime.sleep(5)\nPath('completed').write_bytes(b'done')\n")
        started = threading.Event()
        processes = []

        class StartedKernel(AsyncKernelManager):
            @override
            async def start_kernel(self, **kwargs):
                await super().start_kernel(**kwargs)
                assert isinstance(self.provisioner, LocalProvisioner) and self.provisioner.process is not None
                processes.append(self.provisioner.process)
                started.set()

        class InterruptedSubmission(ThreadPoolExecutor):
            @override
            def submit(self, fn, /, *args, **kwargs):
                super().submit(fn, *args, **kwargs)
                if not started.wait(30):
                    raise AssertionError("kernel did not start before the submission interrupt")
                raise KeyboardInterrupt

        with self.project() as project:
            workspace = project.root.parent
            with (
                patch("jupyter_client.manager.AsyncKernelManager", StartedKernel),
                patch("research_repo_tools.notebooks.ThreadPoolExecutor", InterruptedSubmission),
                self.assertRaises(KeyboardInterrupt),
            ):
                project.execute(self.relative)
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll(), "kernel survived interrupted submission")
            self.assertFalse(psutil.pid_exists(processes[0].pid))
            self.assertFalse((project.root / "completed").exists(), "interrupted notebook ran to completion")
            self.assertEqual(list(project.artifacts.glob("research-notebook-*")), [])
            self.assertEqual((project.root / self.relative).read_bytes(), original)
        self.assertFalse(workspace.exists())
        self.assertEqual((self.source / self.relative).read_bytes(), original)

    def test_bad_selection_and_environment_fail_before_creating_a_workspace(self):
        self.write_notebook("pass\n")
        before = set(self.parent.iterdir())
        for path in (Path("../outside.ipynb"), Path("missing.ipynb"), Path("notebooks"), Path("rust-toolchain.toml"), self.parent / "outside.ipynb"):
            with self.subTest(path=path), self.assertRaises(ValueError), self.project([path]):
                self.fail("invalid fixture was yielded")
        with (
            self.assertRaisesRegex(ValueError, "selected locked environment"),
            isolated_project(self.source, [self.relative], parent=self.parent, environment=self.parent / "different-environment"),
        ):
            self.fail("wrong interpreter accepted")
        with (
            self.assertRaisesRegex(ValueError, "outside the source"),
            isolated_project(self.source, [self.relative], parent=self.source, environment=Path(sys.prefix)),
        ):
            self.fail("source project used for scratch")
        self.assertEqual(set(self.parent.iterdir()), before)

    def test_execution_rejects_escaping_paths_bad_env_and_wrong_python(self):
        self.write_notebook("pass\n")
        before = dict(os.environ)
        with self.project() as project:
            for options in ({"cwd": Path("..")}, {"cwd": self.source}, {"env": {"BAD=NAME": "value"}}, {"timeout": False}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    project.execute(self.relative, **options)
            with self.assertRaises(ValueError):
                project.execute(self.source / self.relative)
            (project.root / ".python-version").write_bytes(b"3.99\n")
            with self.assertRaisesRegex(ValueError, "declared Python version"):
                project.execute(self.relative)
            self.assertEqual(list(project.artifacts.iterdir()), [])
        self.assertEqual(dict(os.environ), before)

    def test_links_cannot_redirect_copies_execution_or_artifacts(self):
        self.write_notebook("pass\n")
        external = self.parent / "external"
        external.mkdir()
        sentinel = external / "sentinel"
        sentinel.write_bytes(b"untouched\r\n")

        def link(path):
            if os.name == "nt":
                subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(path), str(external)], check=True, capture_output=True, timeout=30)
                self.assertTrue(path.is_junction())
            else:
                path.symlink_to(external, target_is_directory=True)
                self.assertTrue(path.is_symlink())
            self.assertEqual(path.resolve(), external)

        link(self.source / "linked")
        with self.assertRaisesRegex(ValueError, "contain links"), self.project([Path("linked/sentinel")]):
            self.fail("linked input accepted")
        with self.project() as project:
            link(project.root / "linked")
            with self.assertRaisesRegex(ValueError, "contain links"):
                project.execute(self.relative, cwd=Path("linked"))
            project.artifacts.rmdir()
            link(project.artifacts)
            with self.assertRaisesRegex(ValueError, "replaced with links"):
                project.execute(self.relative)
        self.assertEqual(sentinel.read_bytes(), b"untouched\r\n")
        self.assertEqual(set(external.iterdir()), {sentinel})


if __name__ == "__main__":
    unittest.main()
