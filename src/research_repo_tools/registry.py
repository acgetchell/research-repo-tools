"""Bounded, read-only exact-version verification for PyPI and crates.io."""

import hashlib
import http.client
import re
import time
import urllib.error
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from packaging.utils import canonicalize_name

from research_repo_tools import _registry_requests
from research_repo_tools._registry_requests import RegistryHTTPSHandler as RegistryHTTPSHandler
from research_repo_tools._registry_requests import RegistryLookupError as RegistryLookupError
from research_repo_tools._registry_requests import TransientRegistryError as TransientRegistryError
from research_repo_tools.evidence import _load_json
from research_repo_tools.release_validation import MAX_BYTES, distribution_names

LIMIT = 1024 * 1024
_NOT_FOUND = object()


def pypi_destination(url: str) -> str:
    """Require an explicit HTTPS PyPI JSON API origin/prefix, without credentials."""
    if not isinstance(url, str) or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("PyPI destination must be an HTTPS URL without whitespace or control characters")
    try:
        parsed = urlsplit(url)
        port = parsed.port  # urlsplit defers port syntax and range checks until this property is read.
    except ValueError:
        raise ValueError("PyPI destination requires a valid HTTPS authority and port") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in url
        or "#" in url
        or parsed.netloc.endswith(":")
        or url.endswith("/")
        or "\\" in url
    ):
        raise ValueError("PyPI destination must be an HTTPS origin/prefix without credentials, query, fragment or trailing slash")
    if parsed.hostname == "pypi.org" and port in (None, 443):
        return parsed._replace(netloc="pypi.org").geturl()
    return url


def verify_published_distributions(package: str, version: str, files: Mapping[str, str], *, index_url: str = "https://pypi.org") -> dict[str, str]:
    """Compare exact registry inventory, declared hashes and downloaded bytes.

    Unlike lookup_version, absence is an error here. Callers supply hashes from
    live verified evidence, never from the registry being checked. Public PyPI
    reads use the bounded GET retry policy. There are no uploads or rebuilds.
    """
    files = dict(files)
    names = distribution_names(f"v{version}", package=package, filenames=tuple(files))
    if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in files.values()):  # ty: ignore[redundant-condition-strict]  Validate untyped evidence assertions.
        raise ValueError("published distribution assertions require SHA-256 hashes")
    index = pypi_destination(index_url)
    public_pypi = urlsplit(index).netloc == "pypi.org"
    url = f"{index}/pypi/{canonicalize_name(package)}/{version}/json"
    data = _json(url)
    if not isinstance(data, dict):
        raise RegistryLookupError("published distributions are missing or malformed")
    info, uploads = data.get("info"), data.get("urls")
    if (
        not isinstance(info, dict)
        or not isinstance(info.get("name"), str)
        or canonicalize_name(info["name"]) != canonicalize_name(package)
        or info.get("version") != version
        or not isinstance(uploads, list)
        or len(uploads) != len(names)
        or any(not isinstance(upload, dict) or not isinstance(upload.get("filename"), str) for upload in uploads)
        or sorted(upload.get("filename", "") for upload in uploads) != list(names)
    ):
        raise RegistryLookupError("published package identity or distribution inventory differs")
    result = {}
    for upload in uploads:
        name = upload["filename"]
        assert isinstance(name, str)
        digest = files[name]
        if (
            upload.get("yanked") is not False
            or upload.get("packagetype") != ("bdist_wheel" if name.endswith(".whl") else "sdist")
            or not isinstance(upload.get("digests"), dict)
            or upload["digests"].get("sha256") != digest
            or type(upload.get("size")) is not int
            or not 0 < upload["size"] <= MAX_BYTES
            or not isinstance(upload.get("url"), str)
        ):
            raise RegistryLookupError("published distribution is yanked or differs from expected bytes")
        host = urlsplit(index).netloc
        try:
            download = pypi_destination(upload["url"])
            host = urlsplit(download).netloc
            if public_pypi:
                if host != "files.pythonhosted.org":
                    raise RegistryLookupError("published distribution requires the exact files.pythonhosted.org origin")
                payload = _registry_requests.read_registry(download, limit=MAX_BYTES)
            else:
                payload = _registry_requests.read_custom_index(download, limit=MAX_BYTES, metadata=False)
        except RegistryLookupError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise RegistryLookupError(f"{host} {name}: published distribution download failed") from error
        if len(payload) != upload["size"] or hashlib.sha256(payload).hexdigest() != digest:
            raise RegistryLookupError("published distribution bytes differ from verified evidence")
        result[name] = digest
    return result


@dataclass(frozen=True, slots=True)
class RegistryVersion:
    """The result of an exact-version lookup, including publication status and an optional checksum."""

    registry: Literal["pypi", "crates-io"]
    package: str
    version: str
    present: bool
    checksum: str | None = None


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
    parsed = urlsplit(url)
    identity = _registry_requests._identity(parsed)
    try:
        if parsed.netloc in {"pypi.org", "crates.io"}:
            payload = _registry_requests.read_registry(url, limit=LIMIT)
        else:
            payload = _registry_requests.read_custom_index(url, limit=LIMIT, metadata=True)
    except urllib.error.HTTPError as error:
        if error.code == 404 and error.url == url:
            return _NOT_FOUND
        raise RegistryLookupError(f"registry lookup failed with HTTP {error.code} for {identity}; absence is unproven") from error
    except (OSError, http.client.HTTPException) as error:
        raise RegistryLookupError(f"registry lookup failed for {identity}; absence is unproven") from error
    except RegistryLookupError:
        raise
    except ValueError as error:
        raise RegistryLookupError(str(error)) from error
    if len(payload) > LIMIT:
        raise RegistryLookupError("registry response exceeds the byte limit")
    try:
        return _load_json(payload, "registry response")
    except ValueError as error:
        raise RegistryLookupError("registry returned malformed JSON") from error


def lookup_version(registry: Literal["pypi", "crates-io"], package: str, version: str, *, index_url: str = "https://pypi.org") -> RegistryVersion:
    """Only an exact endpoint's HTTP 404 proves absence; all other failures raise.

    A visible version must match identity and contain non-yanked upload evidence.
    This verifies registry metadata, not byte provenance or scientific correctness.
    """
    identity(registry, package, version)
    if registry != "pypi" and index_url != "https://pypi.org":
        raise ValueError("index_url is only supported for PyPI")
    url = (
        f"{pypi_destination(index_url)}/pypi/{canonicalize_name(package)}/{version}/json"
        if registry == "pypi"
        else f"https://crates.io/api/v1/crates/{package}/{version}"
    )
    data = _json(url)
    if data is _NOT_FOUND:
        return RegistryVersion(registry, package, version, False)
    if not isinstance(data, dict):
        raise RegistryLookupError("registry response must be an object")
    checksum = None
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
    return RegistryVersion(registry, package, version, True, checksum)


def wait_for_version(registry: Literal["pypi", "crates-io"], package: str, version: str, *, attempts: int = 1, interval: int = 10) -> RegistryVersion:
    """Poll proven absence at most 31 times with up to 300s total inter-poll delay.

    Public registry GETs separately allow three attempts within the configured
    timeout. Exhausted lookups and permanent/integrity failures
    stop polling; only an exact 404 permits the next poll. Never retry uploads.
    """
    if type(attempts) is not int or not 1 <= attempts <= 31 or type(interval) is not int or not 0 <= interval <= 10:
        raise ValueError("registry wait requires 1..31 attempts and a 0..10 second interval")
    for attempt in range(attempts):
        result = lookup_version(registry, package, version)
        if result.present or attempt == attempts - 1:
            return result
        time.sleep(interval)
    raise AssertionError("unreachable")
