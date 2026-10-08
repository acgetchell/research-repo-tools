"""Workflow preflight and failure behavior without mutating any Git repository."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_repo_tools import cli, config
from research_repo_tools import notebook_workflows as workflows


def settings(root, **reset):
    return config.parse({"notebooks": {"reset": {"sources": ["notebooks"], **reset}}}, root=root)


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    path = tmp_path / "notebooks" / "analysis with spaces.ipynb"
    path.parent.mkdir()
    path.write_bytes(b"local notebook\r\n")
    calls = []
    entries = [("notebooks/analysis with spaces.ipynb", "100644", "0"), ("pyproject.toml", "100644", "0")]

    def git(args, **kwargs):
        calls.append((args, kwargs))
        assert args[:2] == ["--no-pager", "--literal-pathspecs"]
        if args[2:4] == ["rev-parse", "--show-toplevel"]:
            output = os.fsencode(str(tmp_path)) + b"\n"
        elif args[2] == "ls-files":
            output = b"".join(f"{mode} {'a' * 40} {stage}\t".encode() + os.fsencode(name) + b"\0" for name, mode, stage in entries)
        elif args[2] == "restore":
            output = b""
        else:
            pytest.fail(f"unexpected Git call: {args}")
        return subprocess.CompletedProcess(args, 0, output, b"")

    monkeypatch.setattr(workflows, "run_git_bytes", git)
    return path, calls, entries


def test_preview_reports_deleted_tracked_sources_and_explicit_cleanup_without_effects(tmp_path, inventory, capsys):
    path, calls, entries = inventory
    entries.append(("notebooks/deleted.ipynb", "100644", "0"))
    checkpoint = path.parent / ".ipynb_checkpoints"
    checkpoint.mkdir()
    checkpoint.joinpath("analysis-checkpoint.ipynb").write_bytes(b"checkpoint")
    scratch = tmp_path / "target" / "output with spaces"
    scratch.parent.mkdir()
    scratch.write_bytes(b"scratch")
    plan = workflows.reset(settings(tmp_path, scratch=["target/output with spaces"], checkpoints=["notebooks/.ipynb_checkpoints"]))
    assert plan.sources == (path, path.parent / "deleted.ipynb")
    assert plan.cleanup == (checkpoint, scratch) and plan.revision is None
    assert path.read_bytes() == b"local notebook\r\n" and scratch.read_bytes() == b"scratch"
    assert checkpoint.is_dir() and all(call[0][2] != "restore" for call in calls)
    assert "Preview only" in capsys.readouterr().out


def test_apply_uses_literal_nul_transport_and_only_cleans_declared_paths(tmp_path, inventory, monkeypatch):
    path, calls, entries = inventory
    # Newlines are legal POSIX filenames, but must fail preflight on Windows.
    if os.name != "nt":
        entries.append(("notebooks/literal\nname.ipynb", "100644", "0"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    scratch.joinpath("result.ipynb").write_bytes(b"artifact")
    keep = tmp_path / "undeclared"
    keep.write_bytes(b"preserve")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_GLOB_PATHSPECS"):
        monkeypatch.setenv(key, "wrong")
    workflows.reset(settings(tmp_path, scratch=["scratch"]), apply=True)
    restore = calls[-1]
    assert restore[0][2:] == ["restore", "--worktree", "--ignore-skip-worktree-bits", "--pathspec-from-file=-", "--pathspec-file-nul"]
    expected = b"notebooks/analysis with spaces.ipynb\0" + (b"notebooks/literal\nname.ipynb\0" if os.name != "nt" else b"")
    assert restore[1]["input"] == expected
    assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_GLOB_PATHSPECS"}.intersection(restore[1]["env"])
    assert not scratch.exists() and keep.read_bytes() == b"preserve" and path.read_bytes() == b"local notebook\r\n"


@pytest.mark.parametrize(
    "scratch",
    [
        ".",
        "..",
        "../outside",
        ".git",
        "notebooks",
        "notebooks/analysis with spaces.ipynb",
        "NOTEBOOKS/analysis with spaces.ipynb",
        "pyproject.toml",
        ".venv",
        "target/../../outside",
    ],
)
def test_unsafe_cleanup_fails_before_restore_or_deletion(tmp_path, inventory, scratch):
    path, calls, _entries = inventory
    with pytest.raises(ValueError):
        workflows.reset(settings(tmp_path, scratch=[scratch]), apply=True)
    assert path.read_bytes() == b"local notebook\r\n" and all(call[0][2] != "restore" for call in calls)


@pytest.mark.parametrize("mode,stage", [("120000", "0"), ("160000", "0"), ("100644", "2")])
def test_nonregular_and_unmerged_sources_fail_before_effects(tmp_path, inventory, mode, stage):
    path, calls, entries = inventory
    entries[0] = (entries[0][0], mode, stage)
    with pytest.raises(ValueError, match="regular, merged"):
        workflows.reset(settings(tmp_path), apply=True)
    assert path.exists() and all(call[0][2] != "restore" for call in calls)


def test_git_discovery_failure_and_malformed_inventory_are_errors(tmp_path, inventory, monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git", stderr=b"not a repository")

    monkeypatch.setattr(workflows, "run_git_bytes", failed)
    with pytest.raises(subprocess.CalledProcessError):
        workflows.reset(settings(tmp_path))
    with pytest.raises(ValueError, match="NUL terminated"):
        workflows._records(b"truncated")
    with pytest.raises(ValueError, match="malformed"):
        workflows._records(b"bad\0")
    with pytest.raises(ValueError, match="unsafe"):
        workflows._records(b"100644 object 0\t../outside.ipynb\0")


def test_restore_failure_leaves_cleanup_untouched_and_reports_possible_partial_restore(tmp_path, inventory, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.write_bytes(b"keep")
    original = workflows.run_git_bytes

    def fail(args, **kwargs):
        if args[2] == "restore":
            raise subprocess.CalledProcessError(1, args, stderr=b"cannot restore")
        return original(args, **kwargs)

    monkeypatch.setattr(workflows, "run_git_bytes", fail)
    with pytest.raises(RuntimeError, match="partially restored; cleanup was not started"):
        workflows.reset(settings(tmp_path, scratch=["scratch"]), apply=True)
    assert scratch.read_bytes() == b"keep"


def test_cleanup_failure_names_completed_and_remaining_paths(tmp_path, inventory, monkeypatch):
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    remove = workflows.shutil.rmtree

    def fail(path):
        if path.name == "b":
            raise PermissionError("injected cleanup failure")
        remove(path)

    monkeypatch.setattr(workflows.shutil, "rmtree", fail)
    with pytest.raises(RuntimeError, match="notebooks restored; cleanup failed") as error:
        workflows.reset(settings(tmp_path, scratch=["a", "b", "c"]), apply=True)
    assert "completed:" in str(error.value) and "remaining:" in str(error.value)
    assert not (tmp_path / "a").exists() and (tmp_path / "b").is_dir() and (tmp_path / "c").is_dir()


def test_hardlink_source_alias_is_rejected_before_restore(tmp_path, inventory):
    path, calls, _entries = inventory
    alias = tmp_path / "scratch.ipynb"
    os.link(path, alias)
    with pytest.raises(ValueError, match="aliases a source"):
        workflows.reset(settings(tmp_path, scratch=["scratch.ipynb"]), apply=True)
    assert alias.exists() and all(call[0][2] != "restore" for call in calls)


def test_nested_cleanup_hardlink_source_alias_is_rejected_before_restore(tmp_path, inventory):
    path, calls, _entries = inventory
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    alias = scratch / "copy.ipynb"
    os.link(path, alias)
    with pytest.raises(ValueError, match="aliases a source"):
        workflows.reset(settings(tmp_path, scratch=["scratch"]), apply=True)
    assert alias.exists() and all(call[0][2] != "restore" for call in calls)


@pytest.mark.parametrize("part", ["CON", "aux.txt", "file:stream", "tail.", "tail ", "new\nline.ipynb", "LPT9"])
def test_windows_reserved_path_model_rejects_device_stream_and_alias_names(part):
    with pytest.raises(ValueError, match="reserved on Windows"):
        workflows._validate_windows_parts(("notebooks", part))


def test_windows_reserved_path_model_accepts_supported_names():
    workflows._validate_windows_parts(("notebooks", "analysis with spaces.ipynb", "[literal].ipynb"))


def test_selected_managed_tool_cache_is_protected_from_cleanup(tmp_path, inventory, monkeypatch):
    managed = tmp_path / "managed-tools"
    managed.mkdir()
    sentinel = managed / "tool"
    sentinel.write_bytes(b"managed tool")
    monkeypatch.setenv("RESEARCH_REPO_TOOLS_HOME", str(managed))
    with pytest.raises(ValueError, match="protected project path"):
        workflows.reset(settings(tmp_path, scratch=["managed-tools"]), apply=True)
    assert sentinel.read_bytes() == b"managed tool" and all(call[0][2] != "restore" for call in inventory[1])


@pytest.mark.parametrize("field,value", [("sources", "notebooks"), ("scratch", [""]), ("checkpoints", [1]), ("sources", ["a", "a"]), ("unknown", [])])
def test_reset_configuration_rejects_invalid_deletion_maps(tmp_path, field, value):
    with pytest.raises(ValueError, match="notebooks.reset"):
        config.parse({"notebooks": {"reset": {field: value}}}, root=tmp_path)


@pytest.mark.parametrize("value", [1, "yes"])
def test_browser_configuration_is_a_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="notebooks.lab.browser"):
        config.parse({"notebooks": {"lab": {"browser": value}}}, root=tmp_path)


def test_missing_reset_selection_is_an_actionable_cli_failure(tmp_path, inventory, capsys):
    assert cli.main(["--root", str(tmp_path), "notebooks", "reset"]) == 1
    assert "select notebook reset source" in capsys.readouterr().err


def test_missing_reset_selection_fails_before_git_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(workflows, "run_git_bytes", lambda *args, **kwargs: pytest.fail("inspected Git before source selection"))
    with pytest.raises(ValueError, match="select notebook reset source"):
        workflows.reset(config.parse({}, root=tmp_path))


@pytest.mark.parametrize("revision", ["", "bad\0revision", 1])
def test_malformed_revision_fails_before_git_discovery(tmp_path, monkeypatch, revision):
    monkeypatch.setattr(workflows, "run_git_bytes", lambda *args, **kwargs: pytest.fail("inspected Git before revision parsing"))
    with pytest.raises(ValueError, match="revision must be a nonempty string without NUL"):
        workflows.reset(settings(tmp_path), revision=revision)


def test_junction_model_and_nested_links_are_rejected_before_restore(tmp_path, inventory, monkeypatch):
    _path, calls, _entries = inventory
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    linked = scratch / "modeled junction"
    linked.mkdir()
    monkeypatch.setattr(Path, "is_junction", lambda self: self == linked)
    with pytest.raises(ValueError, match="link or special file"):
        workflows.reset(settings(tmp_path, scratch=["scratch"]), apply=True)
    assert scratch.is_dir() and all(call[0][2] != "restore" for call in calls)


def test_existing_symlink_source_or_cleanup_is_rejected_when_privileges_allow(tmp_path, inventory):
    path, calls, _entries = inventory
    alias = tmp_path / "scratch"
    try:
        alias.symlink_to(path)
    except OSError as error:
        pytest.skip(f"native symlink privilege unavailable: {error}")
    with pytest.raises(ValueError, match="symlink or junction"):
        workflows.reset(settings(tmp_path, scratch=["scratch"]), apply=True)
    assert alias.is_symlink() and path.read_bytes() == b"local notebook\r\n"
    assert all(call[0][2] != "restore" for call in calls)


def test_invalid_revision_cannot_restore_or_clean_anything(tmp_path, inventory, monkeypatch):
    original = workflows.run_git_bytes

    def fail(args, **kwargs):
        if args[2:4] == ["rev-parse", "--verify"]:
            assert args[-2] == "--end-of-options"
            raise subprocess.CalledProcessError(128, args, stderr=b"unknown revision")
        return original(args, **kwargs)

    monkeypatch.setattr(workflows, "run_git_bytes", fail)
    with pytest.raises(subprocess.CalledProcessError):
        workflows.reset(settings(tmp_path), revision="--bad", apply=True)
    assert all(call[0][2] != "restore" for call in inventory[1])


@pytest.mark.parametrize("scratch", [".", "../outside", ".git", ".venv", "notebooks"])
def test_launch_rejects_unsafe_scratch_before_environment_sync(tmp_path, monkeypatch, scratch):
    monkeypatch.setattr(workflows, "runtime", lambda *args: pytest.fail("inspected runtime before path validation"))
    with pytest.raises(ValueError):
        workflows.launch(settings(tmp_path), scratch_dir=scratch)
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("environment", [".", "../shared-environment"])
def test_launch_rejects_environment_override_outside_project_before_sync(tmp_path, monkeypatch, environment):
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", environment)
    monkeypatch.setattr(workflows, "runtime", lambda *args: pytest.fail("inspected runtime before path validation"))
    with pytest.raises(ValueError, match="below the consumer root|unsafe notebook workflow path"):
        workflows.launch(config.parse({}, root=tmp_path))
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("result,expected", [(0, 0), (9, 9), (-15, 143), ("interrupt", 130), ("missing", None)])
def test_launch_preserves_parent_environment_and_cleans_private_caches_on_every_exit(tmp_path, monkeypatch, result, expected):
    managed = SimpleNamespace(plan=SimpleNamespace(python="3.14"), project_environment=lambda: dict(os.environ), uv_status=lambda: SimpleNamespace(path="uv"))
    monkeypatch.setattr(workflows, "runtime", lambda *args: managed)
    for key in ("UV_NO_SYNC", "UV_FROZEN", "UV_NO_MANAGED_PYTHON", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        monkeypatch.setenv(key, "ambient override")
    before = dict(os.environ)
    caches = []

    def child(command, args, **kwargs):
        assert command == "uv" and args[:3] == ["run", "--locked", "--managed-python"]
        assert "--ServerApp.open_browser=False" in args and kwargs["timeout"] is None and kwargs["check"] is False
        assert not {"UV_NO_SYNC", "UV_FROZEN", "UV_NO_MANAGED_PYTHON", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}.intersection(kwargs["env"])
        for key in ("IPYTHONDIR", "MPLCONFIGDIR", "JUPYTER_CONFIG_DIR", "JUPYTER_DATA_DIR", "JUPYTER_RUNTIME_DIR"):
            caches.append(Path(kwargs["env"][key]))
            assert caches[-1].is_dir() and caches[-1].is_relative_to(tmp_path / "target/jupyter")
        if result == "interrupt":
            raise KeyboardInterrupt
        if result == "missing":
            raise OSError("launch failed")
        return subprocess.CompletedProcess(args, result)

    monkeypatch.setattr(workflows, "run_command_live", child)
    if result == "missing":
        with pytest.raises(OSError, match="launch failed"):
            workflows.launch(config.parse({}, root=tmp_path))
    else:
        assert workflows.launch(config.parse({}, root=tmp_path)) == expected
    assert os.environ == before and all(not path.exists() for path in caches)
