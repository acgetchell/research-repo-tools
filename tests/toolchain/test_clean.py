"""Owned-installation cleanup and conservative retention on every supported host."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from research_repo_tools import config, toolchain, toolchain_config
from research_repo_tools import toolchain_clean as clean


def consumer(root, *, cargo="2.14.1", rust="1.98.0", python="3.14"):
    root.mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version="==0.12.19"\n'
        f'[tool.research-repo-tools.toolchain.cargo]\ngit-cliff="{cargo}"\n'
        '[tool.research-repo-tools.toolchain.binaries]\ngitleaks="8.30.1"\n',
        encoding="utf-8",
        newline="\n",
    )
    (root / ".python-version").write_bytes(python.encode() + b"\r\n")
    (root / "rust-toolchain.toml").write_text(f'[toolchain]\nchannel="{rust}"\n', encoding="utf-8", newline="\n")
    return config.load(root=root)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    # Resolve macOS's /var alias before checking the no-link ownership boundary.
    tmp_path = tmp_path.resolve()
    monkeypatch.setenv("RESEARCH_REPO_TOOLS_HOME", str(tmp_path / "owned tools"))
    for name in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON_INSTALL_DIR"):
        monkeypatch.delenv(name, raising=False)
    settings = consumer(tmp_path / "consumer")
    runtime = toolchain.Runtime(toolchain_config.load(settings))
    return settings, runtime


def install(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "sentinel").write_bytes(b"fixture\r\n")
    return path


def test_retained_roots_versions_compilers_and_unknown_entries(owned, tmp_path):
    settings, runtime = owned
    cargo = runtime.cargo_root(runtime.plan.cargo[0])
    older = install(cargo.with_name("2.13.0"))
    compiler = install(runtime.base / "cargo" / runtime.host / "1.97.0" / "git-cliff" / "2.14.1")
    for path in [
        cargo,
        cargo.with_name("2.14.1-beta.1"),
        cargo.with_name("2.14.1+build"),
        cargo.with_name("2.15.0"),
        cargo.with_name("unrecognized"),
        cargo.parent.parent / "undeclared" / "1.0.0",
        runtime.base / "cargo" / "other-host" / "1.96.0" / "git-cliff" / "2.12.0",
    ]:
        install(path)
    assert {entry.path for entry in clean.plan_clean(settings).removals} == {older, compiler}
    retained = consumer(tmp_path / "retained", cargo="2.13.0", rust="1.97.0")
    assert clean.plan_clean(settings, keep_roots=(retained.root,)).removals == ()


@pytest.mark.parametrize("change", ["declaration", "replacement", "inventory"])
def test_stale_plan_refuses_before_deleting_anything(owned, change):
    settings, runtime = owned
    stale = install(runtime.cargo_root(runtime.plan.cargo[0]).with_name("2.13.0"))
    plan = clean.plan_clean(settings)
    if change == "declaration":
        (settings.root / ".python-version").write_bytes(b"3.14.1\n")
    elif change == "replacement":
        # Keep the original inode allocated so inode reuse cannot mask replacement.
        stale.rename(stale.with_name("retired"))
        install(stale)
    else:
        install(stale.with_name("2.12.0"))
    with pytest.raises(ValueError, match="changed"):
        clean.apply_clean(plan, settings)
    assert stale.is_dir()


@pytest.mark.parametrize("link_type", ["is_symlink", "is_junction"])
def test_linked_installation_boundary_refused(owned, monkeypatch, link_type):
    settings, runtime = owned
    stale = install(runtime.cargo_root(runtime.plan.cargo[0]).with_name("2.13.0"))
    original = getattr(Path, link_type)
    monkeypatch.setattr(Path, link_type, lambda path: path == stale or original(path))
    with pytest.raises(ValueError, match="links or junctions"):
        clean.plan_clean(settings)
    assert stale.is_dir()


def test_locked_windows_directory_reports_partial_progress(owned, monkeypatch):
    settings, runtime = owned
    stale = install(runtime.cargo_root(runtime.plan.cargo[0]).with_name("2.13.0"))
    plan = clean.plan_clean(settings)

    def locked(_path):
        raise PermissionError("file is being used by another process")

    monkeypatch.setattr(clean.shutil, "rmtree", locked)
    with pytest.raises(RuntimeError, match="earlier removals remain applied; close processes"):
        clean.apply_clean(plan, settings)
    assert stale.is_dir()


def test_rust_uses_private_manager_and_preserves_default_and_other_hosts(owned, monkeypatch):
    settings, runtime = owned
    home = runtime.manager / "toolchains"
    old = install(home / "toolchains" / f"1.96.0-{runtime.host}")
    default = install(home / "toolchains" / f"1.97.0-{runtime.host}")
    for name in (f"1.98.0-{runtime.host}", f"1.99.0-{runtime.host}", "1.95.0-other-host", "nightly"):
        install(home / "toolchains" / name)
    (home / "settings.toml").write_text(f'default_toolchain="{default.name}"\n', encoding="utf-8", newline="\n")
    runtime.rustup.parent.mkdir(parents=True)
    runtime.rustup.write_bytes(b"mock manager")
    calls = []

    def uninstall(command, args, **kwargs):
        calls.append(args)
        assert Path(command) == runtime.rustup
        assert args == ["toolchain", "uninstall", old.name]
        assert kwargs["env"]["RUSTUP_HOME"] == str(home)
        assert kwargs["env"]["CARGO_HOME"] == str(runtime.manager / "cargo")
        assert "RUSTUP_TOOLCHAIN" not in kwargs["env"]
        shutil.rmtree(old)

    monkeypatch.setattr(clean, "run_safe_command", uninstall)
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "unrelated")
    plan = clean.plan_clean(settings)
    assert [entry.path for entry in plan.removals] == [old]
    assert calls == []
    clean.apply_clean(plan, settings)
    assert not old.exists() and default.is_dir() and len(calls) == 1


@pytest.fixture
def python_inventory(owned, tmp_path, monkeypatch):
    settings, runtime = owned
    host_os = {"apple-darwin": "macos", "unknown-linux-gnu": "linux", "pc-windows-msvc": "windows"}
    system = next(value for suffix, value in host_os.items() if runtime.host.endswith(suffix))
    arch = runtime.host.split("-", 1)[0]
    entries = []
    for version in ("3.13.7", "3.14.1", "3.15.0"):
        key = f"cpython-{version}-{system}-{arch}-none"
        directory = install(runtime.base / "python" / key)
        entries.append(dict(key=key, version=version, path=str(directory / "sentinel"), os=system, arch=arch, implementation="cpython", variant="default"))
    tools = tmp_path.resolve() / "uv tools"
    tools.mkdir()
    calls = []
    monkeypatch.setattr(toolchain.Runtime, "uv_status", lambda self: toolchain.Status("uv", "0.12.19", "0.12.19", "mock-uv", True))

    def uv(command, args, **kwargs):
        assert command == "mock-uv"
        calls.append((args, kwargs))
        if args[:2] == ["python", "list"]:
            assert kwargs["env"]["UV_PYTHON_INSTALL_DIR"] == str(runtime.base / "python")
            output = json.dumps(entries)
        elif args[:2] == ["tool", "dir"]:
            output = str(tools)
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess([], 0, output, "")

    monkeypatch.setattr(clean, "run_safe_command", uv)
    return settings, runtime, entries, tools, calls


def test_python_private_inventory_and_removal_without_global_uv_uninstall(python_inventory):
    settings, runtime, entries, _, calls = python_inventory
    old = runtime.base / "python" / entries[0]["key"]
    plan = clean.plan_clean(settings)
    assert [entry.path for entry in plan.removals] == [old]
    assert not any("uninstall" in args for args, _ in calls)
    clean.apply_clean(plan, settings)
    assert not old.exists()
    assert clean.plan_clean(settings).removals == ()
    assert not any("uninstall" in args for args, _ in calls)


@pytest.mark.parametrize("protection", ["project", "active", "override", "tool", "running", "outside", "variant", "architecture"])
def test_python_references_and_nonowned_interpreters_preserved(python_inventory, monkeypatch, protection):
    settings, runtime, entries, tools, _ = python_inventory
    interpreter = Path(entries[0]["path"])
    if protection in {"project", "active", "override", "tool"}:
        environment = settings.root / ".venv" if protection == "project" else (tools if protection == "tool" else settings.root) / "using old python"
        environment.mkdir()
        (environment / "pyvenv.cfg").write_text(f"home = {interpreter.parent}\r\n", encoding="utf-8", newline="")
        if protection in {"active", "override"}:
            monkeypatch.setenv("VIRTUAL_ENV" if protection == "active" else "UV_PROJECT_ENVIRONMENT", str(environment))
    elif protection == "running":
        monkeypatch.setattr(clean.sys, "base_prefix", str(interpreter.parent))
    elif protection == "outside":
        entries[0]["path"] = str(settings.root / "user-owned-python")
    elif protection == "variant":
        entries[0]["variant"] = "freethreaded"
    else:
        entries[0]["arch"] = "other"
    assert clean.plan_clean(settings).removals == ()
    assert interpreter.exists()


@pytest.mark.parametrize("malformation", ["relative", "key", "metadata", "array"])
def test_python_ambiguous_inventory_refuses_cleanup(python_inventory, malformation):
    settings, _, entries, tools, _ = python_inventory
    if malformation == "relative":
        entries[0]["path"] = "relative/python"
    elif malformation == "key":
        entries[0]["key"] = "../escape"
    elif malformation == "metadata":
        environment = tools / "unidentifiable"
        environment.mkdir()
        (environment / "pyvenv.cfg").write_bytes(b"home = relative\n")
    else:
        entries.append("invalid entry")
    with pytest.raises(ValueError):
        clean.plan_clean(settings)


def test_new_python_install_default_and_explicit_override(owned, monkeypatch):
    _, runtime = owned
    monkeypatch.setattr(runtime, "uv_status", lambda: toolchain.Status("uv", "", "missing", "", False))
    assert runtime._environment()["UV_PYTHON_INSTALL_DIR"] == str(runtime.base / "python")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(runtime.plan.root / "user Python"))
    assert runtime._environment()["UV_PYTHON_INSTALL_DIR"] == str(runtime.plan.root / "user Python")


def test_python_minor_retains_newest_patch_and_other_consumers_exact_pin(python_inventory, tmp_path):
    settings, runtime, entries, _, _ = python_inventory
    latest = dict(entries[1], version="3.14.7", key=entries[1]["key"].replace("3.14.1", "3.14.7"))
    latest["path"] = str(install(runtime.base / "python" / latest["key"]) / "sentinel")
    entries.append(latest)
    assert {entry.path for entry in clean.plan_clean(settings).removals} == {Path(item["path"]).parent for item in entries[:2]}
    retained = consumer(tmp_path / "retained", python="3.14.1")
    assert {entry.path for entry in clean.plan_clean(settings, keep_roots=(retained.root,)).removals} == {Path(entries[0]["path"]).parent}


@pytest.mark.parametrize("link_type", ["is_symlink", "is_junction"])
def test_python_unrecognized_alias_target_is_preserved(python_inventory, monkeypatch, link_type):
    settings, runtime, entries, _, _ = python_inventory
    old = Path(entries[0]["path"]).parent
    alias = install(runtime.base / "python" / "minor alias")
    original_link = getattr(Path, link_type)
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, link_type, lambda path: path == alias or original_link(path))
    monkeypatch.setattr(Path, "resolve", lambda path, **kwargs: old if path == alias else original_resolve(path, **kwargs))
    assert clean.plan_clean(settings).removals == ()
