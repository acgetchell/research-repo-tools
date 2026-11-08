"""Coverage cleanup rejects stale inventories and unsafe filesystem boundaries."""

from pathlib import Path

import pytest

from research_repo_tools import cli, coverage_clean


@pytest.mark.parametrize("change", ["replacement", "new-file", "nested-directory"])
def test_changed_inventory_preserves_every_candidate(tmp_path, change):
    shard = tmp_path / ".coverage.first"
    shard.write_bytes(b"first")
    run = tmp_path / "coverage/failed-run"
    run.mkdir(parents=True)
    plan = coverage_clean.plan_clean(tmp_path)
    if change == "replacement":
        shard.rename(tmp_path / "retired")
        shard.write_bytes(b"first")
    elif change == "new-file":
        (tmp_path / ".coverage.new").write_bytes(b"new")
    else:
        (run / "nested").mkdir()
    with pytest.raises(ValueError, match="inventory changed"):
        coverage_clean.apply_clean(plan)
    assert shard.read_bytes() == b"first"
    assert run.is_dir()


@pytest.mark.parametrize("link_type", ["is_symlink", "is_junction"])
@pytest.mark.parametrize("boundary", [".coverage.shard", "coverage", "coverage/failed-run", "coverage/failed-run/.coverage.child"])
def test_link_boundaries_refused_before_removal(tmp_path, monkeypatch, link_type, boundary):
    tmp_path = tmp_path.resolve()
    shard = tmp_path / ".coverage.shard"
    shard.write_bytes(b"root")
    child = tmp_path / "coverage/failed-run/.coverage.child"
    child.parent.mkdir(parents=True)
    child.write_bytes(b"child")
    original = getattr(Path, link_type)
    monkeypatch.setattr(Path, link_type, lambda path: path == tmp_path / boundary or original(path))
    with pytest.raises(ValueError, match="links or junctions"):
        coverage_clean.plan_clean(tmp_path)
    assert shard.read_bytes() == b"root"
    assert child.read_bytes() == b"child"


def test_locked_file_reports_partial_progress_and_stops(tmp_path, monkeypatch):
    first = tmp_path / ".coverage.a"
    locked = tmp_path / ".coverage.b"
    last = tmp_path / ".coverage.c"
    for path in (first, locked, last):
        path.write_bytes(b"data")
    plan = coverage_clean.plan_clean(tmp_path)
    original = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == locked:
            raise PermissionError("file is being used by another process")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(RuntimeError, match="earlier removals remain applied"):
        coverage_clean.apply_clean(plan)
    assert not first.exists()
    assert locked.read_bytes() == last.read_bytes() == b"data"


def test_invalid_cli_modes_and_unknown_options_do_not_delete(tmp_path):
    shard = tmp_path / ".coverage.shard"
    shard.write_bytes(b"data")
    for arguments in (["--apply", "--dry-run"], ["--apply", "--typo"], ["--app"]):
        with pytest.raises(SystemExit) as raised:
            cli.main(["--root", str(tmp_path), "coverage", "clean", *arguments])
        assert raised.value.code == 2
        assert shard.read_bytes() == b"data"
