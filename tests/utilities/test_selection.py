"""Representative Git inventories, portable paths, and batching failures."""

import subprocess
import sys
from pathlib import Path

import pytest

from research_repo_tools import selection
from research_repo_tools.process import run_command_live


def test_selects_existing_unique_inputs_and_excludes_fixtures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("space name.md", "-option.md", "fixture.md"):
        (tmp_path / name).write_bytes(b"content")
    calls = []

    def inventory(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b"space name.md\0deleted.md\0-option.md\0fixture.md\0space name.md\0", b"")

    monkeypatch.setattr(selection, "run_git_bytes", inventory)
    assert selection.select_files(tmp_path, include=["*.md"], exclude=["fixture.md"]) == ("-option.md", "space name.md")
    assert calls[0][0] == ["--no-pager", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.md"]


@pytest.mark.parametrize("name", ["../escape", "C:/drive", "a\\b", "CON", ".git/config", "line\nname"])
def test_inventory_rejects_unsafe_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(selection, "run_git_bytes", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, name.encode() + b"\0", b""))
    with pytest.raises(ValueError):
        selection.select_files(tmp_path)


def test_batching_keeps_spaces_and_option_names_in_one_argument() -> None:
    assert selection.argument_batches(["lint", "--check"], ["a b", "-option", "c"], batch_size=2) == (
        ("lint", "--check", "./a b", "./-option"),
        ("lint", "--check", "./c"),
    )
    assert selection.argument_batches(["lint"], []) == ()
    with pytest.raises(ValueError, match="limit"):
        selection.argument_batches(["lint"], ["a" * 100], argument_limit=20)


def test_batch_failure_stops_remaining_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selection, "select_files", lambda *args, **kwargs: ("a", "b", "c"))
    calls = []

    def fail(command, args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(7, [command, *args])

    monkeypatch.setattr(selection, "run_command_live", fail)
    with pytest.raises(subprocess.CalledProcessError):
        selection.run_selected(tmp_path, [sys.executable], batch_size=1)
    assert calls == [("./a",)]


def test_live_runner_inherits_streams_and_propagates_failure(capfd: pytest.CaptureFixture[str]) -> None:
    result = run_command_live(sys.executable, ["-c", "import sys; print('live'); print('error', file=sys.stderr)"])
    assert result.stdout is None and result.stderr is None
    output = capfd.readouterr()
    assert output.out.strip() == "live" and output.err.strip() == "error"
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run_command_live(sys.executable, ["-c", "raise SystemExit(13)"])
    assert caught.value.returncode == 13


def test_argument_budget_handles_native_bytes_utf16_quotes_and_trailing_backslashes() -> None:
    names = ["café spaced.py", 'quoted"name.py', "emoji😀.py", "trailing " + chr(92), "short.py"] * 100
    command = ["long executable path", "--check"]
    batches = selection.argument_batches(command, names, batch_size=1000, argument_limit=180)
    assert len(batches) > 1
    assert [name for batch in batches for name in batch[len(command) :]] == ["./" + name for name in names]
    for batch in batches:
        assert len(subprocess.list2cmdline(batch).encode("utf-16-le")) // 2 + 1 <= 180
        assert sum(len(selection.os.fsencode(arg)) + 1 for arg in batch) <= 180


def test_inventory_rechecks_parent_aliases_on_each_call(tmp_path, monkeypatch):
    from research_repo_tools import files

    root = tmp_path.resolve()
    directories = [root / name for name in ("first", "second")]
    for path in directories:
        path.mkdir()
        (path / "data.py").write_bytes(b"")
    monkeypatch.setattr(selection, "run_git_bytes", lambda *a, **kw: subprocess.CompletedProcess([], 0, b"first/data.py\0second/data.py\0", b""))
    assert selection.select_files(root) == ("first/data.py", "second/data.py")
    # A directory alias model supplements native symlink/junction tests; do
    # not replace global OS identity or rely on Windows symlink privileges.
    original = Path.stat
    first = directories[0].stat()

    def stat(path, *args, **kwargs):
        return first if path == directories[1] else original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", stat)
        with pytest.raises(ValueError, match="duplicate"):
            selection.select_files(root)
        with pytest.raises(ValueError, match="duplicate"):
            files._validate_distinct_paths((directories[0] / "data.py", directories[1] / "data.py"))
    assert selection.select_files(root) == ("first/data.py", "second/data.py")
