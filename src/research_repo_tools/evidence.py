"""Exact-byte evidence, explicit provenance comparisons, and recoverable publication.

Payload schemas and scientific eligibility belong to callers. The versioned
envelope records integrity and source information without rewriting payloads.
"""

import hashlib
import json
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from research_repo_tools.files import _path_keys, _PathKey, _paths_alias, _validate_distinct_paths, replace_many

__all__ = [
    "Compatibility",
    "Difference",
    "Evidence",
    "Provenance",
    "compare_provenance",
    "deterministic_json",
    "fingerprint_files",
    "load_evidence",
    "parse_evidence",
    "publish_evidence",
    "serialize_evidence",
    "sha256",
    "verify_sha256",
]

_SCHEMA = "research-repo-tools/evidence/v1"


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{context} must be a nonempty string without surrounding whitespace or controls")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValueError(f"{context} must be valid UTF-8 text") from error
    return value


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return value


def sha256(payload: bytes) -> str:
    """Hash original bytes without decoding, normalizing, or applying Git filters."""
    if not isinstance(payload, bytes):
        raise TypeError("evidence payload must be bytes")
    return hashlib.sha256(payload).hexdigest()


def verify_sha256(payload: bytes, expected: str) -> None:
    """Raise ValueError when the exact bytes differ from a recorded digest."""
    _digest(expected, "expected digest")
    if sha256(payload) != expected:
        raise ValueError("evidence payload does not match its recorded SHA-256 digest")


def _json_value(value: object) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list | tuple):
        for item in value:
            _json_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError("evidence JSON requires finite JSON values and string object keys")


def deterministic_json(value: object) -> bytes:
    """Sorted, indented UTF-8 JSON with LF and a final newline; reject NaN/Infinity.

    This is a package serialization convention, not RFC 8785 canonical JSON.
    Hash existing evidence directly; do not reserialize it before verification.
    """
    try:
        _json_value(value)
        return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    except RecursionError as error:
        raise ValueError("evidence JSON is cyclic or too deeply nested") from error


def _object(value: object, context: str, keys: set[str] | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be a JSON object")
    result: dict[str, object] = dict(value)
    if keys is not None and set(result) != keys:
        raise ValueError(f"{context} fields must be {sorted(keys)}; found {sorted(result)}")
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_json(payload: bytes, context: str) -> object:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
        _json_value(value)
        return value
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(f"invalid {context}: {error}") from error


@dataclass(frozen=True, slots=True)
class Provenance:
    """A measured source, separate harness digest, and consumer-defined context.

    revision identifies the source commit (full Git SHA-1 or SHA-256). Digests
    may be unknown, represented by None; unknown values never establish a
    compatibility match. Context can record CPU, OS, tool versions, command,
    suite, scope, or measurement mode as strings. Capture these at measurement.
    """

    revision: str
    source_sha256: str | None = None
    harness_sha256: str | None = None
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.revision, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.revision) is None:
            raise ValueError("provenance revision must be a full lowercase Git commit ID")
        for name in ("source_sha256", "harness_sha256"):
            value = getattr(self, name)
            if value is not None:
                _digest(value, name)
        context = tuple((_string(key, "context key"), _string(value, f"context {key}")) for key, value in self.context)
        if len(dict(context)) != len(context):
            raise ValueError("provenance context keys must be unique")
        object.__setattr__(self, "context", tuple(sorted(context)))


@dataclass(frozen=True, slots=True)
class Difference:
    """One requested field that is missing or unequal (None means unknown)."""

    field: str
    baseline: str | None
    current: str | None


@dataclass(frozen=True, slots=True)
class Compatibility:
    """Equality evidence for the requested fields, not scientific acceptance."""

    fields: tuple[str, ...]
    differences: tuple[Difference, ...]

    @property
    def compatible(self) -> bool:
        return not self.differences


def compare_provenance(baseline: Provenance, current: Provenance, *, fields: Sequence[str]) -> Compatibility:
    """Compare explicit required fields; reject an empty or duplicate selection.

    Select revision/source_sha256/harness_sha256 or context.KEY. Select recorded
    versus freshly captured source fields to detect stale provenance; select
    environment/harness fields to assess measurement compatibility. Two unknown
    values are a difference, not evidence of compatibility.
    """
    if isinstance(fields, str) or not fields or len(set(fields)) != len(fields):
        raise ValueError("compatibility requires a nonempty sequence of unique fields")
    differences = []
    for name in sorted(fields):
        if name in {"revision", "source_sha256", "harness_sha256"}:
            left, right = getattr(baseline, name), getattr(current, name)
        elif name.startswith("context.") and name.removeprefix("context."):
            key = name.removeprefix("context.")
            left, right = dict(baseline.context).get(key), dict(current.context).get(key)
        else:
            raise ValueError(f"unknown provenance field: {name}")
        if left is None or right is None or left != right:
            differences.append(Difference(name, left, right))
    return Compatibility(tuple(sorted(fields)), tuple(differences))


@dataclass(frozen=True, slots=True)
class Evidence:
    """Opaque payload bytes with a schema ID and named measured sources.

    Loading verifies integrity and envelope structure. Call the payload schema's
    parser before using measurements. The envelope provides no authentication.
    """

    payload: bytes
    payload_schema: str
    sources: tuple[tuple[str, Provenance], ...]
    _original_manifest: bytes | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        sha256(self.payload)
        _string(self.payload_schema, "payload schema")
        sources = tuple((name, source) for name, source in self.sources)
        if not sources or len(dict(sources)) != len(sources):
            raise ValueError("evidence requires unique named sources")
        for name, source in sources:
            _string(name, "source name")
            if not isinstance(source, Provenance):
                raise TypeError("evidence sources must be Provenance instances")
        object.__setattr__(self, "sources", tuple(sorted(sources)))


def serialize_evidence(evidence: Evidence) -> tuple[bytes, bytes]:
    """Return payload and sidecar bytes, retaining loaded files byte for byte."""
    if evidence._original_manifest is not None:
        return evidence.payload, evidence._original_manifest
    sources = {
        name: {"revision": source.revision, "source_sha256": source.source_sha256, "harness_sha256": source.harness_sha256, "context": dict(source.context)}
        for name, source in evidence.sources
    }
    return evidence.payload, deterministic_json(
        {
            "schema": _SCHEMA,
            "payload_schema": evidence.payload_schema,
            "payload_sha256": sha256(evidence.payload),
            "sources": sources,
        }
    )


def parse_evidence(payload: bytes, manifest: bytes) -> Evidence:
    """Validate the versioned sidecar and exact payload hash before returning."""
    if not isinstance(manifest, bytes):
        raise TypeError("evidence manifest must be bytes")
    document = _object(_load_json(manifest, "evidence manifest"), "evidence manifest", {"schema", "payload_schema", "payload_sha256", "sources"})
    if document["schema"] != _SCHEMA:
        raise ValueError(f"unsupported evidence schema: {document['schema']!r}")
    verify_sha256(payload, _digest(document["payload_sha256"], "payload_sha256"))
    sources = []
    for name, raw in _object(document["sources"], "sources").items():
        source = _object(raw, f"source {name}", {"revision", "source_sha256", "harness_sha256", "context"})
        context = tuple((key, _string(value, f"context {key}")) for key, value in _object(source["context"], "source context").items())
        sources.append(
            (
                name,
                Provenance(
                    _string(source["revision"], "revision"),
                    None if source["source_sha256"] is None else _digest(source["source_sha256"], "source_sha256"),
                    None if source["harness_sha256"] is None else _digest(source["harness_sha256"], "harness_sha256"),
                    context,
                ),
            )
        )
    result = Evidence(payload, _string(document["payload_schema"], "payload_schema"), tuple(sources))
    object.__setattr__(result, "_original_manifest", manifest)
    return result


def load_evidence(payload_path: Path, manifest_path: Path) -> Evidence:
    """Read and verify two retained files without text newline conversion."""
    return parse_evidence(payload_path.read_bytes(), manifest_path.read_bytes())


def publish_evidence(
    evidence: Evidence,
    payload_path: Path,
    manifest_path: Path,
    *,
    reports: Mapping[Path, bytes] | None = None,
    immutable: Collection[Path] = (),
) -> None:
    """Publish verified evidence and pre-rendered reports in one transaction.

    Immutable targets may be absent or already contain identical bytes. All
    targets must be distinct, including aliases, and immutable paths must be
    among them. Consumers validate their payload schema and render reports before
    calling. Shares replace_many's rollback, recovery, and concurrency contract.
    """
    payload, manifest = serialize_evidence(evidence)
    parse_evidence(payload, manifest)
    writes = [(payload_path, payload), (manifest_path, manifest), *(reports or {}).items()]
    paths = _validate_distinct_paths(tuple(path for path, _ in writes))
    if any(not any(_paths_alias(path, target) for target in paths) for path in immutable):
        raise ValueError("immutable paths must be publication targets")
    for (path, data), resolved in zip(writes, paths, strict=True):
        if any(_paths_alias(resolved, protected) for protected in immutable) and path.exists() and path.read_bytes() != data:
            raise ValueError(f"immutable evidence already exists with different bytes: {path}")
    replace_many(dict(writes))


def fingerprint_files(root: Path, paths: Sequence[Path]) -> str:
    """Hash an explicit inventory of relative regular files, including names.

    Framing is versioned and length-prefixed; order does not matter. Reject
    aliases, traversal, and symlinks. This is an exact filesystem fingerprint,
    not a Git tree hash or automatic source discovery. Callers own the inventory
    and must keep it stable during measurement (including additions/deletions).
    """
    root = root.resolve(strict=True)
    inventory: dict[str, Path] = {}
    selected: set[_PathKey] = set()
    for path in paths:
        if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
            raise ValueError(f"fingerprint path must be relative without traversal: {path}")
        target = root / path
        if any(candidate.is_symlink() for candidate in (target, *target.parents) if candidate.is_relative_to(root)):
            raise ValueError(f"fingerprint input must not be a symlink: {path}")
        if not target.is_file():
            raise ValueError(f"fingerprint input must be a regular file: {path}")
        name = path.as_posix()
        keys = _path_keys(target)
        if not selected.isdisjoint(keys):
            raise ValueError(f"duplicate fingerprint input: {path}")
        inventory[name] = target
        selected.update(keys)
    if not inventory:
        raise ValueError("source fingerprint requires a nonempty file inventory")
    digest = hashlib.sha256(b"research-repo-tools/files/v1\0")
    for name, path in sorted(inventory.items()):
        for data in (name.encode("utf-8"), path.read_bytes()):
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    return digest.hexdigest()
