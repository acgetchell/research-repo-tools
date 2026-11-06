"""Verified source transitions and published-only dependency release checks."""

import difflib
import json
import os
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from research_repo_tools import toml_source
from research_repo_tools.dependency_graphs import CRATES_IO, _resolve_cargo, check_native_graph, inspect_project, verify_graph
from research_repo_tools.dependency_policy import (
    MAX_SNAPSHOT_FILES,
    POLICY_FILE,
    InputSnapshot,
    SourceSelection,
    configuration_paths,
    inside,
    load_policy,
    project_manifests,
)
from research_repo_tools.dependency_sources import GitCommit, RegistryRelease, Source, SourceProvider, VerifiedSource, table, verify_source
from research_repo_tools.files import replace_many

__all__ = ["SourcePlan", "apply_source", "check_dependency_policy", "plan_source"]


@dataclass(frozen=True, slots=True, init=False)
class SourcePlan:
    """A fully resolved candidate bound to unchanged inputs and verified source."""

    root: Path
    selection: SourceSelection
    verified: VerifiedSource
    snapshot: InputSnapshot
    writes: tuple[tuple[Path, bytes], ...]

    def __init__(self) -> None:
        raise TypeError("use plan_source to obtain a SourcePlan")

    def diff(self) -> str:
        originals = dict(self.snapshot.files)
        return "".join(
            "".join(
                difflib.unified_diff(
                    (originals[path] or b"").decode("utf-8").splitlines(keepends=True),
                    content.decode("utf-8").splitlines(keepends=True),
                    fromfile=str(path.relative_to(self.root)),
                    tofile=str(path.relative_to(self.root)),
                )
            )
            for path, content in self.writes
        )


def _table_name(parts: tuple[str, ...]) -> str:
    return ".".join(json.dumps(part) for part in parts)


def _literal(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if type(value) is bool:
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(key) + " = " + _literal(item) for key, item in value.items()) + " }"
    raise ValueError("unsupported dependency declaration value")


def _python_arrays(document: dict[str, object]) -> Iterator[tuple[tuple[str, ...], str, list[object]]]:
    project = table(document.get("project", {}), "project")
    groups = table(document.get("dependency-groups", {}), "dependency-groups")
    optional = table(project.get("optional-dependencies", {}), "optional dependencies")
    uv = table(table(document.get("tool", {}), "tool").get("uv", {}), "uv")
    for path, values in (
        (("project",), {"dependencies": project.get("dependencies", [])}),
        (("dependency-groups",), groups),
        (("project", "optional-dependencies"), optional),
        (("build-system",), table(document.get("build-system", {}), "build system")),
        (
            ("tool", "uv"),
            {key: uv[key] for key in ("dev-dependencies", "constraint-dependencies", "override-dependencies", "build-constraint-dependencies") if key in uv},
        ),
    ):
        for key, entries in values.items():
            if path == ("build-system",) and key != "requires":
                continue
            if not isinstance(entries, list):
                raise ValueError("Python requirements must be arrays")
            yield path, key, list[object](entries)


def _python_selection(text: str, verified: VerifiedSource) -> str:
    source = verified.source
    document = table(tomllib.loads(text), "manifest")
    found = False
    for path, key, values in _python_arrays(document):
        replacements = {}
        for value in values:
            if not isinstance(value, str):
                continue
            requirement = Requirement(value)
            if canonicalize_name(requirement.name) != source.package:
                continue
            found = True
            extras = "[" + ",".join(sorted(requirement.extras)) + "]" if requirement.extras else ""
            marker = f"; {requirement.marker}" if requirement.marker else ""
            replacements[value] = f"{requirement.name}{extras}=={verified.version}{marker}"
        if replacements:
            text = toml_source.replace_array_strings(text, _table_name(path), key, replacements)
    if not found:
        return text
    uv = table(table(document.get("tool", {}), "tool").get("uv", {}), "uv")
    sources = table(uv.get("sources", {}), "sources")
    matches = [key for key in sources if canonicalize_name(key) == source.package]
    if len(matches) > 1:
        raise ValueError("ambiguous Python source aliases")
    key = matches[0] if matches else source.package
    if isinstance(source, RegistryRelease):
        if matches:
            text = toml_source.remove_value(text, "tool.uv.sources", key)
    else:
        value: dict[str, object] = {"git": source.url, "rev": source.commit}
        if source.directory != ".":
            value["subdirectory"] = source.directory
        # A single selected source applies to all existing requirement markers,
        # extras and dependency groups; those declaration semantics stay intact.
        text = toml_source.set_value(text, "tool.uv.sources", key, _literal(value))
    return text


def _cargo_tables(document: dict[str, object]) -> Iterator[tuple[tuple[str, ...], dict[str, object]]]:
    sections = [((), document), (("workspace",), table(document.get("workspace", {}), "workspace"))]
    sections.extend((("target", target), table(value, "target")) for target, value in table(document.get("target", {}), "targets").items())
    for path, section in sections:
        for kind in ("dependencies", "build-dependencies", "dev-dependencies"):
            yield (*path, kind), table(section.get(kind, {}), kind)


def _cargo_selection(text: str, verified: VerifiedSource) -> str:
    source = verified.source
    document = table(tomllib.loads(text), "Cargo manifest")
    patches = table(table(document.get("patch", {}), "patch").get("crates-io", {}), "crates.io patches")
    for key, raw in patches.items():
        dependency = table(raw, "Cargo patch")
        if dependency.get("package", key) == source.package:
            if isinstance(source, RegistryRelease):
                text = toml_source.remove_value(text, "patch.crates-io", key)
            else:
                replacement = {k: v for k, v in dependency.items() if k not in {"git", "branch", "tag", "rev", "path", "version"}}
                replacement.update(git=source.url, rev=source.commit, version="=" + verified.version)
                text = toml_source.set_value(text, "patch.crates-io", key, _literal(replacement))
    for path, dependencies in _cargo_tables(document):
        for alias, raw in dependencies.items():
            dependency = {"version": raw} if isinstance(raw, str) else dict(table(raw, "Cargo dependency"))
            if dependency.get("package", alias) != source.package or dependency.get("workspace") is True:
                continue
            if set(dependency) & {"registry", "registry-index"}:
                raise ValueError("dependency transitions support crates.io only")
            replacement = {key: value for key, value in dependency.items() if key not in {"git", "branch", "tag", "rev", "path", "version"}}
            replacement["version"] = "=" + verified.version
            if isinstance(source, GitCommit):
                replacement.update(git=source.url, rev=source.commit)
            try:
                toml_source.key_line(text, _table_name(path), alias)
            except ValueError:
                # Expanded dependency tables retain unrelated field comments.
                owner = _table_name((*path, alias))
                for key in dependency.keys() - replacement.keys():
                    text = toml_source.remove_value(text, owner, key)
                for key, value in replacement.items():
                    if dependency.get(key) != value:
                        text = toml_source.set_value(text, owner, key, _literal(value))
            else:
                text = toml_source.set_value(text, _table_name(path), alias, _literal(replacement))
    return text


def _source_files(root: Path, manifests: tuple[Path, ...]) -> tuple[Path, ...]:
    paths = []
    pending = [root]
    entries = 0
    while pending:
        with os.scandir(pending.pop()) as directory:
            for entry in directory:
                path = Path(entry.path)
                if entry.name == ".git":
                    continue
                excluded = entry.name in {".venv", "target", "dist", "coverage", "__pycache__", ".pytest_cache", ".ruff_cache"}
                if excluded and entry.is_dir(follow_symlinks=False) and not any(manifest.is_relative_to(path) for manifest in manifests):
                    continue
                entries += 1
                if entries > MAX_SNAPSHOT_FILES:
                    raise ValueError("dependency source inventory exceeds the entry-count limit")
                if entry.is_symlink():
                    raise ValueError("source transitions require an unlinked candidate checkout")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    paths.append(path)
                else:
                    raise ValueError("source transitions require regular files")
    return tuple(sorted(paths, key=str))


def _resolve_candidate(root: Path, verified: VerifiedSource, *, checkout: Path, candidate_checkout: Path) -> None:
    from research_repo_tools.process import resolve_executable, run_command
    from research_repo_tools.release_credentials import environment as without_credentials

    source = verified.source
    cwd = candidate_checkout.parent
    environment = {
        key: value
        for key, value in without_credentials().items()
        if (
            key.upper() in {"CARGO_HOME", "RUSTUP_HOME"}
            or (source.ecosystem == "cargo" and key.upper() == "RUSTUP_TOOLCHAIN")
            or not key.upper().startswith(("UV_", "CARGO_", "RUST"))
        )
        and key.upper() != "RRT_GITHUB_LOOKUP_TOKEN"
    }
    environment["PATH"] = os.pathsep.join(
        entry
        for entry in os.get_exec_path()
        if Path(entry).is_absolute() and not Path(entry).resolve().is_relative_to(checkout) and not Path(entry).resolve().is_relative_to(candidate_checkout)
    )
    executable = (
        resolve_executable("uv", cwd=cwd, env=environment)
        if source.ecosystem == "python"
        else _resolve_cargo(cwd=cwd, environment=environment, checkouts=(checkout, candidate_checkout))
    )
    if executable.resolve().is_relative_to(candidate_checkout) or executable.resolve().is_relative_to(checkout):
        raise ValueError("dependency resolver must be outside the candidate checkout")
    if source.ecosystem == "python":
        environment["UV_PROJECT_ENVIRONMENT"] = str(root.parent / "uncreated-environment")
        arguments = ["lock", "--no-build", "--no-python-downloads", "--python", sys.executable, "--project", str(root)]
    else:
        arguments = [
            "update",
            "--manifest-path",
            str(root / "Cargo.toml"),
            "--package",
            source.package,
        ]
        # --precise is interpreted using the OLD locked source. A commit is
        # invalid for a registry entry, and a version is a Git revision for a
        # Git entry. The manifest already pins Git rev; reconcile a changed
        # origin before selecting an exact registry version (also for patches).
        if isinstance(source, RegistryRelease):
            locked = tomllib.loads((root / "Cargo.lock").read_text(encoding="utf-8"))
            if any(item.get("name") == source.package and item.get("source") != CRATES_IO for item in locked.get("package", [])):
                run_command(executable, arguments, cwd=cwd, env=environment, timeout=300)
            arguments.extend(("--precise", source.version))
    run_command(executable, arguments, cwd=cwd, env=environment, timeout=300)


def _matches(verified: VerifiedSource, locked: Source, version: str) -> bool:
    source = verified.source
    if isinstance(source, GitCommit) and isinstance(locked, GitCommit):
        if source.ecosystem == "python" and source.directory != locked.directory:
            return False
        return (source.ecosystem, source.package, source.repository, source.commit, verified.version) == (
            locked.ecosystem,
            locked.package,
            locked.repository,
            locked.commit,
            version,
        )
    return source == locked and verified.version == version


def plan_source(root: Path, name: str, *, provider: SourceProvider | None = None) -> SourcePlan:
    """Resolve an explicit selection in a disposable copy; return its full diff.

    The original manifest, lock, environment and evidence remain untouched.
    Native resolution may download exact selected sources into its normal cache.
    No consumer startup or build hooks are run (uv resolution uses --no-build).
    """
    root = root.resolve(strict=True)
    policy = load_policy(root)
    selections = [] if policy is None else [item for item in policy.selections if item.name == name]
    if len(selections) != 1:
        raise ValueError("source name must identify one dependency-sources.toml selection")
    selection = selections[0]
    verified = verify_source(selection.source, provider=provider)
    project_root = inside(root, selection.project.directory)
    # Capture all copied source/harness bytes so a plan cannot publish a lock
    # resolved against a different workspace or silently preserve old evidence.
    manifests = project_manifests(project_root, selection.project.ecosystem)
    files = _source_files(root, manifests)
    snapshot = InputSnapshot.capture((*files, *configuration_paths(project_root, selection.project.ecosystem)))
    originals = dict(snapshot.files)
    from research_repo_tools.dependency_graphs import _check_configuration

    _check_configuration(project_root, selection.project.ecosystem)
    changes: dict[Path, bytes] = {}
    declared = False
    transform = _python_selection if selection.project.ecosystem == "python" else _cargo_selection
    for path in manifests:
        original = originals[path]
        if original is None:
            raise ValueError("source transition requires captured workspace manifests")
        if selection.project.ecosystem == "cargo":
            for _, dependencies in _cargo_tables(table(tomllib.loads(original.decode("utf-8")), "Cargo manifest")):
                declared |= any((raw.get("package", key) if isinstance(raw, dict) else key) == selection.source.package for key, raw in dependencies.items())
        candidate = transform(original.decode("utf-8"), verified).encode("utf-8")
        if candidate != original:
            changes[path] = candidate
    if selection.project.ecosystem == "cargo" and isinstance(verified.source, GitCommit) and not declared and not changes:
        # Cargo's native patch mechanism also supports an exact upstream fix
        # selected transitively; no consumer-owned source string is needed.
        manifest = project_root / "Cargo.toml"
        original = originals[manifest]
        if original is None:
            raise ValueError("source transition requires a captured root manifest")
        text = original.decode("utf-8")
        value = {"git": verified.source.url, "rev": verified.source.commit, "version": "=" + verified.version}
        candidate = toml_source.set_value(text, "patch.crates-io", verified.source.package, _literal(value)).encode("utf-8")
        if candidate != original:
            changes[manifest] = candidate
    lock = project_root / ("uv.lock" if selection.project.ecosystem == "python" else "Cargo.lock")
    original_lock = originals.get(lock)
    if original_lock is None:
        raise ValueError("source transition requires an existing native lock")
    with tempfile.TemporaryDirectory(prefix="dependency-candidate-") as directory:
        candidate_root = Path(directory) / "checkout"
        source_files = set(files)
        for path, original in snapshot.files:
            if path not in source_files or original is None:
                continue
            target = candidate_root / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
        for path, content in changes.items():
            (candidate_root / path.relative_to(root)).write_bytes(content)
        _resolve_candidate(inside(candidate_root, selection.project.directory), verified, checkout=root, candidate_checkout=candidate_root)
        graph = inspect_project(candidate_root, selection.project, release=False)
        matches = [dependency for dependency in graph.dependencies if dependency.source.package == selection.source.package]
        if not matches or any(not _matches(verified, item.source, item.version) for item in matches):
            raise ValueError("native resolver did not select the verified package source")
        if isinstance(verified.source, RegistryRelease):
            available = {(a.url, a.sha256, a.size) for a in verified.artifacts}
            if any(
                item.artifacts and not set(item.artifacts) <= available or item.checksum and item.checksum not in {a.sha256 for a in verified.artifacts}
                for item in matches
            ):
                raise ValueError("resolved artifact identity differs from the verified published source")
        content = (candidate_root / lock.relative_to(root)).read_bytes()
        if content != original_lock:
            changes[lock] = content
    snapshot.verify()
    if files != _source_files(root, manifests):
        raise ValueError("source inventory changed during dependency planning")
    result = object.__new__(SourcePlan)
    for key, value in (("root", root), ("selection", selection), ("verified", verified), ("snapshot", snapshot), ("writes", tuple(changes.items()))):
        object.__setattr__(result, key, value)
    return result


def apply_source(plan: SourcePlan, *, provider: SourceProvider | None = None) -> None:
    """Reverify source and inputs for recoverable multi-file publication of manifest + lock.

    Failure preserves the prior valid files; incomplete rollback reports retained
    recovery backups through the shared file-publication exception contract.
    Existing measurements are retained and never relabeled as fresh evidence.
    """
    if not isinstance(plan, SourcePlan):
        raise TypeError("apply_source requires a SourcePlan")
    plan.snapshot.verify()
    if verify_source(plan.selection.source, provider=provider) != plan.verified:
        raise ValueError("upstream source evidence changed; preview the transition again")
    expected = tuple(sorted((path for path, content in plan.snapshot.files if path.is_relative_to(plan.root) and content is not None), key=str))

    manifests = project_manifests(inside(plan.root, plan.selection.project.directory), plan.selection.project.ecosystem)
    if _source_files(plan.root, manifests) != expected:
        raise ValueError("source inventory changed; preview the transition again")
    # File publication creates its own temporary siblings while staging. Check
    # the source inventory before staging and exact original inputs afterwards.
    replace_many(dict(plan.writes), verify=plan.snapshot.verify)


def check_dependency_policy(root: Path, *, provider: SourceProvider | None = None) -> tuple[VerifiedSource, ...]:
    """Recheck every configured project before release effects, without mutation."""
    policy = load_policy(root)
    if policy is None:
        _require_dependency_free(root)
        return ()
    # Reject a development selection even if the native manifest happens to
    # look registry-only; source, declaration and lock are separate obligations.
    if any(isinstance(selection.source, GitCommit) for selection in policy.selections):
        raise ValueError("release source policy still selects a development commit; transition to its published registry release and revalidate")
    graphs = [inspect_project(root, project) for project in policy.projects]
    results: list[VerifiedSource] = []
    verified_graphs = []
    for graph in graphs:
        for selection in policy.selections:
            if selection.project != graph.project:
                continue
            matches = [item for item in graph.dependencies if item.source.package == selection.source.package]
            if not matches or any(item.source != selection.source for item in matches):
                raise ValueError("declared source selection differs from the release lock")
        # Provider rejection precedes even a native graph-inspection subprocess.
        verified = verify_graph(graph, provider=provider)
        verified_graphs.append((graph, verified))
        results.extend(verified)
    for graph, verified in verified_graphs:
        check_native_graph(graph, sources=verified, provider=provider)
    for graph in graphs:
        graph.snapshot.verify()
    return tuple(results)


def _require_dependency_free(root: Path) -> None:
    """An absent policy can only release a checkout with no dependency graph."""
    from research_repo_tools.dependency_graphs import _cargo_dependencies, _check_configuration, _requirements

    paths = (root / POLICY_FILE, root / "pyproject.toml", root / "uv.lock", root / "Cargo.toml", root / "Cargo.lock")
    snapshot = InputSnapshot.capture((*paths, *configuration_paths(root, "python"), *configuration_paths(root, "cargo")))
    for ecosystem, manifest, lock in (("python", paths[1], paths[2]), ("cargo", paths[3], paths[4])):
        if not manifest.exists():
            if lock.exists():
                raise ValueError("release dependency graph requires dependency-sources.toml")
            continue
        _check_configuration(root, ecosystem)
        data = table(tomllib.loads(manifest.read_text(encoding="utf-8")), "dependency manifest")
        project = table(data.get("project" if ecosystem == "python" else "package", {}), "project")
        owned_name = project.get("name")
        if ecosystem == "python" and isinstance(owned_name, str):
            owned_name = canonicalize_name(owned_name)
        if ecosystem == "python":
            uv = table(table(data.get("tool", {}), "tool").get("uv", {}), "uv")
            configured = bool(tuple(_requirements(data))) or bool(
                set(uv) & {"sources", "index", "index-url", "extra-index-url", "find-links", "no-index", "dependency-metadata"}
            )
            workspace = table(uv.get("workspace", {}), "uv workspace")
        else:
            configured = bool(tuple(_cargo_dependencies(data))) or bool(set(data) & {"patch", "replace"})
            workspace = table(data.get("workspace", {}), "Cargo workspace")
        members = workspace.get("members", [])
        if not isinstance(members, list):
            raise ValueError("workspace members must be an array")
        configured |= bool(members)
        if lock.exists():
            version = project.get("version")
            if ecosystem == "cargo" and version == {"workspace": True}:
                version = table(workspace.get("package", {}), "workspace.package").get("version")
            packages = table(tomllib.loads(lock.read_text(encoding="utf-8")), "lock").get("package")
            if not isinstance(packages, list):
                raise ValueError("release dependency graph requires dependency-sources.toml")
            for raw in packages:
                item = table(raw, "locked package")
                local = item.get("source") in ({"editable": "."}, {"virtual": "."}) if ecosystem == "python" else item.get("source") is None
                configured |= not local or (item.get("name"), item.get("version")) != (owned_name, version)
        if configured:
            raise ValueError("release dependency graph requires dependency-sources.toml; declare projects and release-owned boundaries")
    snapshot.verify()
