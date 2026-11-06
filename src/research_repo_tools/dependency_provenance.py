"""Exact development sources and published upstream tooling at release boundaries.

Development Git identity is offline. Release proof uses PyPI's exact-version
inventory, locked artifact hashes and the bytes of a matching published wheel.
Neither operation resolves, installs, changes pins, or mutates the checkout.
"""

import hashlib
import io
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from packaging.requirements import Requirement
from packaging.tags import parse_tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from research_repo_tools._dependency_probe import inspect_installation
from research_repo_tools._git_checkout import _in_checkout
from research_repo_tools.evidence import _load_json
from research_repo_tools.release_policy import dependency_names

__all__ = ["DependencyIdentity", "check_development_dependency", "check_release_dependencies"]

PACKAGE = "research-repo-tools"
INDEX = "https://pypi.org/simple"
RECOVERY = (
    "Publish the supplying upstream version; replace development source overrides with its exact published pin; "
    "reconcile normal and isolated runtime pins, resolve and sync uv.lock, then revalidate. "
    "Changed dependency or harness inputs invalidate prior acquisition/checkpoint/candidate evidence."
)


@dataclass(frozen=True, slots=True)
class DependencyIdentity:
    """Verified selected dependency; a Git revision never claims publication."""

    name: str
    version: str
    source: str
    revision: str | None = None


def _table(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a table")
    return value


def _uv(document: dict[str, Any]) -> dict[str, Any]:
    return _table(_table(document.get("tool", {}), "tool").get("uv", {}), "tool.uv")


def _source(document: dict[str, Any], name: str) -> object:
    matches = [value for key, value in _table(_uv(document).get("sources", {}), "tool.uv.sources").items() if canonicalize_name(key) == name]
    if len(matches) > 1:
        raise ValueError(f"{name}: ambiguous source aliases")
    return matches[0] if matches else None


def _git(source: object) -> tuple[str, str]:
    if not isinstance(source, dict) or set(source) != {"git", "rev"}:
        raise ValueError("development source requires one Git URL and full commit rev; branches, paths, workspaces and conditional sources are unsupported")
    url, revision = source["git"], source["rev"]
    if not isinstance(url, str) or not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("development Git rev must be a full lowercase 40-character commit")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in url
        or any(character.isspace() or ord(character) < 32 for character in url)
    ):
        raise ValueError("development Git source must be an uncredentialed HTTPS repository URL")
    return url, revision


def validate_development_source(document: dict[str, Any], name: str = PACKAGE) -> None:
    """Validate the declared source before adoption without requiring a new lock."""
    source = _source(document, name)
    if source is not None:
        _git(source)


def _requirements(document: dict[str, Any], name: str) -> tuple[Requirement, ...]:
    project = _table(document.get("project", {}), "project")
    groups = _table(document.get("dependency-groups", {}), "dependency-groups")
    optional = _table(project.get("optional-dependencies", {}), "project.optional-dependencies")
    arrays = [project.get("dependencies", []), *groups.values(), *optional.values()]
    selected = []
    for entries in arrays:
        if not isinstance(entries, list):
            raise ValueError("dependency declarations must be arrays")
        for raw in entries:
            if isinstance(raw, dict) and set(raw) == {"include-group"}:
                continue
            if not isinstance(raw, str):
                raise ValueError("dependency declarations must contain requirements or group includes")
            item = Requirement(raw)
            if canonicalize_name(item.name) == name:
                specs = list(item.specifier)
                if item.url or item.marker or len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version:
                    raise ValueError(f"{name}: requires unmarked exact ==version declarations")
                selected.append(item)
    if not selected or len({str(item.specifier) for item in selected}) != 1:
        raise ValueError(f"{name}: missing or inconsistent exact dependency declarations")
    for key in ("constraint-dependencies", "override-dependencies", "build-constraint-dependencies"):
        entries = _uv(document).get(key, [])
        if not isinstance(entries, list) or any(not isinstance(raw, str) for raw in entries):
            raise ValueError(f"tool.uv.{key} must be requirements")
        if any(canonicalize_name(Requirement(raw).name) == name for raw in entries):
            raise ValueError(f"{name}: competing tool.uv.{key} is unsupported")
    return tuple(selected)


def _locked(root: Path, name: str, version: str) -> dict[str, Any]:
    document = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = document.get("package")
    if not isinstance(packages, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in packages):
        raise ValueError("uv.lock must contain named package tables")
    selected = [item for item in packages if canonicalize_name(item["name"]) == name]
    if len(selected) != 1 or selected[0].get("version") != version:
        raise ValueError(f"{name}: uv.lock has missing, ambiguous or stale identity")
    return selected[0]


def _artifacts(locked: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    wheels, sdist = locked.get("wheels"), locked.get("sdist")
    if not isinstance(wheels, list) or not wheels or not isinstance(sdist, dict):
        raise ValueError("published dependency proof requires locked wheel and sdist artifacts")
    artifacts = (sdist, *wheels)
    seen = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("locked artifact must be a table")
        url, digest, size = artifact.get("url"), artifact.get("hash"), artifact.get("size")
        if (
            not isinstance(url, str)
            or urlsplit(url).scheme != "https"
            or urlsplit(url).hostname != "files.pythonhosted.org"
            or urlsplit(url).username is not None
            or urlsplit(url).password is not None
            or urlsplit(url).query
            or urlsplit(url).fragment
            or url in seen
            or not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or type(size) is not int
            or size <= 0
        ):
            raise ValueError("locked artifact requires unique PyPI URL, SHA-256 and positive byte size")
        seen.add(url)
    return artifacts


def _installations(root: Path, name: str) -> tuple[dict[str, Any], ...]:
    results = [inspect_installation(name)]
    if name == PACKAGE:
        location = results[0].get("location")
        if not isinstance(location, str) or not Path(__file__).samefile(Path(location) / "research_repo_tools" / "dependency_provenance.py"):
            raise ValueError(f"{name}: executing checker does not belong to the observed installed distribution; remove source import overrides")
    environment = root / os.environ.get("UV_PROJECT_ENVIRONMENT", ".venv")
    if environment.exists():
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if not python.is_file():
            raise ValueError("normal .venv interpreter is missing; sync the declared lock")
        if not environment.samefile(sys.prefix):
            # Static metadata paths only: -I still executes .pth/sitecustomize,
            # and even -S cannot make a checkout-owned executable trustworthy.
            configuration = (environment / "pyvenv.cfg").read_text(encoding="utf-8")
            if re.search(r"(?im)^include-system-site-packages\s*=\s*true\s*$", configuration):
                raise ValueError("normal environment with system site packages is unsupported")
            candidates = [environment / "Lib/site-packages"] if sys.platform == "win32" else list((environment / "lib").glob("python*/site-packages"))
            sites = [site for site in candidates if site.is_dir()]
            if len(sites) != 1:
                raise ValueError("normal environment requires exactly one native site-packages directory")
            results.append(inspect_installation(name, paths=tuple(sites)))
    return tuple(results)


def _check_installation(observed: dict[str, Any], name: str, version: str) -> None:
    if observed.get("name") != name or observed.get("version") != version:
        raise ValueError(f"{name}: installed or isolated runtime version differs from its declared and locked pin")


def _check_lock(root: Path) -> None:
    from research_repo_tools.process import ExecutableNotFoundError, resolve_executable, run_command

    if os.environ.get("UV_CONFIG_FILE") or os.environ.get("UV_NO_CONFIG", "").lower() not in {"", "0", "false"}:
        raise ValueError("published dependency proof requires the observed project's uv configuration")
    environment = {key: value for key, value in os.environ.items() if key.upper() not in {"PATH", "UV_FROZEN"}}
    environment["PATH"] = os.pathsep.join(entry for entry in os.get_exec_path() if Path(entry).is_absolute() and not _in_checkout(Path(entry), root.resolve()))
    try:
        uv = resolve_executable("uv", cwd=root, env=environment)
    except ExecutableNotFoundError as error:
        raise ValueError("release dependency verification requires uv outside the observed checkout") from error
    if _in_checkout(uv, root.resolve()):
        raise ValueError("release dependency verification requires uv outside the observed checkout")
    with tempfile.TemporaryDirectory(prefix="release-dependency-lock-") as directory:
        # uv inspects an existing project interpreter even with --python. Never
        # execute that consumer-owned binary or its startup hooks during proof.
        environment["UV_PROJECT_ENVIRONMENT"] = str(Path(directory) / "uncreated")
        run_command(
            uv,
            [
                "lock",
                "--check",
                "--offline",
                "--no-cache",
                "--no-python-downloads",
                "--no-build",
                "--python",
                sys.executable,
                "--directory",
                str(root.resolve()),
                "--project",
                str(root.resolve()),
            ],
            cwd=root,
            env=environment,
            timeout=60,
        )


def check_development_dependency(root: Path, name: str = PACKAGE, *, document: dict[str, Any] | None = None) -> DependencyIdentity | None:
    """Verify an opted-in exact Git source against lock and executing/normal installs.

    Registry-only development retains its existing baseline/tool checks. Strong
    published-byte proof is deliberately reserved for the release boundary.
    """
    document = document if document is not None else tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    name = canonicalize_name(name, validate=True)
    source = _source(document, name)
    if source is None:
        return None
    url, revision = _git(source)
    requirements = _requirements(document, name)
    version = next(iter(requirements[0].specifier)).version
    locked = _locked(root, name, version)
    lock_source = _table(locked.get("source"), "locked source")
    git = lock_source.get("git")
    if set(lock_source) != {"git"} or not isinstance(git, str):
        raise ValueError(f"{name}: uv.lock source differs from the development Git pin")
    parsed = urlsplit(git)
    if (
        parsed._replace(query="", fragment="").geturl() != url
        or parse_qs(parsed.query, keep_blank_values=True) != {"rev": [revision]}
        or parsed.fragment != revision
    ):
        raise ValueError(f"{name}: locked Git URL, requested revision and commit must match the exact development pin")
    for observed in _installations(root, name):
        _check_installation(observed, name, version)
        direct = observed.get("direct_url")
        if not isinstance(direct, str):
            raise ValueError(f"{name}: Git installation requires direct_url.json provenance")
        origin = _table(_load_json(direct.encode("utf-8"), "direct_url.json"), "direct_url.json")
        vcs = origin.get("vcs_info")
        if (
            set(origin) != {"url", "vcs_info"}
            or origin.get("url") != url
            or not isinstance(vcs, dict)
            or vcs.get("vcs") != "git"
            or vcs.get("commit_id") != revision
            or vcs.get("requested_revision") != revision
            or set(vcs) != {"vcs", "commit_id", "requested_revision"}
        ):
            raise ValueError(f"{name}: installed or isolated Git provenance differs from the exact declaration and lock")
    return DependencyIdentity(name, version, url, revision)


def _published_wheel(name: str, version: str, locked: dict[str, Any], installed: tuple[dict[str, Any], ...]) -> None:
    from research_repo_tools import registry

    data = registry._json(f"https://pypi.org/pypi/{name}/{version}/json")
    data = _table(data, "PyPI exact-version response")
    info = _table(data.get("info"), "PyPI identity")
    if not isinstance(info.get("name"), str) or canonicalize_name(info["name"]) != name or info.get("version") != version:
        raise ValueError(f"{name}: published identity differs")
    uploads = data.get("urls")
    artifacts = _artifacts(locked)
    if not isinstance(uploads, list) or not uploads or any(not isinstance(item, dict) for item in uploads):
        raise ValueError(f"{name}: missing or malformed registry/locked artifacts")
    by_url = {}
    for item in uploads:
        url = item.get("url")
        if not isinstance(url, str) or url in by_url:
            raise ValueError(f"{name}: ambiguous published artifacts")
        by_url[url] = item
    for artifact in artifacts:
        upload = by_url.get(artifact.get("url"))
        digest = artifact.get("hash")
        if (
            upload is None
            or upload.get("yanked") is not False
            or not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or _table(upload.get("digests"), "PyPI artifact hashes").get("sha256") != digest[7:]
            or type(upload.get("size")) is not int
            or artifact["size"] != upload.get("size")
        ):
            raise ValueError(f"{name}: locked artifact is missing, yanked or differs from PyPI")
    payloads: dict[str, bytes] = {}
    for observed in installed:
        wheel = observed.get("wheel")
        if not isinstance(wheel, str):
            raise ValueError(f"{name}: installed WHEEL provenance is missing")
        tags = {tag for raw in Parser().parsestr(wheel).get_all("Tag", []) for tag in parse_tag(raw)}
        candidates = []
        for artifact in locked.get("wheels", []):
            upload = by_url[artifact["url"]]
            filename = upload.get("filename")
            if not isinstance(filename, str):
                raise ValueError("published wheel filename is missing")
            parsed_name, parsed_version, _, parsed_tags = parse_wheel_filename(filename)
            if parsed_name == name and parsed_version == Version(version) and parsed_tags == tags:
                candidates.append(artifact)
        if len(candidates) != 1:
            raise ValueError(f"{name}: installed wheel needs exactly one matching locked published artifact")
        artifact = candidates[0]
        if artifact["size"] > registry.MAX_BYTES:
            raise ValueError("published dependency wheel exceeds the byte limit")
        url = registry.pypi_destination(artifact["url"])
        if url not in payloads:
            with registry.closing_http_errors(), registry._OPENER.open(url, timeout=30) as response:
                payload = response.read(registry.MAX_BYTES + 1)
                if response.status != 200 or response.url != url:
                    raise ValueError("published dependency wheel returned an unexpected status or redirect")
            payloads[url] = payload
        payload = payloads[url]
        if len(payload) != artifact["size"] or hashlib.sha256(payload).hexdigest() != artifact["hash"][7:]:
            raise ValueError("published dependency wheel bytes differ from the lock")
        files = _table(observed.get("files"), "installed file hashes")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > 10000 or sum(member.file_size for member in members) > registry.MAX_BYTES:
                raise ValueError("published dependency wheel exceeds content limits")
            names = [member.filename for member in members]
            if len(set(names)) != len(names):
                raise ValueError("published dependency wheel has duplicate members")
            metadata = [item for item in names if item.endswith(".dist-info/METADATA")]
            if len(metadata) != 1 or metadata[0].replace("METADATA", "WHEEL") not in names or metadata[0].replace("METADATA", "RECORD") not in names:
                raise ValueError("published dependency wheel metadata is missing or ambiguous")
            installer_files = {str(PurePosixPath(metadata[0]).parent / name) for name in ("INSTALLER", "REQUESTED", "uv_cache.json")}
            if set(files) - set(names) - installer_files:
                raise ValueError(f"{name}: unpublished installed files differ from the published wheel")
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename or any(part.endswith(".data") for part in path.parts):
                    raise ValueError("published dependency proof requires a wheel without relocated .data members or unsafe paths")
                if member.is_dir() or member.filename.endswith((".dist-info/RECORD", ".dist-info/RECORD.jws", ".dist-info/RECORD.p7s")):
                    continue
                if files.get(member.filename) != hashlib.sha256(archive.read(member)).hexdigest():
                    raise ValueError(f"{name}: installed or isolated runtime bytes differ from the published wheel: {member.filename}")


def check_release_dependencies(root: Path, *, packages: tuple[str, ...] | None = None) -> tuple[DependencyIdentity, ...]:
    """Enforce configured upstream dependencies before release effects.

    With no explicit packages, read release.published-dependencies from the
    observed checkout. Empty policy is a no-op, including this package's own
    source release. A selected dependency may never name the project itself.
    """
    try:
        manifest = root / "pyproject.toml"
        if not manifest.is_file() and not packages:
            return ()
        original = manifest.read_bytes()
        document = tomllib.loads(original.decode("utf-8"))
        release = _table(_table(_table(document.get("tool", {}), "tool").get(PACKAGE, {}), PACKAGE).get("release", {}), "release")
        names = set()
        for values in (release.get("published-dependencies", ()), packages if packages is not None else ()):
            names.update(dependency_names(values))
        if not names:
            return ()
        if (root / "uv.toml").exists():
            raise ValueError("published dependency proof requires uv configuration in pyproject.toml, not uv.toml")
        project = _table(document.get("project", {}), "project")
        project_name = project.get("name", "")
        if not isinstance(project_name, str):
            raise ValueError("project.name must be a package name")
        original_lock = (root / "uv.lock").read_bytes()
        pending = []
        for name in sorted(names):
            if canonicalize_name(project_name) == name:
                raise ValueError(f"{name}: published-dependencies selects the package being released; select upstream tooling only")
            requirements = _requirements(document, name)
            version = next(iter(requirements[0].specifier)).version
            if _source(document, name) is not None:
                raise ValueError(f"{name}: release requires a published registry dependency without tool.uv.sources overrides")
            locked = _locked(root, name, version)
            if locked.get("source") != {"registry": INDEX}:
                raise ValueError(f"{name}: release requires uv.lock provenance from {INDEX}")
            _artifacts(locked)
            installed = _installations(root, name)
            for observed in installed:
                _check_installation(observed, name, version)
                if observed.get("direct_url") is not None:
                    raise ValueError(f"{name}: installed or isolated runtime has direct_url.json; release requires a registry installation")
            pending.append((name, version, locked, installed))
        # Offline freshness validates the effective entire uv project, including
        # group edges and index configuration. It cannot resolve or rewrite.
        _check_lock(root)
        for name, version, locked, installed in pending:
            _published_wheel(name, version, locked, installed)
        if manifest.read_bytes() != original or (root / "uv.lock").read_bytes() != original_lock:
            raise ValueError("dependency declarations or lock changed during verification")
        for name, _, _, installed in pending:
            if _installations(root, name) != installed:
                raise ValueError(f"{name}: runtime changed during verification")
        return tuple(DependencyIdentity(name, version, INDEX) for name, version, _, _ in pending)
    except (ValueError, OSError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        raise ValueError(f"release dependency provenance: {error}. {RECOVERY}") from error
