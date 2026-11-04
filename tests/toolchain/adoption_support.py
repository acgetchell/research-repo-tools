"""Fresh Python-adoption consumers shared by migration and gate tests."""

import json
import subprocess

import pytest

from research_repo_tools import python_adoption as adoption
from research_repo_tools import python_baseline, toolchain


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    root = tmp_path / "consumer café with spaces"
    root.mkdir()
    text = """# consumer policy stays here
[project]
name = "consumer"
version = "1.0.0"
requires-python = ">=3.14"
[dependency-groups]
tooling = ["research-repo-tools==0.1.6"] # retained comment
notebook = ["research-repo-tools[notebooks]==0.1.6"]
dev = [{include-group="tooling"}, "ruff==0.16.9"]
[tool.uv]
required-version = "==0.12.19"
package = false
[tool.research-repo-tools.toolchain]
inherit-python = true
[tool.ruff]
target-version = "py314"
line-length = 100
[tool.ruff.lint.per-file-ignores]
"negative.py" = ["F821"]
[tool.ty.environment]
python-version = "3.14"
"""
    (root / "pyproject.toml").write_bytes(text.replace("\n", "\r\n").encode())
    (root / ".python-version").write_bytes(b"3.14\r\n")
    (root / "uv.lock").write_bytes(b"old locked resolution\r\n")
    (root / "README.md").write_bytes(b"consumer documentation\r\n")
    monkeypatch.setattr(python_baseline, "baseline", lambda: python_baseline.PythonBaseline(">=3.15", "3.15", "0.1.7"))
    monkeypatch.setattr(adoption, "select_files", lambda *_args, **_kwargs: (".python-version", "pyproject.toml", "uv.lock", "README.md"))
    monkeypatch.setattr(adoption, "get_safe_executable", lambda _: "uv")
    monkeypatch.setattr(toolchain.Runtime, "uv_status", lambda _: toolchain.Status("uv", "0.12.19", "0.12.19", "uv", True))
    monkeypatch.setattr(toolchain.Runtime, "sync", lambda _: None)
    calls = []

    def execute(command, args, *, cwd, env, **kwargs):
        calls.append((str(command), args, cwd, env))
        if args[0] == "lock":
            (cwd / "uv.lock").write_bytes(b"new locked resolution\n")
        elif args[0] == "sync" and "--check" not in args:
            environment = cwd / ".venv"
            environment.mkdir(exist_ok=True)
            (environment / "pyvenv.cfg").write_bytes(b"new managed interpreter\n")
        elif args[:2] == ["-m", "ipykernel"]:
            path = cwd / ".venv/share/jupyter/kernels/research-repo-tools/kernel.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(json.dumps({"argv": [str(command), "-m", "ipykernel_launcher"]}).encode())
        return subprocess.CompletedProcess([command, *args], 0, "", "")

    monkeypatch.setattr(adoption, "run_safe_command", execute)
    return root, calls


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
