"""Portable unsafe-archive rejection and bounded download behavior."""

import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from research_repo_tools import archives
from research_repo_tools.archives import ArchiveLimits, download_asset, extract_archive
from research_repo_tools.evidence import sha256


def tar_asset(path: Path, entries: list[tuple[str, bytes | str]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in entries:
            entry = tarfile.TarInfo(name)
            if isinstance(data, bytes):
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
            else:
                entry.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE, "dir": tarfile.DIRTYPE}[data]
                entry.linkname = "../outside"
                archive.addfile(entry)


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/escape",
        "C:escape",
        "\\\\server\\share",
        "folder\\escape",
        "a/../b",
        "a//b",
        "NUL.txt",
        "con",
        "LPT1",
        "com¹.txt",
        "a/trailing.",
        "a/trailing ",
        "bad:name",
        "a/<bad>",
        "a/./b",
    ],
)
@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_archive_rejects_unsafe_paths_without_partial_destination(tmp_path: Path, name: str, kind: str) -> None:
    asset = tmp_path / "asset"
    if kind == "tar":
        tar_asset(asset, [("valid/first", b"valid"), (name, b"unsafe")])
    else:
        with zipfile.ZipFile(asset, "w") as archive:
            archive.writestr("valid/first", b"valid")
            archive.writestr(name, b"unsafe")
    with pytest.raises(ValueError, match="path"):
        extract_archive(asset, tmp_path / "output")
    assert list(tmp_path.iterdir()) == [asset]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_tar_rejects_links_and_special_files(tmp_path: Path, kind: str) -> None:
    asset = tmp_path / "asset"
    tar_asset(asset, [("link", kind)])
    with pytest.raises(ValueError, match="unsupported"):
        extract_archive(asset, tmp_path / "output")
    assert list(tmp_path.iterdir()) == [asset]


def test_zip_rejects_unix_symlinks(tmp_path: Path) -> None:
    asset = tmp_path / "asset"
    with zipfile.ZipFile(asset, "w") as archive:
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../outside")
    with pytest.raises(ValueError, match="unsupported"):
        extract_archive(asset, tmp_path / "output")


@pytest.mark.parametrize("names", [("x", "x"), ("X", "x"), ("A/x", "a/y"), ("café/x", "cafe\u0301/y"), ("x", "x/y"), ("x/y", "x")])
def test_archive_rejects_duplicate_aliasing_and_overlapping_members(tmp_path: Path, names: tuple[str, str]) -> None:
    asset = tmp_path / "asset"
    tar_asset(asset, [(name, b"x") for name in names])
    with pytest.raises(ValueError, match="duplicate|alias|overlap"):
        extract_archive(asset, tmp_path / "output")
    assert list(tmp_path.iterdir()) == [asset]


@pytest.mark.parametrize("limits", [ArchiveLimits(archive_bytes=1), ArchiveLimits(members=1), ArchiveLimits(content_bytes=1)])
def test_archive_limits_apply_before_publication(tmp_path: Path, limits: ArchiveLimits) -> None:
    asset = tmp_path / "asset"
    tar_asset(asset, [("one", b"1"), ("two", b"2")])
    with pytest.raises(ValueError, match="limit|too many"):
        extract_archive(asset, tmp_path / "output", limits=limits)
    assert list(tmp_path.iterdir()) == [asset]


def test_existing_destination_and_digest_mismatch_leave_existing_files(tmp_path: Path) -> None:
    asset = tmp_path / "asset"
    tar_asset(asset, [("payload", b"new")])
    output = tmp_path / "output"
    output.mkdir()
    (output / "original").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        extract_archive(asset, output)
    with pytest.raises(ValueError, match="SHA-256"):
        extract_archive(asset, tmp_path / "new", expected_sha256="0" * 64)
    assert (output / "original").read_bytes() == b"old"
    assert not (tmp_path / "new").exists()


def test_corrupt_archive_and_mid_extraction_failure_clean_staging(tmp_path: Path, monkeypatch) -> None:
    asset = tmp_path / "asset"
    asset.write_bytes(b"invalid tar")
    with pytest.raises(ValueError, match="invalid release archive"):
        extract_archive(asset, tmp_path / "output")
    tar_asset(asset, [("one", b"1"), ("two", b"2")])
    copy = archives._copy_member

    def fail(source, target: Path, size: int) -> None:
        if target.name == "two":
            raise OSError("injected extraction failure")
        copy(source, target, size)

    monkeypatch.setattr(archives, "_copy_member", fail)
    with pytest.raises(OSError, match="extraction failure"):
        extract_archive(asset, tmp_path / "output")
    assert list(tmp_path.iterdir()) == [asset]


class Response(io.BytesIO):
    def geturl(self) -> str:
        return "https://example.invalid/asset"


def fake_download(monkeypatch, payload: bytes) -> None:
    class Opener:
        def open(self, url: str, *, timeout: float) -> Response:
            return Response(payload)

    monkeypatch.setattr(archives.urllib.request, "build_opener", lambda *args: Opener())


def test_download_verifies_raw_bytes_and_preserves_existing_on_failure(tmp_path: Path, monkeypatch) -> None:
    payload = b"release\r\n\x00\xff"
    target = tmp_path / "asset"
    fake_download(monkeypatch, payload)
    download_asset("https://example.invalid/asset", target, expected_sha256=sha256(payload))
    assert target.read_bytes() == payload
    for expected, limits in [("0" * 64, ArchiveLimits()), (sha256(payload), ArchiveLimits(archive_bytes=1))]:
        with pytest.raises(ValueError):
            download_asset("https://example.invalid/asset", target, expected_sha256=expected, limits=limits)
        assert target.read_bytes() == payload


@pytest.mark.parametrize("url", ["http://example.invalid/a", "file:///etc/passwd", "https://user:password@example.invalid/a", "https://example.invalid/a#frag"])
def test_download_rejects_invalid_urls_before_network(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        download_asset(url, tmp_path / "asset", expected_sha256="0" * 64)
    assert not list(tmp_path.iterdir())


def test_redirect_cannot_downgrade_https() -> None:
    redirect = archives._HTTPSRedirect()
    with pytest.raises(ValueError, match="HTTPS"):
        redirect.redirect_request(None, None, 302, "redirect", {}, "http://example.invalid/asset")


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5])
def test_limits_require_positive_integers(invalid) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ArchiveLimits(members=invalid)
