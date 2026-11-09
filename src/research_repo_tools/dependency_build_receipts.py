"""Machine-local authenticated source-build evidence, never a PyPI wheel claim.

The signing store is outside the checkout and is part of the trusted local
runtime/filesystem boundary. These receipts are not portable attestations and
do not establish hermeticity, reproducibility, or upstream code correctness.
"""

import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import parse_wheel_filename

from research_repo_tools.dependency_build_policy import member_name, native_platform
from research_repo_tools.dependency_sources import table
from research_repo_tools.evidence import _load_json
from research_repo_tools.files import replace_many

MAX_WHEEL_BYTES = 256 * 1024 * 1024
SCHEMA = "research-repo-tools/source-build/v1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def read_regular(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"build evidence requires a regular file: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"build evidence exceeds its byte limit: {path.name}")
    return data


def context(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    return {
        "root": str(root),
        "platform": native_platform(),
        "system": platform.platform(),
        "python": sys.version,
        "interpreter_sha256": digest(read_regular(Path(sys.executable).resolve(), MAX_WHEEL_BYTES)),
        "builder_sha256": digest(canonical({path.name: digest(path.read_bytes()) for path in Path(__file__).parent.glob("*.py")})),
        "inputs": {name: digest(read_regular(root / name, 32 * 1024 * 1024)) for name in ("pyproject.toml", "uv.lock", "dependency-sources.toml")},
    }


def build_store(root: Path, directory: Path | None = None) -> Path:
    selected = directory or Path(os.environ.get("RESEARCH_REPO_TOOLS_BUILD_STORE", str(Path.home() / ".research-repo-tools" / "source-builds")))
    if not selected.is_absolute() or selected.is_symlink() or selected.is_junction():
        raise ValueError("source-build store must be an absolute directory without links")
    resolved = selected.resolve()
    if resolved.is_relative_to(root.resolve()) or root.resolve().is_relative_to(resolved):
        raise ValueError("source-build signing store must be separate from the checkout")
    return resolved


def _key(directory: Path, *, create: bool = False) -> bytes:
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        raise ValueError("source-build signing store is absent or unsafe; prepare dependency builds first")
    if os.name != "nt" and directory.stat().st_mode & 0o077:
        raise ValueError("source-build signing store must be private to its owner")
    path = directory / "key"
    if create and not path.exists():
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secrets.token_bytes(32))
    key = read_regular(path, 32)
    if len(key) != 32 or os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("source-build signing key is invalid or not private")
    return key


def _entry(root: Path, package: str) -> str:
    return digest(canonical({"root": str(root.resolve()), "package": package}))


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("build receipt requires a SHA-256")
    return value


@dataclass(frozen=True, slots=True, init=False)
class VerifiedBuild:
    """Evidence obtained only by authenticating a current local receipt."""

    wheel: Path
    payload: bytes

    def __init__(self) -> None:
        raise TypeError("use verify_build_receipt")


def _publish_build_receipt(
    root: Path, package: str, version: str, payload: dict[str, object], wheel: Path, *, build_log: bytes, directory: Path | None = None
) -> VerifiedBuild:
    """Called by the trusted builder after source/input verification and build."""
    store = build_store(root, directory)
    if set(payload) != {"source", "tools", "external", "build_log_sha256"}:
        raise ValueError("builder receipt fields are incomplete or unknown")
    if len(build_log) > 4 * 1024 * 1024 or digest(build_log) != payload["build_log_sha256"]:
        raise ValueError("build log differs from the receipt or exceeds its byte limit")
    data = read_regular(wheel, MAX_WHEEL_BYTES)
    name, parsed_version, _, _ = parse_wheel_filename(wheel.name)
    if name != package or str(parsed_version) != version:
        raise ValueError("built wheel identity differs from the requested source")
    record = {
        "schema": SCHEMA,
        "context": context(root),
        "package": package,
        "version": version,
        **payload,
        "wheel": {"filename": wheel.name, "sha256": digest(data)},
    }
    key = _key(store, create=True)
    object_dir = store / digest(data)
    object_dir.mkdir(exist_ok=True, mode=0o700)
    if object_dir.is_symlink() or object_dir.is_junction():
        raise ValueError("build object directory cannot be a link")
    envelope = {"payload": record, "mac": hmac.new(key, canonical(record), "sha256").hexdigest()}
    replace_many(
        {object_dir / wheel.name: data, store / (digest(build_log) + ".log"): build_log, store / (_entry(root, package) + ".json"): canonical(envelope)}
    )
    return verify_build_receipt(root, package, version, table(payload["source"], "source"), directory=store)


def verify_build_receipt(root: Path, package: str, version: str, source: dict[str, object], *, directory: Path | None = None) -> VerifiedBuild:
    """Authenticate the receipt and bind it to live inputs and retained bytes."""
    store = build_store(root, directory)
    key = _key(store)
    path = store / (_entry(root, package) + ".json")
    if not path.exists():
        raise ValueError(f"{package}: source-build evidence is missing; run release dependency-builds")
    envelope = table(_load_json(read_regular(path, 4 * 1024 * 1024), "source-build receipt"), "receipt")
    if set(envelope) != {"payload", "mac"}:
        raise ValueError("source-build receipt envelope is malformed")
    payload = table(envelope["payload"], "receipt payload")
    if not hmac.compare_digest(_sha(envelope["mac"]), hmac.new(key, canonical(payload), "sha256").hexdigest()):
        raise ValueError("source-build receipt authentication failed")
    if set(payload) != {"schema", "context", "package", "version", "source", "tools", "external", "build_log_sha256", "wheel"}:
        raise ValueError("source-build receipt fields are incomplete or unknown")
    if (
        payload["schema"] != SCHEMA
        or payload["package"] != package
        or payload["version"] != version
        or payload["source"] != source
        or payload["context"] != context(root)
    ):
        raise ValueError(f"{package}: source-build receipt is stale or belongs to different inputs/platform; rebuild dependency evidence")
    if not table(payload["tools"], "build tools") or not isinstance(payload["external"], list):
        raise ValueError("source-build receipt requires build tools and external input inventory")
    log_hash = _sha(payload["build_log_sha256"])
    if digest(read_regular(store / (log_hash + ".log"), 4 * 1024 * 1024)) != log_hash:
        raise ValueError("retained build log differs from the authenticated receipt")
    wheel = table(payload["wheel"], "built wheel")
    if set(wheel) != {"filename", "sha256"}:
        raise ValueError("built wheel receipt is malformed")
    filename = member_name(wheel["filename"])
    if "/" in filename:
        raise ValueError("built wheel requires a plain filename")
    name, parsed_version, _, _ = parse_wheel_filename(filename)
    if name != package or str(parsed_version) != version:
        raise ValueError("built wheel identity differs from receipt")
    expected = _sha(wheel["sha256"])
    wheel_path = store / expected / filename
    if wheel_path.parent.is_symlink() or wheel_path.parent.is_junction():
        raise ValueError("build object directory cannot be a link")
    data = read_regular(wheel_path, MAX_WHEEL_BYTES)
    if digest(data) != expected:
        raise ValueError("retained source-build wheel differs from authenticated receipt")
    result = object.__new__(VerifiedBuild)
    object.__setattr__(result, "wheel", wheel_path)
    object.__setattr__(result, "payload", data)
    return result
