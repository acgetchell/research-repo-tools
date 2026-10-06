"""Bounded, read-only exact-version verification for PyPI and crates.io."""

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

from packaging.utils import canonicalize_name

from research_repo_tools.evidence import _load_json

LIMIT = 1024 * 1024
_NOT_FOUND = object()


class RegistryLookupError(ValueError):
    """Registry evidence is unavailable or malformed, rather than absent."""


@dataclass(frozen=True, slots=True)
class RegistryVersion:
    registry: Literal["pypi", "crates-io"]
    package: str
    version: str
    present: bool


def identity(registry: str, package: str, version: str) -> None:
    """Accept only supported registries, package names, and stable versions."""
    if registry not in {"pypi", "crates-io"}:
        raise ValueError("registry must be pypi or crates-io")
    if not isinstance(package, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", package) is None:
        raise ValueError("invalid registry package name")
    if registry == "crates-io" and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", package) is None:
        raise ValueError("invalid crates.io package name")
    if not isinstance(version, str) or re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version) is None:
        raise ValueError("registry verification requires a stable X.Y.Z version")


def _json(url: str) -> object:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "research-repo-tools (https://github.com/acgetchell/research-repo-tools)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read(LIMIT + 1)
            if response.status != 200 or response.url != url:
                raise RegistryLookupError("registry returned an unexpected status or redirect")
    except urllib.error.HTTPError as error:
        with error:
            if error.code == 404 and error.url == url:
                return _NOT_FOUND
            raise RegistryLookupError(f"registry lookup failed with HTTP {error.code}; absence is unproven") from error
    except (OSError, urllib.error.URLError) as error:
        raise RegistryLookupError("registry lookup failed; absence is unproven") from error
    if len(payload) > LIMIT:
        raise RegistryLookupError("registry response exceeds the byte limit")
    try:
        return _load_json(payload, "registry response")
    except ValueError as error:
        raise RegistryLookupError("registry returned malformed JSON") from error


def lookup_version(registry: Literal["pypi", "crates-io"], package: str, version: str) -> RegistryVersion:
    """Only an exact endpoint's HTTP 404 proves absence; all other failures raise.

    A visible version must match identity and contain non-yanked upload evidence.
    This verifies registry metadata, not byte provenance or scientific correctness.
    """
    identity(registry, package, version)
    url = f"https://pypi.org/pypi/{canonicalize_name(package)}/{version}/json" if registry == "pypi" else f"https://crates.io/api/v1/crates/{package}/{version}"
    data = _json(url)
    if data is _NOT_FOUND:
        return RegistryVersion(registry, package, version, False)
    if not isinstance(data, dict):
        raise RegistryLookupError("registry response must be an object")
    if registry == "pypi":
        info, uploads = data.get("info"), data.get("urls")
        if not isinstance(info, dict) or not isinstance(info.get("name"), str) or not isinstance(uploads, list) or not uploads:
            raise RegistryLookupError("PyPI response lacks package identity or uploaded files")
        if canonicalize_name(info["name"]) != canonicalize_name(package) or info.get("version") != version:
            raise RegistryLookupError("PyPI package identity differs from the requested version")
        kinds = set()
        for upload in uploads:
            if not isinstance(upload, dict) or upload.get("yanked") is not False:
                raise RegistryLookupError("PyPI upload is malformed or yanked")
            kind = upload.get("packagetype")
            if not isinstance(kind, str) or kind not in {"bdist_wheel", "sdist"}:
                raise RegistryLookupError("unsupported PyPI upload type")
            kinds.add(kind)
        if kinds != {"bdist_wheel", "sdist"}:
            raise RegistryLookupError("PyPI version requires both wheel and sdist uploads")
    else:
        info = data.get("version")
        if not isinstance(info, dict) or info.get("crate") != package or info.get("num") != version or info.get("yanked") is not False:
            raise RegistryLookupError("crates.io package identity differs, is malformed, or is yanked")
        checksum = info.get("checksum")
        if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise RegistryLookupError("crates.io version lacks a valid archive checksum")
    return RegistryVersion(registry, package, version, True)


def wait_for_version(registry: Literal["pypi", "crates-io"], package: str, version: str, *, attempts: int = 1, interval: int = 10) -> RegistryVersion:
    """Retry only proven absence, for at most 31 requests and 300 seconds of delay.

    Each request has a 15-second timeout. Authentication, rate-limit, transport,
    and parsing failures stop immediately. This function never retries uploads.
    """
    if type(attempts) is not int or not 1 <= attempts <= 31 or type(interval) is not int or not 0 <= interval <= 10:
        raise ValueError("registry wait requires 1..31 attempts and a 0..10 second interval")
    for attempt in range(attempts):
        result = lookup_version(registry, package, version)
        if result.present or attempt == attempts - 1:
            return result
        time.sleep(interval)
    raise AssertionError("unreachable")
