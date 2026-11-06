"""Verified dependency selectors shared by Python and Cargo workflows.

Selectors carry syntax; only ``verify_source`` creates availability evidence.
The provider boundary transports bytes, never executable consumer metadata.
"""

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Literal, Protocol, TypeIs
from urllib.parse import quote, urlsplit

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from research_repo_tools.evidence import _load_json
from research_repo_tools.tool_pins import SEMVER

__all__ = ["Artifact", "GitCommit", "RegistryRelease", "SourceProvider", "VerifiedSource", "parse_source", "verify_source"]

type Ecosystem = Literal["python", "cargo"]
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")


def package_name(ecosystem: Ecosystem, name: str) -> str:
    if ecosystem == "python":
        return canonicalize_name(name, validate=True)
    if ecosystem != "cargo" or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) is None:
        raise ValueError("expected a python package or cargo crate name")
    return name


def exact_version(ecosystem: Ecosystem, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("dependency version must be an exact version string")
    if ecosystem == "python":
        try:
            version = Version(value)
        except InvalidVersion as error:
            raise ValueError("Python dependency requires an exact PEP 440 version") from error
        if str(version) != value or version.local is not None:
            raise ValueError("Python dependency requires a canonical public PEP 440 version")
    elif ecosystem != "cargo" or SEMVER.fullmatch(value) is None:
        raise ValueError("Cargo dependency requires an exact SemVer version")
    return value


def relative_directory(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise ValueError("dependency directory must be a portable relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("dependency directory must be a normalized relative path without traversal")
    return value


@dataclass(frozen=True, slots=True)
class RegistryRelease:
    ecosystem: Ecosystem
    package: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "package", package_name(self.ecosystem, self.package))
        exact_version(self.ecosystem, self.version)


@dataclass(frozen=True, slots=True)
class GitCommit:
    ecosystem: Ecosystem
    package: str
    repository: str
    commit: str
    directory: str = "."

    def __post_init__(self) -> None:
        object.__setattr__(self, "package", package_name(self.ecosystem, self.package))
        if _REPOSITORY.fullmatch(self.repository) is None or self.repository.endswith(".git"):
            raise ValueError("repository must be a GitHub owner/name without URL or .git suffix")
        if _COMMIT.fullmatch(self.commit) is None:
            raise ValueError("development source requires a full lowercase 40-character commit SHA")
        relative_directory(self.directory)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repository}.git"


type Source = RegistryRelease | GitCommit


def _is_table(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def table(value: object, context: str) -> dict[str, object]:
    if not _is_table(value):
        raise ValueError(f"{context} must be a table")
    return value


def string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a nonempty string")
    return value


def parse_source(value: object) -> Source:
    """Parse one exact source; unknown or competing selectors are errors."""
    data = table(value, "dependency source")
    ecosystem = data.get("ecosystem")
    if ecosystem not in ("python", "cargo"):
        raise ValueError("dependency ecosystem must be python or cargo")
    kind: Ecosystem = "python" if ecosystem == "python" else "cargo"
    name = string(data.get("package"), "dependency package")
    if set(data) == {"ecosystem", "package", "version"}:
        return RegistryRelease(kind, name, string(data["version"], "version"))
    if set(data) in ({"ecosystem", "package", "github", "commit"}, {"ecosystem", "package", "github", "commit", "directory"}):
        return GitCommit(kind, name, string(data["github"], "github"), string(data["commit"], "commit"), relative_directory(data.get("directory", ".")))
    raise ValueError("select only an exact version or github plus commit (and optional directory)")


class SourceProvider(Protocol):
    """Read bounded exact-URL bytes; raise on absence, redirect or lookup failure."""

    def read(self, url: str) -> bytes: ...


class HttpSourceProvider:
    def read(self, url: str) -> bytes:
        from research_repo_tools import registry
        from research_repo_tools.tooling_runtime import _read

        if urlsplit(url).netloc in {"files.pythonhosted.org", "static.crates.io"}:
            with registry.closing_http_errors(), registry._OPENER.open(url, timeout=30) as response:
                payload: object = response.read(registry.MAX_BYTES + 1)
                if not isinstance(payload, bytes) or response.status != 200 or response.url != url or len(payload) > registry.MAX_BYTES:
                    raise ValueError("dependency artifact returned an unexpected status, redirect or oversized payload")
            return payload
        return _read(url)


@dataclass(frozen=True, slots=True)
class Artifact:
    filename: str
    url: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        if not self.filename or PurePosixPath(self.filename).name != self.filename or "\\" in self.filename:
            raise ValueError("artifact requires a plain filename")
        if _SHA256.fullmatch(self.sha256) is None or type(self.size) is not int or self.size <= 0:
            raise ValueError("artifact requires a SHA-256 and positive byte size")
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or parsed.netloc not in {"files.pythonhosted.org", "static.crates.io"} or parsed.query or parsed.fragment:
            raise ValueError("artifact requires an uncredentialed supported registry URL")


@dataclass(frozen=True, slots=True, init=False)
class VerifiedSource:
    """In-process availability evidence, never accepted by deserialization.

    Reverify immediately before a dependent operation. Metadata fingerprints
    identify observations; they do not turn saved JSON into trusted evidence.
    """

    source: Source
    version: str
    artifacts: tuple[Artifact, ...]
    metadata_sha256: str

    def __init__(self) -> None:
        raise TypeError("use verify_source to obtain a VerifiedSource")


def _verified(source: Source, version: str, artifacts: tuple[Artifact, ...]) -> VerifiedSource:
    artifacts = tuple(sorted(artifacts, key=lambda item: item.filename))
    identity = json.dumps(
        {"source": asdict(source), "version": version, "artifacts": [asdict(item) for item in artifacts]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result = object.__new__(VerifiedSource)
    object.__setattr__(result, "source", source)
    object.__setattr__(result, "version", version)
    object.__setattr__(result, "artifacts", artifacts)
    object.__setattr__(result, "metadata_sha256", hashlib.sha256(identity).hexdigest())
    return result


def verify_source(source: Source, *, provider: SourceProvider | None = None, release: bool = False) -> VerifiedSource:
    """Verify an exact public registry release or GitHub commit without processes.

    Python supports static project metadata and public PEP 440 versions. Cargo
    supports SemVer and explicit workspace version inheritance. Registry proof
    binds eligible artifact URLs, sizes and hashes; consumers check native locks.
    """
    if not isinstance(source, (RegistryRelease, GitCommit)):
        raise TypeError("source must be a parsed RegistryRelease or GitCommit")
    if release and isinstance(source, GitCommit):
        raise ValueError("release requires a published registry source; commits are development-only")
    provider = provider or HttpSourceProvider()

    def read(url: str) -> bytes:
        payload: object = provider.read(url)
        if not isinstance(payload, bytes) or len(payload) > 1024 * 1024:
            raise ValueError("source metadata must be bounded bytes")
        return payload

    def json(url: str) -> dict[str, object]:
        return table(_load_json(read(url), "dependency source metadata"), "source metadata")

    if isinstance(source, GitCommit):
        repository = json(f"https://api.github.com/repos/{source.repository}")
        if repository.get("full_name") != source.repository:
            raise ValueError("repository identity differs from the requested GitHub repository")
        commit = json(f"https://api.github.com/repos/{source.repository}/git/commits/{source.commit}")
        if commit.get("sha") != source.commit:
            raise ValueError("GitHub returned a different commit")
        filename = "pyproject.toml" if source.ecosystem == "python" else "Cargo.toml"
        prefix = f"https://raw.githubusercontent.com/{source.repository}/{source.commit}/"
        path = (PurePosixPath(source.directory) / filename).as_posix()
        manifest_bytes = read(prefix + quote(path))
        manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
        project = table(manifest.get("project" if source.ecosystem == "python" else "package"), "upstream package")
        if package_name(source.ecosystem, string(project.get("name"), "upstream package name")) != source.package:
            raise ValueError("commit manifest identifies a different package")
        if source.ecosystem == "cargo":
            tree_sha = string(table(commit.get("tree"), "commit tree").get("sha"), "tree SHA")
            if _COMMIT.fullmatch(tree_sha) is None:
                raise ValueError("commit tree must have an immutable SHA")
            tree = json(f"https://api.github.com/repos/{source.repository}/git/trees/{tree_sha}?recursive=1")
            entries = tree.get("tree")
            if tree.get("sha") != tree_sha or tree.get("truncated") is not False or not isinstance(entries, list):
                raise ValueError("Cargo commit requires a complete, matching repository tree")
            manifests = []
            seen = set()
            matches = []
            for raw in entries:
                entry = table(raw, "Git tree entry")
                member = string(entry.get("path"), "Git tree path")
                relative_directory(member)
                if member in seen:
                    raise ValueError("Git tree contains duplicate paths")
                seen.add(member)
                if PurePosixPath(member).name == "Cargo.toml":
                    if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
                        raise ValueError("Cargo manifest must be a regular Git blob")
                    manifests.append((member, entry.get("sha")))
            if not manifests or len(manifests) > 100:
                raise ValueError("Cargo source proof supports 1..100 repository manifests")
            for member, blob in manifests:
                payload = manifest_bytes if member == path else read(prefix + quote(member))
                if hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest() != blob:
                    raise ValueError("Cargo manifest bytes differ from the verified commit tree")
                package = table(tomllib.loads(payload.decode("utf-8")).get("package", {}), "Cargo package")
                if package.get("name") == source.package:
                    matches.append(member)
            if matches != [path]:
                raise ValueError("Cargo repository must contain exactly one requested package at the selected directory")
        version = project.get("version")
        if source.ecosystem == "cargo" and version == {"workspace": True}:
            # Find the nearest ancestor workspace; never infer inheritance from
            # a repository root unrelated to this package.
            for parent in PurePosixPath(source.directory).parents:
                ancestor = tomllib.loads(read(prefix + quote((parent / "Cargo.toml").as_posix())).decode("utf-8"))
                if "workspace" in ancestor:
                    version = table(table(ancestor["workspace"], "workspace").get("package"), "workspace.package").get("version")
                    break
        dynamic = project.get("dynamic", [])
        if not isinstance(dynamic, list):
            raise ValueError("dynamic metadata must be an array")
        if source.ecosystem == "python" and "version" in dynamic:
            raise ValueError("dynamic upstream version metadata cannot provide inert source proof")
        return _verified(source, exact_version(source.ecosystem, version), ())
    artifacts: list[Artifact] = []
    if source.ecosystem == "python":
        data = json(f"https://pypi.org/pypi/{source.package}/{source.version}/json")
        info = table(data.get("info"), "PyPI package")
        if canonicalize_name(string(info.get("name"), "PyPI name")) != source.package or info.get("version") != source.version:
            raise ValueError("published Python package identity differs")
        uploads = data.get("urls")
        if not isinstance(uploads, list) or not uploads:
            raise ValueError("published Python release has no artifacts")
        for raw in uploads:
            upload = table(raw, "PyPI artifact")
            filename = string(upload.get("filename"), "artifact filename")
            kind = upload.get("packagetype")
            if upload.get("yanked") is not False or kind not in {"bdist_wheel", "sdist"}:
                raise ValueError("published Python artifact is yanked or unsupported")
            parsed = parse_wheel_filename(filename) if kind == "bdist_wheel" else parse_sdist_filename(filename)
            if parsed[0] != source.package or parsed[1] != Version(source.version):
                raise ValueError("artifact filename identifies a different package/version")
            size = upload.get("size")
            if type(size) is not int:
                raise ValueError("artifact size must be an integer")
            artifacts.append(
                Artifact(filename, string(upload.get("url"), "artifact URL"), string(table(upload.get("digests"), "digests").get("sha256"), "digest"), size)
            )
            if urlsplit(artifacts[-1].url).netloc != "files.pythonhosted.org":
                raise ValueError("Python artifact must come from PyPI")
    else:
        data = json(f"https://crates.io/api/v1/crates/{source.package}/{source.version}")
        info = table(data.get("version"), "crates.io version")
        if info.get("crate") != source.package or info.get("num") != source.version or info.get("yanked") is not False:
            raise ValueError("published Cargo package identity differs or is yanked")
        size = info.get("crate_size")
        if type(size) is not int:
            raise ValueError("published Cargo archive requires its byte size")
        filename = f"{source.package}-{source.version}.crate"
        artifacts.append(
            Artifact(filename, f"https://static.crates.io/crates/{source.package}/{filename}", string(info.get("checksum"), "crate checksum"), size)
        )
    if len({item.url for item in artifacts}) != len(artifacts) or len({item.filename for item in artifacts}) != len(artifacts):
        raise ValueError("published artifacts contain duplicate identities")
    return _verified(source, source.version, tuple(artifacts))
