"""Inert uv/Cargo source inspection and native locked graph verification."""

import os
import re
import tempfile
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from research_repo_tools._dependency_observation import retain_guard
from research_repo_tools._git_checkout import _in_checkout
from research_repo_tools.dependency_policy import POLICY_FILE, DependencyProject, InputSnapshot, configuration_paths, inside, project_manifests
from research_repo_tools.dependency_progress import report
from research_repo_tools.dependency_sources import (
    GitCommit,
    RegistryRelease,
    Source,
    SourceProvider,
    VerifiedSource,
    package_name,
    string,
    table,
    verify_source,
)
from research_repo_tools.evidence import _load_json

PYPI = "https://pypi.org/simple"
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"


@dataclass(frozen=True, slots=True)
class LockedDependency:
    source: Source
    version: str
    artifacts: tuple[tuple[str, str, int], ...] = ()
    checksum: str | None = None


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    project: DependencyProject
    root: Path
    snapshot: InputSnapshot
    manifests: tuple[Path, ...]
    dependencies: tuple[LockedDependency, ...]
    owned: tuple[tuple[str, str, Path], ...]
    checkout: Path


def _records(value: object, context: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array of tables")
    return tuple(table(item, context) for item in value)


def _requirements(document: dict[str, object]) -> Iterator[Requirement]:
    project = table(document.get("project", {}), "project")
    uv = table(table(document.get("tool", {}), "tool").get("uv", {}), "uv")
    groups = table(document.get("dependency-groups", {}), "dependency-groups")
    optional = table(project.get("optional-dependencies", {}), "optional-dependencies")
    arrays = [
        project.get("dependencies", []),
        *groups.values(),
        *optional.values(),
        table(document.get("build-system", {}), "build-system").get("requires", []),
    ]
    arrays.extend(uv.get(key, []) for key in ("dev-dependencies", "constraint-dependencies", "override-dependencies", "build-constraint-dependencies"))
    for entries in arrays:
        if not isinstance(entries, list):
            raise ValueError("Python dependency declarations must be arrays")
        for entry in entries:
            if isinstance(entry, dict) and set(entry) == {"include-group"} and isinstance(entry["include-group"], str) and entry["include-group"] in groups:
                continue
            yield Requirement(string(entry, "Python requirement"))


def _cargo_dependencies(document: dict[str, object]) -> Iterator[tuple[str, dict[str, object]]]:
    sections = [document, table(document.get("workspace", {}), "workspace")]
    sections.extend(table(value, "Cargo target") for value in table(document.get("target", {}), "target").values())
    for section in sections:
        for kind in ("dependencies", "build-dependencies", "dev-dependencies"):
            for name, raw in table(section.get(kind, {}), kind).items():
                yield name, {"version": raw} if isinstance(raw, str) else table(raw, "Cargo dependency")


def _check_configuration(root: Path, ecosystem: str) -> None:
    prefixes = ("UV_",) if ecosystem == "python" else ("CARGO_", "RUSTC", "RUSTDOC", "RUSTUP_TOOLCHAIN")
    allowed = {
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON",
        "UV_PYTHON_DOWNLOADS",
        "UV_NO_PROGRESS",
        "UV_RUN_RECURSION_DEPTH",
        "UV_TOOL_BIN_DIR",
        "UV_TOOL_DIR",
        "CARGO_HOME",
        "CARGO_TARGET_DIR",
        "CARGO_TERM_COLOR",
        "CARGO_NET_OFFLINE",
        "CARGO_BUILD_JOBS",
        "RUSTUP_TOOLCHAIN",
    }
    for key, value in os.environ.items():
        if value and key.upper().startswith(prefixes) and key.upper() not in allowed:
            raise ValueError(f"dependency proof does not support environment override {key}")
    for path in configuration_paths(root, "python" if ecosystem == "python" else "cargo"):
        if not path.exists():
            continue
        if ecosystem == "python":
            raise ValueError("dependency proof requires project-local pyproject.toml uv configuration; uv.toml is unsupported")
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(data) & {"source", "registries", "registry", "paths", "patch", "replace", "env", "unstable"}:
            raise ValueError("Cargo source replacement, path/registry overrides and injected environment are unsupported for release proof")
        if set(table(data.get("build", {}), "Cargo build")) & {"rustc", "rustc-wrapper", "rustc-workspace-wrapper", "rustdoc"}:
            raise ValueError("Cargo compiler wrappers are unsupported for inert dependency inspection")


def _git_source(ecosystem: str, name: str, value: str) -> GitCommit:
    parsed = urlsplit(value.removeprefix("git+"))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or set(query) - ({"rev", "subdirectory"} if ecosystem == "python" else {"rev"})
        or query.get("rev") != [parsed.fragment]
        or len(query.get("subdirectory", ["."])) != 1
    ):
        raise ValueError("locked development source requires an exact GitHub rev and identical resolved commit")
    return GitCommit(
        "python" if ecosystem == "python" else "cargo",
        name,
        parsed.path.removeprefix("/").removesuffix(".git"),
        parsed.fragment,
        query.get("subdirectory", ["."])[0],
    )


def inspect_project(checkout: Path, project: DependencyProject, *, release: bool = True) -> DependencyGraph:
    """Reject non-registry declarations/locks before any native process or lookup."""
    checkout = checkout.resolve(strict=True)
    root = inside(checkout, project.directory).resolve(strict=True)
    owner = inspect_project(checkout, project.cargo_owner, release=release) if project.cargo_owner else None
    if owner is not None and (
        root == owner.root or not root.is_relative_to(owner.root) or root != checkout / project.directory or owner.root != checkout / owner.project.directory
    ):
        raise ValueError("Cargo helper and owner require distinct canonical paths inside the observed checkout")
    manifests = project_manifests(root, project.ecosystem)
    lock_path = root / ("uv.lock" if project.ecosystem == "python" else "Cargo.lock")
    owner_inputs = tuple(path for path, _ in owner.snapshot.files) if owner else ()
    snapshot = InputSnapshot.capture((checkout / POLICY_FILE, *manifests, lock_path, *configuration_paths(root, project.ecosystem), *owner_inputs))
    _check_configuration(root, project.ecosystem)
    documents = {path: table(tomllib.loads(path.read_text(encoding="utf-8")), "manifest") for path in manifests}
    owned: list[tuple[str, str, Path]] = []
    root_document = documents[root / ("pyproject.toml" if project.ecosystem == "python" else "Cargo.toml")]
    if owner is not None and (
        len(manifests) != 1 or "workspace" not in root_document or table(root_document.get("package"), "Cargo helper package").get("publish") is not False
    ):
        raise ValueError("cargo-owner is only supported for a standalone unpublished Cargo helper workspace")
    for relative in project.owned:
        path = inside(root, relative).resolve() / ("pyproject.toml" if project.ecosystem == "python" else "Cargo.toml")
        if path not in documents:
            raise ValueError("release-owned packages must be declared members of this workspace")
        data = table(documents[path].get("project" if project.ecosystem == "python" else "package"), "release-owned package")
        version = data.get("version")
        if project.ecosystem == "cargo" and version == {"workspace": True}:
            version = table(table(root_document.get("workspace"), "workspace").get("package"), "workspace.package").get("version")
        name = package_name(project.ecosystem, string(data.get("name"), "owned package name"))
        pin = RegistryRelease(project.ecosystem, name, string(version, "owned package version"))
        if owned:
            private = (
                data.get("publish") is False
                if project.ecosystem == "cargo"
                else table(table(documents[path].get("tool", {}), "tool").get("uv", {}), "uv").get("package") is False
            )
            if not private:
                raise ValueError(
                    "separately published workspace members require registry dependencies and upstream-first publication; only one published package is release-owned"
                )
        owned.append((name, pin.version, path.parent))
    if owner:
        owned.extend(owner.owned)
    if len({name for name, _, _ in owned}) != len(owned):
        raise ValueError("ambiguous release-owned package names")
    owned_paths = {path for _, _, path in owned}
    owned_names = {name for name, _, _ in owned}
    uses_owner = False
    for manifest, document in documents.items():
        if project.ecosystem == "python":
            uv = table(table(document.get("tool", {}), "tool").get("uv", {}), "uv")
            if set(uv) & {"index", "index-url", "extra-index-url", "find-links", "no-index", "dependency-metadata"}:
                raise ValueError("Python release proof supports only PyPI without alternate indexes or metadata overrides")
            for requirement in _requirements(document):
                if requirement.url and release:
                    raise ValueError("Python release dependency declarations cannot use direct URLs, Git or paths")
            for name, raw in table(uv.get("sources", {}), "uv sources").items():
                if release and not (canonicalize_name(name) in owned_names and raw == {"workspace": True}):
                    raise ValueError("Python release source overrides must select release-owned workspace packages only")
        else:
            patches = table(document.get("patch", {}), "Cargo patches")
            if release and (document.get("replace") or any(table(value, "Cargo patch source") for value in patches.values())):
                raise ValueError("Cargo patch/replace overrides require removal and lock reconciliation before release proof")
            for name, dependency in _cargo_dependencies(document):
                if "registry" in dependency or "registry-index" in dependency:
                    raise ValueError("Cargo release proof supports only crates.io")
                if release and set(dependency) & {"git", "branch", "tag", "rev"}:
                    raise ValueError("Cargo Git dependencies are development-only, including git plus version fallbacks")
                if "path" in dependency and (release or owner):
                    relative = Path(string(dependency["path"], "Cargo dependency path"))
                    target = (manifest.parent / relative).resolve()
                    if target not in owned_paths:
                        raise ValueError("Cargo external path dependencies cannot become release-owned through a local path")
                    if owner and target == owner.root:
                        owner_name, _, _ = owner.owned[0]
                        parent_path = ("..",) * len(root.relative_to(owner.root).parts)
                        if relative.parts != parent_path or dependency.get("package", name) != owner_name:
                            raise ValueError("Cargo helper path must directly identify its declared enclosing owner package")
                        uses_owner = True
    if owner and not uses_owner:
        raise ValueError("Cargo helper must depend by path on its declared owner package")
    lock = table(tomllib.loads(lock_path.read_text(encoding="utf-8")), "dependency lock")
    dependencies: list[LockedDependency] = []
    seen: set[tuple[str, str, str]] = set()
    helper_packages = {(name, version) for name, version, _ in owned} if owner else set()
    for item in _records(lock.get("package"), "locked packages"):
        name = package_name(project.ecosystem, string(item.get("name"), "locked name"))
        version = RegistryRelease(project.ecosystem, name, string(item.get("version"), "locked version")).version
        source = item.get("source")
        key = (name, version, repr(source))
        if key in seen:
            raise ValueError("duplicate locked package identity")
        seen.add(key)
        if project.ecosystem == "python":
            origin = table(source, "uv locked source")
            if set(origin) in ({"editable"}, {"virtual"}):
                relative = string(next(iter(origin.values())), "workspace path")
                candidate = inside(root, relative).resolve()
                if (name, version, candidate) not in owned:
                    raise ValueError("uv lock contains an external path or separately published workspace package")
                continue
            if origin == {"registry": PYPI}:
                artifacts = []
                raw_artifacts = list(_records(item.get("wheels", []), "locked wheels"))
                if "sdist" in item:
                    raw_artifacts.append(table(item["sdist"], "locked sdist"))
                for artifact in raw_artifacts:
                    url, digest, size = artifact.get("url"), artifact.get("hash"), artifact.get("size")
                    if (
                        not isinstance(url, str)
                        or not isinstance(digest, str)
                        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
                        or type(size) is not int
                        or size <= 0
                    ):
                        raise ValueError("uv locked artifacts require URL, SHA-256 and positive size")
                    artifacts.append((url, digest[7:], size))
                if not artifacts or len(set(artifacts)) != len(artifacts) or len({a[0] for a in artifacts}) != len(artifacts):
                    raise ValueError("uv locked artifacts are missing or ambiguous")
                dependencies.append(LockedDependency(RegistryRelease("python", name, version), version, tuple(artifacts)))
            elif not release and set(origin) == {"git"}:
                dependencies.append(LockedDependency(_git_source("python", name, string(origin["git"], "git source")), version))
            else:
                raise ValueError("every external Python dependency must resolve to PyPI for release")
        elif source == CRATES_IO:
            checksum = string(item.get("checksum"), "Cargo checksum")
            if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
                raise ValueError("Cargo registry lock requires an archive SHA-256")
            dependencies.append(LockedDependency(RegistryRelease("cargo", name, version), version, checksum=checksum))
        elif source is None:
            if owner:
                if (name, version) not in helper_packages:
                    raise ValueError("Cargo helper lock package differs from the declared helper and owner identities")
                helper_packages.remove((name, version))
            else:
                members = [path for path, data in documents.items() if table(data.get("package", {}), "package").get("name") == name]
                if len(members) != 1:
                    raise ValueError("Cargo source-less lock package must be an unambiguous workspace member")
        elif not release and isinstance(source, str) and source.startswith("git+"):
            dependencies.append(LockedDependency(_git_source("cargo", name, source), version))
        else:
            raise ValueError("every external Cargo dependency must resolve to crates.io for release")
    if project.ecosystem == "python":
        for document in documents.values():
            for requirement in _requirements(document):
                name = canonicalize_name(requirement.name)
                if name in owned_names:
                    continue
                matches = [d for d in dependencies if d.source.package == name and requirement.specifier.contains(d.version, prereleases=True)]
                if not matches:
                    raise ValueError(
                        f"{name}: declaration/build requirement lacks a matching locked identity; include build dependencies in the universal lock"
                    )
    if helper_packages:
        raise ValueError("Cargo helper lock is missing the declared helper or owner package")
    if owner:
        owner.snapshot.verify()
    snapshot.verify()
    return DependencyGraph(project, root, snapshot, manifests, tuple(dependencies), tuple(owned), checkout)


def _resolve_cargo(*, cwd: Path, environment: dict[str, str], checkouts: tuple[Path, ...]) -> Path:
    """Check both a Cargo launcher and the executable a rustup proxy selects."""
    from research_repo_tools.process import ExecutableNotFoundError, resolve_executable, run_command

    def outside(executable: Path) -> Path:
        if any(_in_checkout(executable, root.resolve()) for root in checkouts):
            raise ValueError("Cargo executable must be outside dependency checkouts")
        return executable

    cargo = outside(resolve_executable("cargo", cwd=cwd, env=environment))
    rustup = cargo.with_name("rustup" + cargo.suffix)
    if not rustup.is_file():
        try:
            rustup = resolve_executable("rustup", cwd=cwd, env=environment)
        except ExecutableNotFoundError as error:
            if environment.get("RUSTUP_TOOLCHAIN") or environment.get("RUSTUP_HOME"):
                raise ValueError("cannot verify the selected Rust toolchain without rustup") from error
            return cargo
    # Package managers can wrap Cargo and rustup in distinct launchers. Different
    # files or bytes do not prove that Cargo bypasses rustup's selected toolchain.
    rustup = outside(resolve_executable(rustup, cwd=cwd, env=environment))
    selected = Path(run_command(rustup, ["which", "cargo"], cwd=cwd, env={**environment, "RUSTUP_AUTO_INSTALL": "0"}, timeout=30).stdout.strip())
    if not selected.is_absolute():
        raise ValueError("rustup must identify an absolute Cargo executable")
    outside(resolve_executable(selected, cwd=cwd, env=environment))
    return cargo


def check_native_graph(graph: DependencyGraph, *, sources: tuple[VerifiedSource, ...] = (), provider: SourceProvider | None = None) -> None:
    """Use uv/Cargo freshness semantics; never update a lock, fetch or build."""
    report("started", "native graph", graph=f"{graph.project.ecosystem}:{graph.project.directory}")
    graph.snapshot.verify()
    if graph.project.ecosystem == "python":
        from research_repo_tools.dependency_provenance import _check_lock

        _check_lock(graph.root)
        from research_repo_tools.dependency_artifacts import verify_python_environment

        verify_python_environment(graph)
    else:
        from research_repo_tools.process import run_command

        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CARGO_", "RUST"))}
        environment["PATH"] = os.pathsep.join(p for p in os.get_exec_path() if Path(p).is_absolute() and not _in_checkout(Path(p), graph.checkout))
        for key in ("CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN"):
            if key in os.environ:
                environment[key] = os.environ[key]
        # Run rustup proxies outside the inert checkout, so rust-toolchain files
        # and local .cargo configuration cannot select a consumer executable.
        with tempfile.TemporaryDirectory(prefix="dependency-graph-") as directory:
            cwd = Path(directory)
            cargo = _resolve_cargo(cwd=cwd, environment=environment, checkouts=(graph.checkout,))
            result = run_command(
                cargo,
                ["metadata", "--manifest-path", str(graph.root / "Cargo.toml"), "--format-version", "1", "--all-features", "--locked", "--offline"],
                cwd=cwd,
                env=environment,
                timeout=120,
            )
        metadata = table(_load_json(result.stdout.encode("utf-8"), "Cargo metadata"), "Cargo metadata")
        if Path(string(metadata.get("workspace_root"), "workspace root")).resolve() != graph.root:
            raise ValueError("dependency project must identify the complete Cargo workspace root")
        packages = _records(metadata.get("packages"), "Cargo packages")
        nodes = _records(table(metadata.get("resolve"), "Cargo resolution").get("nodes"), "Cargo nodes")
        by_id = {string(p.get("id"), "Cargo package id"): p for p in packages}
        edges = {string(n.get("id"), "Cargo node id"): n.get("dependencies") for n in nodes}
        if len(by_id) != len(packages) or len(edges) != len(nodes) or set(by_id) != set(edges):
            raise ValueError("Cargo resolved graph has missing or duplicate identities")
        roots = []
        for name, version, path in graph.owned:
            matches = [
                identity
                for identity, p in by_id.items()
                if p.get("name") == name
                and p.get("version") == version
                and p.get("source") is None
                and Path(string(p.get("manifest_path"), "Cargo manifest")).resolve() == path / "Cargo.toml"
            ]
            if len(matches) != 1:
                raise ValueError("Cargo graph differs from release-owned package identity")
            roots.extend(matches)
        visited: set[str] = set()
        while roots:
            identity = roots.pop()
            if identity in visited:
                continue
            if identity not in by_id:
                raise ValueError("Cargo graph references a missing package")
            visited.add(identity)
            package = by_id[identity]
            source = package.get("source")
            if source is None:
                if Path(string(package.get("manifest_path"), "Cargo manifest")).resolve().parent not in {p for _, _, p in graph.owned}:
                    raise ValueError("resolved Cargo dependency is an external path/workspace source")
            elif source != CRATES_IO:
                raise ValueError("resolved Cargo release graph contains a non-crates.io source")
            elif not any(d.source.package == package.get("name") and d.version == package.get("version") for d in graph.dependencies):
                raise ValueError("Cargo resolved package has no matching locked registry identity")
            dependencies = edges[identity]
            if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
                raise ValueError("Cargo node requires dependency IDs")
            roots.extend(dependencies)
        from research_repo_tools.dependency_artifacts import verify_cargo_input

        inputs = []
        for identity in sorted(visited):
            package = by_id[identity]
            if package.get("source") is None:
                continue
            matches = [source for source in sources if source.source.package == package.get("name") and source.version == package.get("version")]
            if len(matches) != 1:
                raise ValueError("resolved Cargo input lacks unambiguous verified source evidence")
            inputs.append(verify_cargo_input(Path(string(package.get("manifest_path"), "Cargo manifest")).parent, matches[0], provider=provider))
        for observed in inputs:
            observed.verify()
            retain_guard(observed.verify, observed.digest)
    graph.snapshot.verify()


def verify_graph(graph: DependencyGraph, *, provider: SourceProvider | None = None, release: bool = True) -> tuple[VerifiedSource, ...]:
    """Bind every locked external source to eligible registry/commit evidence."""
    return _verify_graph(graph, provider=provider, release=release, sources={})


def _verify_graph(
    graph: DependencyGraph, *, provider: SourceProvider | None, release: bool, sources: dict[Source, VerifiedSource]
) -> tuple[VerifiedSource, ...]:
    """Private pass-owned reuse of verified sources; each graph checks its lock."""
    results = []
    scope = f"{graph.project.ecosystem}:{graph.project.directory}"
    report("started", "registry graph", graph=scope, total=len(graph.dependencies))
    for index, dependency in enumerate(graph.dependencies):
        verified = sources.get(dependency.source)
        if verified is None:
            verified = verify_source(dependency.source, provider=provider, release=release)
            sources[dependency.source] = verified
        if verified.version != dependency.version:
            raise ValueError("verified source version differs from the lock")
        artifacts = {(a.url, a.sha256, a.size) for a in verified.artifacts}
        if dependency.artifacts and not set(dependency.artifacts) <= artifacts:
            raise ValueError("locked Python artifact differs from published PyPI bytes")
        if dependency.checksum is not None and (len(verified.artifacts) != 1 or verified.artifacts[0].sha256 != dependency.checksum):
            raise ValueError("locked Cargo checksum differs from crates.io")
        results.append(verified)
        report("verified", "registry graph", graph=scope, checked=index + 1, total=len(graph.dependencies), detail=dependency.source.package)
    graph.snapshot.verify()
    return tuple(results)
