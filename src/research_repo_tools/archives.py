"""Bounded HTTPS asset retrieval and portable, staged archive extraction."""

import io
import logging
import math
import ntpath
import os
import shutil
import stat
import tarfile
import tempfile
import unicodedata
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

from research_repo_tools.evidence import _digest, verify_sha256
from research_repo_tools.files import replace_many

__all__ = ["ArchiveLimits", "download_asset", "extract_archive"]
LOGGER = logging.getLogger(__name__)

_ARCHIVE_ERRORS: tuple[type[Exception], ...] = (tarfile.TarError, zipfile.BadZipFile, EOFError, zlib.error)
# These standard-library decoders can be omitted from a Python build. Match
# tarfile's optional support without making unrelated commands require them.
try:
    from lzma import LZMAError
except ImportError:
    pass
else:
    _ARCHIVE_ERRORS += (LZMAError,)
try:
    from compression.zstd import ZstdError
except ImportError:
    pass
else:
    _ARCHIVE_ERRORS += (ZstdError,)


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Positive byte/member bounds applied to both archive formats."""

    archive_bytes: int = 256 * 1024 * 1024
    members: int = 100_000
    content_bytes: int = 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in ("archive_bytes", "members", "content_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"archive limit {name} must be a positive integer")


def _https(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("asset URL must be HTTPS without credentials or a fragment")
    return url


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _https(newurl))


def download_asset(url: str, destination: Path, *, expected_sha256: str, limits: ArchiveLimits = ArchiveLimits(), timeout: float = 60) -> None:
    """Fetch HTTPS bytes with a required independently supplied SHA-256 digest.

    Reject HTTP redirects and oversize responses; publish only after a complete
    verified download. No credentials or release discovery are supplied. Timeout
    is a positive finite per-socket timeout, not a total download deadline.
    """
    _https(url)
    _digest(expected_sha256, "expected digest")
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("asset timeout must be finite and positive")
    opener = urllib.request.build_opener(_HTTPSRedirect())
    with opener.open(url, timeout=timeout) as response:
        _https(response.geturl())
        payload = response.read(limits.archive_bytes + 1)
    if len(payload) > limits.archive_bytes:
        raise ValueError("release asset exceeds the archive byte limit")
    verify_sha256(payload, expected_sha256)
    replace_many({destination: payload})


def _member_path(name: str, *, directory: bool) -> PurePosixPath:
    # Common tar producers prefix relative entries with './'. It is harmless,
    # unlike an internal traversal component. A root directory entry is handled
    # separately by the caller.
    while name.startswith("./"):
        name = name[2:]
    if directory:
        name = name.removesuffix("/")
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        raise ValueError(f"unsafe archive path: {name!r}")
    parts = name.split("/")
    for part in parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or "\x7f" in part
            or ntpath.isreserved(part)
            or any(unicodedata.category(char) == "Cs" for char in part)
        ):
            raise ValueError(f"unsafe or nonportable archive path: {name!r}")
    return PurePosixPath(*parts)


def _portable_key(path: PurePosixPath) -> str:
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def _copy_member(source: IO[bytes], target: Path, size: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as output:
        remaining = size
        while remaining:
            block = source.read(min(remaining, 1024 * 1024))
            if not block:
                raise ValueError(f"truncated archive member: {target.name}")
            output.write(block)
            remaining -= len(block)
        if source.read(1):
            raise ValueError(f"archive member exceeds its declared size: {target.name}")


def _extract(archive: tarfile.TarFile | zipfile.ZipFile, stage: Path, limits: ArchiveLimits) -> None:
    entries: list[tuple[PurePosixPath, tarfile.TarInfo | zipfile.ZipInfo, bool, int]] = []
    names: dict[str, bool] = {}
    # Include implicit directories to catch portable aliases even when an
    # archive omits explicit directory entries (A/x and a/y, for example).
    spellings: dict[str, str] = {}
    total = 0
    members = archive if isinstance(archive, tarfile.TarFile) else archive.infolist()
    for count, member in enumerate(members, 1):
        if count > limits.members:
            raise ValueError("archive contains too many members")
        if isinstance(member, tarfile.TarInfo):
            name, directory, size = member.name, member.isdir(), member.size
            if not (directory or member.isfile()) or member.issparse():
                raise ValueError(f"unsupported archive entry (links/special/sparse files): {name}")
        else:
            name, directory, size = member.orig_filename, member.is_dir(), member.file_size
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if member.flag_bits & 1 or kind not in {0, stat.S_IFDIR if directory else stat.S_IFREG}:
                raise ValueError(f"unsupported archive entry (links/special/encrypted files): {name}")
        if directory and name in {".", "./"}:
            continue
        path = _member_path(name, directory=directory)
        key = _portable_key(path)
        if key in names:
            raise ValueError(f"duplicate archive path: {name}")
        for component in (path, *path.parents):
            if component == PurePosixPath("."):
                continue
            portable = _portable_key(component)
            spelling = component.as_posix()
            if portable in spellings and spellings[portable] != spelling:
                raise ValueError(f"archive paths alias on supported platforms: {name}")
            spellings[portable] = spelling
        names[key] = directory
        if size < 0:
            raise ValueError(f"archive member has a negative size: {name}")
        if not directory:
            total += size
            if total > limits.content_bytes:
                raise ValueError("archive expands beyond the content byte limit")
        entries.append((path, member, directory, size))
    for path, _, _, _ in entries:
        if any(names.get(_portable_key(parent)) is False for parent in path.parents):
            raise ValueError(f"archive file overlaps a directory: {path}")
    for path, member, directory, size in entries:
        target = stage.joinpath(*path.parts)
        if directory:
            target.mkdir(parents=True, exist_ok=True)
        elif isinstance(archive, tarfile.TarFile) and isinstance(member, tarfile.TarInfo):
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"archive member has no file content: {path}")
            with source:
                _copy_member(source, target, size)
        elif isinstance(archive, zipfile.ZipFile) and isinstance(member, zipfile.ZipInfo):
            with archive.open(member) as source:
                _copy_member(source, target, size)


def extract_archive(archive_path: Path, destination: Path, *, limits: ArchiveLimits = ArchiveLimits(), expected_sha256: str | None = None) -> None:
    """Extract tar (including compressed tar) or ZIP into an absent directory.

    Preflight names, types, collisions and size limits; extract regular files
    into a private sibling staging directory and rename only after success.
    Reject links, devices, sparse/encrypted files and nonportable paths. Do not
    restore ownership, modes or timestamps. The destination parent must exist.
    Callers must exclude concurrent writers to the destination/parent.
    """
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"archive destination must be absent: {destination}")
    destination = destination.absolute()
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"archive destination parent must exist: {destination.parent}")
    with archive_path.open("rb") as stream:
        payload = stream.read(limits.archive_bytes + 1)
    if len(payload) > limits.archive_bytes:
        raise ValueError("archive exceeds the archive byte limit")
    if expected_sha256 is not None:
        verify_sha256(payload, expected_sha256)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        data = io.BytesIO(payload)
        try:
            if zipfile.is_zipfile(data):
                with zipfile.ZipFile(data) as archive:
                    _extract(archive, stage, limits)
            else:
                data.seek(0)
                with tarfile.open(fileobj=data, mode="r:*") as archive:
                    _extract(archive, stage, limits)
        except _ARCHIVE_ERRORS as error:
            raise ValueError(f"invalid release archive {archive_path}: {error}") from error
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"archive destination appeared during extraction: {destination}")
        stage.rename(destination)
    finally:
        if stage.exists():
            try:
                shutil.rmtree(stage)
            except OSError as error:
                LOGGER.warning("Could not remove archive staging directory %s: %s", stage, error)
