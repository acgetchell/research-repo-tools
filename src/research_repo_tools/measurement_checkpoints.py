"""Exclusive, immutable journals of validated acquisition units."""

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from research_repo_tools.evidence import _load_json, _object, deterministic_json, sha256
from research_repo_tools.files import replace_many

__all__ = ["Checkpoint", "open_checkpoint"]


class Checkpoint:
    """Owned checkpoint; final unit files are the journal, without a mutable index."""

    def __init__(self, root: Path, units: tuple[str, ...], *, events: bool = False) -> None:
        self.root = root
        self.units = units
        self._active = False
        self.events_enabled = events
        self._event_count: int | None = None
        self._event_tail = "0" * 64

    def _require_owner(self) -> None:
        if not self._active:
            raise ValueError("checkpoint handle is closed; use open_checkpoint for exclusive ownership")

    def read(self) -> dict[str, dict[str, object]]:
        self._require_owner()
        accepted = {}
        allowed = {"header.json", ".writer", *(f"unit-{name}.json" for name in self.units)}
        if self.events_enabled:
            allowed.add("events.jsonl")
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
            if path.name in {"header.json", "events.jsonl"}:
                continue
            record = _object(_load_json(path.read_bytes(), "checkpoint unit"), "checkpoint unit", {"sha256", "unit"})
            unit = _object(record["unit"], "checkpoint unit data")
            if sha256(deterministic_json(unit)) != record["sha256"]:
                raise ValueError(f"corrupt checkpoint unit: {path.name}")
            accepted[path.name.removeprefix("unit-").removesuffix(".json")] = unit
        return accepted

    def read_events(self) -> list[dict[str, object]]:
        """Reject partial records and broken chains; never treat them as retries."""
        self._require_owner()
        if not self.events_enabled:
            return []
        path = self.root / "events.jsonl"
        if path.is_symlink() or path.is_junction() or not path.is_file():
            raise ValueError("checkpoint event journal is missing or not a regular file")
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError("partial checkpoint event journal")
        events = []
        previous = "0" * 64
        for index, line in enumerate(data.splitlines()):
            record = _object(_load_json(line, "checkpoint event"), "checkpoint event", {"sequence", "previous", "event", "sha256"})
            event = _object(record["event"], "checkpoint event data")
            content = {"sequence": index, "previous": previous, "event": event}
            if (
                type(record["sequence"]) is not int
                or record["sequence"] != index
                or record["previous"] != previous
                or record["sha256"] != sha256(deterministic_json(content))
            ):
                raise ValueError("corrupt checkpoint event chain")
            previous = record["sha256"]
            events.append(event)
        self._event_count, self._event_tail = len(events), previous
        return events

    def append_event(self, event: dict[str, object]) -> None:
        """Durably append under the existing exclusive writer, closing before return."""
        self._require_owner()
        if not self.events_enabled:
            raise ValueError("legacy checkpoint has no recovery event journal")
        if self._event_count is None:
            self.read_events()
        assert self._event_count is not None
        content = {"sequence": self._event_count, "previous": self._event_tail, "event": event}
        record = {**content, "sha256": sha256(deterministic_json(content))}
        data = (json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        with (self.root / "events.jsonl").open("ab") as output:
            if output.write(data) != len(data):
                raise OSError("short checkpoint event write")
            output.flush()
            os.fsync(output.fileno())
        self._event_count += 1
        self._event_tail = record["sha256"]

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
def open_checkpoint(path: Path, identity: dict[str, object], units: tuple[str, ...], *, resume: bool, events: bool = False) -> Iterator[Checkpoint]:
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
    body_error = None
    try:
        header = path / "header.json"
        if header.is_symlink() or header.is_junction():
            raise ValueError("checkpoint header must be a regular file")
        expected = {"schema": "research-repo-tools/acquisition-checkpoint/v1", "identity": identity, "units": units}
        legacy = dict(expected)
        if events:
            expected["schema"] = "research-repo-tools/acquisition-checkpoint/v2"
        if resume:
            if not header.is_file():
                raise ValueError("checkpoint header is missing; use a new path for a fresh run")
            actual = _object(_load_json(header.read_bytes(), "checkpoint header"), "checkpoint header")
            if events and deterministic_json(actual) == deterministic_json(legacy):
                expected = legacy
                events = False
            if deterministic_json(actual) != deterministic_json(expected):
                previous = _object(actual.get("identity", {}), "checkpoint identity")
                changed = sorted(key for key in set(previous) | set(identity) if previous.get(key) != identity.get(key))
                raise ValueError(f"checkpoint identity changed ({', '.join(changed) or 'schema/units'}); use a new path for a fresh run")
        else:
            replace_many({header: deterministic_json(expected)})
            if events:
                (path / "events.jsonl").write_bytes(b"")
        journal = Checkpoint(path, units, events=events)
        journal._active = True
        try:
            yield journal
        finally:
            journal._active = False
    except BaseException as error:
        body_error = error
        raise
    finally:
        try:
            lock.rmdir()
        except BaseException as error:
            error.add_note(f"checkpoint lock cleanup failed at {lock}; inspect before resuming")
            if body_error is not None:
                raise BaseExceptionGroup("checkpoint operation and lock cleanup failed", [body_error, error]) from None
            raise
