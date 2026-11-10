"""Declarative dependency projects and immutable input observations.

The canonical dependency-sources.toml travels with source, lock and evidence.
It is read by every release boundary, including when no pyproject.toml exists.
"""

import hashlib
import os
import stat
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from research_repo_tools.dependency_build_policy import SourceBuildPolicy, parse_build_policy
from research_repo_tools.dependency_sources import Ecosystem, Source, parse_source, relative_directory, string, table

POLICY_FILE = "dependency-sources.toml"
MAX_SNAPSHOT_FILES = 10000
MAX_SNAPSHOT_FILE_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DependencyProject:
    ecosystem: Ecosystem
    directory: str = "."
    owned: tuple[str, ...] = (".",)
    cargo_owner: DependencyProject | None = None

    def __post_init__(self) -> None:
        if self.ecosystem not in {"python", "cargo"}:
            raise ValueError("dependency project ecosystem must be python or cargo")
        relative_directory(self.directory)
        object.__setattr__(self, "owned", tuple(self.owned))
        if not self.owned or len(set(self.owned)) != len(self.owned):
            raise ValueError("release-owned must contain distinct workspace directories")
        for value in self.owned:
            relative_directory(value)
        owner = self.cargo_owner
        if owner is not None and (
            self.ecosystem != "cargo"
            or owner.ecosystem != "cargo"
            or self.owned != (".",)
            or owner.owned != (".",)
            or owner.cargo_owner is not None
            or self.directory == owner.directory
            or not PurePosixPath(self.directory).is_relative_to(PurePosixPath(owner.directory))
        ):
            raise ValueError("cargo-owner must identify a separate enclosing Cargo project owning its root package")


@dataclass(frozen=True, slots=True)
class SourceSelection:
    name: str
    project: DependencyProject
    source: Source


@dataclass(frozen=True, slots=True)
class DependencyPolicy:
    projects: tuple[DependencyProject, ...]
    selections: tuple[SourceSelection, ...]
    source_builds: SourceBuildPolicy | None = None


def parse_policy(value: object) -> DependencyPolicy:
    data = table(value, "dependency policy")
    if set(data) - {"schema", "projects", "sources", "source-builds"} or type(data.get("schema")) is not int or data["schema"] != 1:
        raise ValueError("dependency policy requires schema = 1 and only projects/sources/source-builds")
    projects: dict[str, DependencyProject] = {}
    owners: dict[str, str] = {}
    for name, raw in table(data.get("projects"), "projects").items():
        project = table(raw, "dependency project")
        if set(project) - {"cargo-owner", "ecosystem", "directory", "release-owned"}:
            raise ValueError("unknown dependency project field")
        ecosystem = project.get("ecosystem")
        if ecosystem not in ("python", "cargo"):
            raise ValueError("dependency project ecosystem must be python or cargo")
        owned = project.get("release-owned", ["."])
        if not isinstance(owned, list) or any(not isinstance(path, str) for path in owned):
            raise ValueError("release-owned must be a list of workspace directories")
        projects[name] = DependencyProject("python" if ecosystem == "python" else "cargo", relative_directory(project.get("directory", ".")), tuple(owned))
        if "cargo-owner" in project:
            owners[name] = string(project["cargo-owner"], "cargo-owner")
    for name, owner in owners.items():
        if owner not in projects or owner in owners:
            raise ValueError("cargo-owner must name a declared Cargo project without its own cargo-owner")
        projects[name] = replace(projects[name], cargo_owner=projects[owner])
    if not projects or len(set(projects.values())) != len(projects):
        raise ValueError("dependency policy requires distinct projects")
    if len({(p.ecosystem, p.directory) for p in projects.values()}) != len(projects):
        raise ValueError("a project cannot have competing release-owned boundaries")
    selections = []
    for name, raw in table(data.get("sources", {}), "sources").items():
        selection = dict(table(raw, "source selection"))
        project_name = string(selection.pop("project", None), "source project")
        if project_name not in projects:
            raise ValueError("source must name a configured project")
        project = projects[project_name]
        source = parse_source({"ecosystem": project.ecosystem, **selection})
        if source.ecosystem != project.ecosystem:
            raise ValueError("source ecosystem must match its project")
        selections.append(SourceSelection(name, project, source))
    if len({(s.project, s.source.package) for s in selections}) != len(selections):
        raise ValueError("a package cannot have competing source selections")
    builds = parse_build_policy(data.get("source-builds"))
    if builds is not None and sum(project.ecosystem == "python" for project in projects.values()) != 1:
        raise ValueError("source builds require exactly one declared Python project")
    return DependencyPolicy(tuple(projects.values()), tuple(selections), builds)


def load_policy(root: Path) -> DependencyPolicy | None:
    path = root / POLICY_FILE
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("dependency policy must be a regular source file")
    return parse_policy(tomllib.loads(path.read_text(encoding="utf-8")))


def inside(root: Path, relative: str) -> Path:
    path = root / relative_directory(relative)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("dependency paths must stay within the observed checkout")
    return path


def project_manifests(root: Path, ecosystem: Ecosystem) -> tuple[Path, ...]:
    """Enumerate declared workspace membership without executing consumer code."""
    name = "pyproject.toml" if ecosystem == "python" else "Cargo.toml"
    manifest = root / name
    if manifest.is_symlink():
        raise ValueError("workspace manifest must be a regular file, not a symlink")
    document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    workspace = (
        table(table(table(document.get("tool", {}), "tool").get("uv", {}), "uv").get("workspace", {}), "workspace")
        if ecosystem == "python"
        else table(document.get("workspace", {}), "workspace")
    )
    members, excludes = workspace.get("members", []), workspace.get("exclude", [])
    if not isinstance(members, list) or not isinstance(excludes, list) or any(not isinstance(item, str) for item in (*members, *excludes)):
        raise ValueError("workspace members/exclude must be path patterns")
    paths = {manifest.resolve()}
    omitted: set[Path] = set()
    for patterns, destination in ((excludes, omitted), (members, paths)):
        for pattern in patterns:
            relative_directory(pattern)
            for match in root.glob(pattern):
                candidate = match / name
                if not candidate.resolve().is_relative_to(root.resolve()):
                    raise ValueError("workspace members must stay inside the project")
                if candidate.is_file():
                    destination.add(candidate.resolve())
    return tuple(sorted(paths - omitted, key=str))


def configuration_paths(root: Path, ecosystem: Ecosystem) -> tuple[Path, ...]:
    if ecosystem == "python":
        return tuple(parent / "uv.toml" for parent in (root, *root.parents))
    paths = [parent / ".cargo" / name for parent in (root, *root.parents) for name in ("config", "config.toml")]
    home = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")))
    paths.extend(home / name for name in ("config", "config.toml"))
    return tuple(dict.fromkeys(paths))


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    """Exact bytes and absence assertions, rechecked around all effects."""

    files: tuple[tuple[Path, bytes | None], ...]
    environment: tuple[tuple[str, str], ...]

    @classmethod
    def capture(cls, paths: tuple[Path, ...]) -> InputSnapshot:
        files = []
        seen = set()
        remaining = MAX_SNAPSHOT_BYTES
        for path in paths:
            if path in seen:
                continue
            if len(seen) >= MAX_SNAPSHOT_FILES:
                raise ValueError("dependency snapshot exceeds the file-count limit")
            seen.add(path)
            try:
                metadata = path.lstat()
            except FileNotFoundError, NotADirectoryError:
                files.append((path, None))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"dependency input must be a regular file, not a symlink: {path}")
            limit = min(MAX_SNAPSHOT_FILE_BYTES, remaining)
            if metadata.st_size > limit:
                raise ValueError(f"dependency snapshot exceeds the per-file or aggregate byte limit: {path}")
            with path.open("rb") as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                raise ValueError(f"dependency snapshot exceeds the per-file or aggregate byte limit: {path}")
            remaining -= len(content)
            files.append((path, content))
        return cls(tuple(files), _environment())

    @property
    def digest(self) -> str:
        digest = hashlib.sha256()
        for path, payload in self.files:
            for value in (str(path).encode("utf-8"), b"absent" if payload is None else b"present" + payload):
                digest.update(len(value).to_bytes(8))
                digest.update(value)
        digest.update(repr(self.environment).encode("utf-8"))
        return digest.hexdigest()

    def verify(self) -> None:
        if InputSnapshot.capture(tuple(path for path, _ in self.files)) != self:
            raise ValueError("dependency inputs changed; revalidate source, locks and downstream evidence")


def _environment() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted((key, value) for key, value in os.environ.items() if key.upper().startswith(("UV_", "CARGO_", "RUST")) or key.upper() in {"PATH", "VIRTUAL_ENV"})
    )
