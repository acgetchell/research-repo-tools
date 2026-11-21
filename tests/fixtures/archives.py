"""Byte-exact ZIP fixtures for retained artifacts."""

import io
import zipfile
from pathlib import Path


def zip_asset(path: Path | io.BytesIO, entries: list[tuple[str, bytes]]) -> None:
    """Write a ZIP fixture and verify its original member names survived."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries:
            member = zipfile.ZipInfo(name)
            # ZipInfo normalizes Windows separators. Malformed fixtures need
            # the original spelling in both ZIP headers on every platform.
            member.filename = name
            archive.writestr(member, payload)
    with zipfile.ZipFile(path) as archive:
        assert [member.orig_filename for member in archive.infolist()] == [name for name, _ in entries], "ZIP fixture changed its member names"
