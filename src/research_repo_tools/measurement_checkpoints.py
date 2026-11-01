"""Exclusive, immutable journals of validated acquisition units."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from research_repo_tools.evidence import _load_json, _object, deterministic_json, sha256
from research_repo_tools.files import replace_many

__all__ = ["Checkpoint", "open_checkpoint"]


class Checkpoint:
    """Owned checkpoint; final unit files are the journal, without a mutable index."""

    def __init__(self, root: Path, units: tuple[str, ...]) -> None:
        self.root = root
        self.units = units
        self._active = False

    def _require_owner(self) -> None:
        if not self._active:
            raise ValueError("checkpoint handle is closed; use open_checkpoint for exclusive ownership")

    def read(self) -> dict[str, dict[str, object]]:
        self._require_owner()
        accepted = {}
        allowed = {"header.json", ".writer", *(f"unit-{name}.json" for name in self.units)}
        for path in self.root.iterdir():
            if path.is_symlink() or path.is_junction():
                raise ValueError("checkpoint contains a link or junction")
            if path.name == ".writer":
                continue
            if not path.is_file():
                raise ValueError("checkpoint entries must be regular files")
            if path.name not in allowed:
                # Only unpublished files created by replace_many are leftovers.
                if re.fullmatch(r"\.(?:header|unit-[a-z][a-z0-9-]*)\.json\.[0-9a-f]{24}\.(?:tmp|bak)", path.name):
                    continue
                raise ValueError(f"unexpected checkpoint entry: {path.name}")
            if path.name == "header.json":
                continue
            record = _object(_load_json(path.read_bytes(), "checkpoint unit"), "checkpoint unit", {"sha256", "unit"})
            unit = _object(record["unit"], "checkpoint unit data")
            if sha256(deterministic_json(unit)) != record["sha256"]:
                raise ValueError(f"corrupt checkpoint unit: {path.name}")
            accepted[path.name.removeprefix("unit-").removesuffix(".json")] = unit
        return accepted

    def accept(self, name: str, unit: dict[str, object]) -> None:
        """Commit one already validated complete unit, preserving earlier bytes."""
        self._require_owner()
        if name not in self.units:
            raise ValueError("unknown checkpoint unit")
        path = self.root / f"unit-{name}.json"
        if path.exists():
            raise ValueError("checkpoint unit is immutable")
        replace_many({path: deterministic_json({"sha256": sha256(deterministic_json(unit)), "unit": unit})})


@contextmanager
def open_checkpoint(path: Path, identity: dict[str, object], units: tuple[str, ...], *, resume: bool) -> Iterator[Checkpoint]:
    """Never steal locks or reinterpret corruption as unfinished work.

    File data is fsynced before atomic replacement. Directory-entry persistence
    across sudden power loss depends on the native filesystem; a missing/corrupt
    header or corrupt final unit is rejected. Missing units are recollected.
    A hard-kill lock requires operator recovery; handles expire on context exit.
    """
    path = path.absolute()
    from research_repo_tools.publication import _publication_name

    _publication_name(path.name)
    if (
        any(part.casefold() == ".git" for part in path.parts)
        or not units
        or len(set(units)) != len(units)
        or any(re.fullmatch(r"[a-z][a-z0-9-]*", name) is None for name in units)
    ):
        raise ValueError("checkpoint requires safe paths and unique portable unit names")
    if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)):
        raise ValueError("checkpoint path must not contain links or junctions")
    if not path.parent.is_dir():
        raise ValueError("checkpoint parent must already exist")
    if resume:
        if not path.is_dir():
            raise ValueError("resume requires an existing checkpoint; use a new path for a fresh run")
    else:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError as error:
            raise ValueError("checkpoint already exists; use --resume or a new path for a fresh run") from error
    lock = path / ".writer"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as error:
        raise ValueError("checkpoint has an active or unresolved writer; verify exclusive ownership before manual lock recovery") from error
    try:
        header = path / "header.json"
        if header.is_symlink() or header.is_junction():
            raise ValueError("checkpoint header must be a regular file")
        expected = {"schema": "research-repo-tools/acquisition-checkpoint/v1", "identity": identity, "units": units}
        if resume:
            if not header.is_file():
                raise ValueError("checkpoint header is missing; use a new path for a fresh run")
            actual = _object(_load_json(header.read_bytes(), "checkpoint header"), "checkpoint header")
            if deterministic_json(actual) != deterministic_json(expected):
                previous = _object(actual.get("identity", {}), "checkpoint identity")
                changed = sorted(key for key in set(previous) | set(identity) if previous.get(key) != identity.get(key))
                raise ValueError(f"checkpoint identity changed ({', '.join(changed) or 'schema/units'}); use a new path for a fresh run")
        else:
            replace_many({header: deterministic_json(expected)})
        journal = Checkpoint(path, units)
        journal._active = True
        try:
            yield journal
        finally:
            journal._active = False
    finally:
        lock.rmdir()
