"""Pinned Python migration, stale plans, and Windows-compatible rollback boundaries."""

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from research_repo_tools import config, python_baseline, toolchain
from research_repo_tools import python_adoption as adoption


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


def test_314_to_315_preview_preserves_sources_and_applies_complete_migration(consumer, monkeypatch):
    root, calls = consumer
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/unrelated/environment")
    before = snapshot(root)
    plan = adoption.plan_python_adoption(config.load(root=root))
    assert snapshot(root) == before
    assert plan.baseline.selected == "3.15"
    assert plan.notebook_group == "notebook"
    assert all(env["UV_PROJECT_ENVIRONMENT"] == str(cwd / ".venv") for _, _, cwd, env in calls)
    adoption.apply_python_adoption(plan)
    text = (root / "pyproject.toml").read_bytes()
    document = tomllib.loads(text.decode())
    assert document["project"]["requires-python"] == ">=3.15"
    assert document["dependency-groups"]["tooling"] == ["research-repo-tools==0.1.7"]
    assert document["dependency-groups"]["notebook"] == ["research-repo-tools[notebooks]==0.1.7"]
    assert document["tool"]["ruff"]["line-length"] == 100
    assert document["tool"]["ruff"]["lint"]["per-file-ignores"] == {"negative.py": ["F821"]}
    assert "target-version" not in document["tool"]["ruff"]
    assert "python-version" not in document["tool"]["ty"]["environment"]
    assert b"# retained comment\r\n" in text
    assert (root / ".python-version").read_bytes() == b"3.15\r\n"
    assert (root / "uv.lock").read_bytes() == b"new locked resolution\n"
    kernel = json.loads((root / ".venv/share/jupyter/kernels/research-repo-tools/kernel.json").read_bytes())
    assert Path(kernel["argv"][0]).is_relative_to(root / ".venv")
    after = snapshot(root)
    repeat = adoption.plan_python_adoption(config.load(root=root))
    assert not repeat.changed_paths
    adoption.apply_python_adoption(repeat)
    assert snapshot(root) == after
    assert python_baseline.drift(root) == ()


@pytest.mark.parametrize("final_table", [b"", b"\r\n[tool.uv.dependency-groups]"])
def test_public_package_runtime_promise_is_preserved(consumer, final_table):
    root, _ = consumer
    manifest = root / "pyproject.toml"
    manifest.write_bytes(manifest.read_bytes().replace(b"package = false", b"package = true").replace(b">=3.14", b">=3.12") + final_table)
    plan = adoption.plan_python_adoption(config.load(root=root))
    document = tomllib.loads(dict(plan.replacements)["pyproject.toml"].decode())
    assert document["project"]["requires-python"] == ">=3.12"
    assert document["tool"]["uv"]["dependency-groups"] == {
        "tooling": {"requires-python": ">=3.15"},
        "notebook": {"requires-python": ">=3.15"},
    }


@pytest.mark.parametrize("existing_environment", [False, True])
def test_failed_sync_restores_old_declarations_and_environment(consumer, monkeypatch, existing_environment):
    root, _ = consumer
    if existing_environment:
        (root / ".venv").mkdir()
        (root / ".venv/pyvenv.cfg").write_bytes(b"previous usable interpreter")
        (root / ".venv/user-file").write_bytes(b"preserve this file")
    before = snapshot(root)
    plan = adoption.plan_python_adoption(config.load(root=root))

    def fail(*_args, **_kwargs):
        (root / ".venv").mkdir(exist_ok=True)
        (root / ".venv/partial").write_bytes(b"failed installation")
        raise subprocess.CalledProcessError(17, ["uv", "sync"])

    monkeypatch.setattr(adoption, "_sync", fail)
    with pytest.raises(subprocess.CalledProcessError):
        adoption.apply_python_adoption(plan)
    assert snapshot(root) == before
    assert not list(root.glob(".venv.rrt-backup-*"))


def test_failed_resolution_does_not_publish_anything(consumer, monkeypatch):
    root, _ = consumer
    before = snapshot(root)
    monkeypatch.setattr(adoption, "run_safe_command", lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["uv", "lock"])))
    with pytest.raises(subprocess.CalledProcessError):
        adoption.plan_python_adoption(config.load(root=root))
    assert snapshot(root) == before


def test_stale_plan_fails_before_installs_or_replacement(consumer, monkeypatch):
    root, _ = consumer
    plan = adoption.plan_python_adoption(config.load(root=root))
    (root / "README.md").write_bytes(b"new user work")
    before = snapshot(root)
    monkeypatch.setattr(toolchain.Runtime, "sync", lambda _: pytest.fail("installed before stale-input rejection"))
    with pytest.raises(ValueError, match="source changed"):
        adoption.apply_python_adoption(plan)
    assert snapshot(root) == before


def test_windows_locked_environment_rename_leaves_prior_state(consumer, monkeypatch):
    root, _ = consumer
    (root / ".venv").mkdir()
    (root / ".venv/pyvenv.cfg").write_bytes(b"previous usable interpreter")
    before = snapshot(root)
    plan = adoption.plan_python_adoption(config.load(root=root))
    original = Path.rename

    def locked(path, target):
        if path == root / ".venv":
            raise PermissionError("Windows process holds the environment open")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(PermissionError):
        adoption.apply_python_adoption(plan)
    assert snapshot(root) == before


def test_ordinary_drift_check_does_not_resolve_or_install(consumer, monkeypatch):
    root, calls = consumer
    before = snapshot(root)
    with pytest.raises(ValueError, match="shared Python drift.*toolchain adopt"):
        python_baseline.check(root)
    assert not calls and snapshot(root) == before


@pytest.mark.parametrize("target", ["py316", "future"])
def test_incompatible_targets_fail_before_resolution(consumer, monkeypatch, target):
    root, _calls = consumer
    manifest = root / "pyproject.toml"
    manifest.write_bytes(manifest.read_bytes().replace(b"py314", target.encode()))
    before = snapshot(root)
    with pytest.raises(ValueError, match="incompatible"):
        adoption.plan_python_adoption(config.load(root=root))
    assert snapshot(root) == before


def test_new_inputs_reject_stale_plan(consumer, monkeypatch):
    root, _calls = consumer
    plan = adoption.plan_python_adoption(config.load(root=root))
    (root / "new.py").write_bytes(b"new_source = True\n")
    monkeypatch.setattr(adoption, "select_files", lambda *_args, **_kwargs: (*plan.inventory, "new.py"))
    with pytest.raises(ValueError, match="inventory changed"):
        adoption.apply_python_adoption(plan)


def test_stale_kernel_is_repaired_even_without_declaration_changes(consumer):
    root, calls = consumer
    first = adoption.plan_python_adoption(config.load(root=root))
    adoption.apply_python_adoption(first)
    kernel = root / ".venv/share/jupyter/kernels/research-repo-tools/kernel.json"
    kernel.write_bytes(b'{"argv": ["old-removed-python", "-m", "ipykernel_launcher"]}')
    repeat = adoption.plan_python_adoption(config.load(root=root))
    assert not repeat.changed_paths
    adoption.apply_python_adoption(repeat)
    assert "old-removed-python" not in kernel.read_text(encoding="utf-8")
    assert sum(args[:2] == ["-m", "ipykernel"] for _command, args, _cwd, _env in calls) == 2


def test_candidate_preserves_executable_build_helpers(consumer, monkeypatch):
    root, _ = consumer
    helper = root / "build-helper.sh"
    helper.write_bytes(b"#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    inventory = adoption.select_files(root)
    monkeypatch.setattr(adoption, "select_files", lambda *_args, **_kwargs: (*inventory, helper.name))
    execute = adoption.run_safe_command

    def check_candidate(command, args, **kwargs):
        if args[0] == "lock":
            candidate = kwargs["cwd"] / helper.name
            assert candidate.read_bytes() == helper.read_bytes()
            assert candidate.stat().st_mode & 0o111 == helper.stat().st_mode & 0o111
        return execute(command, args, **kwargs)

    monkeypatch.setattr(adoption, "run_safe_command", check_candidate)
    adoption.plan_python_adoption(config.load(root=root))


def test_windows_junction_environment_rejected_before_mutation(consumer, monkeypatch):
    root, _ = consumer
    environment = root / ".venv"
    environment.mkdir()
    (environment / "pyvenv.cfg").write_bytes(b"external interpreter")
    plan = adoption.plan_python_adoption(config.load(root=root))
    before = snapshot(root)
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == environment or original(path))
    with pytest.raises(ValueError, match="regular project virtual environment"):
        adoption.apply_python_adoption(plan)
    assert snapshot(root) == before
