"""Read-only native capture and controlled Git transport race/failure regressions."""

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from research_repo_tools import source_exports
from research_repo_tools.source_exports import capture_source, export_source


def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]) -> None:
    def git(root: Path, *args: str, input: bytes | None = None) -> bytes:
        if args == ("rev-parse", "--show-prefix"):
            return b""
        if args[0] == "rev-parse":
            return b"a" * 40 + b"\n"
        blobs = {hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode(): data for data in files.values()}
        if args[0] == "ls-tree":
            return b"".join(
                b"100644 blob " + hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode() + b"\t" + name.encode() + b"\0"
                for name, data in files.items()
            )
        assert args == ("cat-file", "--batch")
        assert input is not None
        return b"".join(oid + b" blob " + str(len(blobs[oid])).encode() + b"\n" + blobs[oid] + b"\n" for oid in input.splitlines())

    monkeypatch.setattr(source_exports, "_git", git)
    monkeypatch.setattr(
        source_exports, "select_files", lambda root, **kwargs: tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()))
    )


def test_dirty_export_exact_bytes_deletions_new_files_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    repository(root, monkeypatch, {"changed": b"old", "deleted": b"gone"})
    (root / "changed").write_bytes(b"new\r\n\0\xff")
    (root / "new").write_bytes(b"new file")
    source = capture_source(root, working_tree=True)
    assert source.dirty
    destination = tmp_path / "export"
    with export_source(source, destination) as exported:
        assert (exported / "changed").read_bytes() == b"new\r\n\0\xff"
        assert (exported / "new").read_bytes() == b"new file"
        assert not (exported / "deleted").exists()
        assert not (exported / ".git").exists()
    assert not destination.exists()
    (root / "new").write_bytes(b"changed later")
    with pytest.raises(ValueError, match="changed"):
        source.verify()


def test_capture_detects_ref_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository(tmp_path, monkeypatch, {"source": b"bytes"})
    with patch.object(source_exports, "_resolve", side_effect=["a" * 40, "b" * 40]), pytest.raises(ValueError, match="during capture"):
        capture_source(tmp_path)


@pytest.mark.parametrize("name", ["../escape", ".git/config", "CON", "a\\b"])
def test_unsafe_stored_names_fail_before_materialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    repository(tmp_path, monkeypatch, {name: b"unsafe"})
    with pytest.raises(ValueError):
        capture_source(tmp_path)


def test_export_failure_cleanup_reports_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository(tmp_path, monkeypatch, {"source": b"bytes"})
    source = capture_source(tmp_path)
    destination = tmp_path / "export"
    with patch.object(source_exports.shutil, "rmtree", side_effect=OSError("blocked")), pytest.raises(RuntimeError, match="retained source"):
        with export_source(source, destination):
            pass
    assert (destination / "source").read_bytes() == b"bytes"


def test_blob_hash_is_independently_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository(tmp_path, monkeypatch, {"source": b"bytes"})
    original = source_exports._git

    def corrupted(root: Path, *args: str, input: bytes | None = None) -> bytes:
        data = original(root, *args, input=input)
        return data.replace(b"bytes", b"wrong") if args[0] == "cat-file" else data

    monkeypatch.setattr(source_exports, "_git", corrupted)
    with pytest.raises(ValueError, match="identity differs"):
        capture_source(tmp_path)


@pytest.mark.parametrize("files", [{"A/x": b"x", "a/y": b"y"}, {"file": b"x", "file/child": b"y"}])
def test_portable_directory_aliases_and_file_parents_reject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]) -> None:
    repository(tmp_path, monkeypatch, files)
    with pytest.raises(ValueError, match="alias|overlap"):
        capture_source(tmp_path)


@pytest.mark.parametrize("mode", [b"120000 blob", b"160000 commit"])
def test_git_links_and_submodules_reject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: bytes) -> None:
    repository(tmp_path, monkeypatch, {"file": b"x"})
    original = source_exports._git

    def unsupported(root: Path, *args: str, input: bytes | None = None) -> bytes:
        data = original(root, *args, input=input)
        return data.replace(b"100644 blob", mode) if args[0] == "ls-tree" else data

    monkeypatch.setattr(source_exports, "_git", unsupported)
    with pytest.raises(ValueError, match="links, submodules"):
        capture_source(tmp_path)


def test_prepared_export_rejects_unrecorded_inputs_and_mode_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository(tmp_path, monkeypatch, {"source": b"bytes"})
    source = capture_source(tmp_path)
    destination = tmp_path / "export"
    with export_source(source, destination):
        (destination / "extra").write_bytes(b"unrecorded")
        with pytest.raises(ValueError, match="inventory differs"):
            source_exports.verify_export(source, destination)
        (destination / "extra").unlink()
        path = destination / "source"
        path.chmod(0o444)
        with pytest.raises(ValueError, match="bytes/modes differ"):
            source_exports.verify_export(source, destination)


def test_transaction_rechecks_inputs_after_staging_and_preserves_outputs(tmp_path: Path) -> None:
    from research_repo_tools import files

    output, source = tmp_path / "output", tmp_path / "source"
    output.write_bytes(b"old output")
    source.write_bytes(b"old input")
    stage = files._stage_writes

    def changed(writes):
        result = stage(writes)
        source.write_bytes(b"changed input")
        return result

    def verify() -> None:
        if source.read_bytes() != b"old input":
            raise ValueError("source changed")

    with patch.object(files, "_stage_writes", side_effect=changed), pytest.raises(ValueError, match="source changed"):
        files.replace_many({output: b"new output"}, verify=verify)
    assert output.read_bytes() == b"old output"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["output", "source"]
