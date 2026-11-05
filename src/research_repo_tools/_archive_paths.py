"""Standard-library archive path checks shared with install-free release runners."""

import ntpath
import unicodedata
from pathlib import PurePosixPath


def _member_path(name: str, *, directory: bool) -> PurePosixPath:
    # Common tar producers prefix relative entries with './'. It is harmless,
    # unlike an internal traversal component. Root entries belong to the caller.
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


class ArchivePaths:
    """Reject portable aliases and file/directory overlaps in either entry order."""

    def __init__(self) -> None:
        self._members: dict[str, bool] = {}
        # Include implicit directories, even when the archive omits their entries.
        self._spellings: dict[str, str] = {}

    def add(self, name: str, *, directory: bool) -> PurePosixPath:
        path = _member_path(name, directory=directory)
        key = _portable_key(path)
        if key in self._members:
            raise ValueError(f"duplicate archive path: {name}")
        spellings = {_portable_key(part): part.as_posix() for part in (path, *path.parents) if part != PurePosixPath(".")}
        if any(key in self._spellings and self._spellings[key] != spelling for key, spelling in spellings.items()):
            raise ValueError(f"archive paths alias on supported platforms: {name}")
        if (not directory and key in self._spellings) or any(self._members.get(_portable_key(parent)) is False for parent in path.parents):
            raise ValueError(f"archive file overlaps a directory: {name}")
        self._members[key] = directory
        self._spellings.update(spellings)
        return path
