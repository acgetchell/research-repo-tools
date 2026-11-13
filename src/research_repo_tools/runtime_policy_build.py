"""Resolve dedicated runtime locks and retain verified portable wheel bundles."""

import json
import os
import sys
import tempfile
import tomllib
from email.parser import Parser
from pathlib import Path

from packaging.tags import parse_tag, sys_tags
from packaging.utils import parse_wheel_filename

from research_repo_tools import release_credentials
from research_repo_tools.dependency_build_receipts import _entry, build_store, read_regular, verify_build_receipt
from research_repo_tools.dependency_builds import locked_sdist
from research_repo_tools.dependency_graphs import inspect_project, verify_graph
from research_repo_tools.dependency_policy import DependencyProject, InputSnapshot, load_policy
from research_repo_tools.dependency_provenance import _check_lock, verify_wheel_payload
from research_repo_tools.dependency_sources import HttpSourceProvider, SourceProvider, table
from research_repo_tools.evidence import deterministic_json, sha256, verify_sha256
from research_repo_tools.files import replace_many
from research_repo_tools.process import resolve_executable, run_command
from research_repo_tools.runtime_policy import (
    MAX_BUNDLE_BYTES,
    MAX_NATIVE_BYTES,
    SCHEMA,
    RuntimePolicy,
    _closure,
    _context,
    _inventory,
    _native_evidence,
    _requirements,
    _wheel_metadata,
    parse_runtime_policy,
    runtime_requirements,
    verify_runtime,
)

__all__ = ["install_runtime", "record_runtime_policy", "resolve_runtime"]


def _installer_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith(("UV_", "PYTHON"))}
    for key in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"):
        if key in os.environ:
            environment[key] = os.environ[key]
    return release_credentials.environment(environment)


def install_runtime(policy: RuntimePolicy, directory: Path, *, artifacts: Path, uv: Path | None = None) -> Path:
    """Install only approved retained wheels into a new external venv, then verify.

    Failed installations remain available for inspection; retry in a new directory.
    The caller must trust uv and its bootstrap interpreter independently.
    """
    if directory.exists() or directory.is_symlink():
        raise ValueError("runtime installation requires a new directory")
    data = json.loads(policy.payload)
    if data["context"] != _context():
        raise ValueError("runtime policy belongs to a different Python/platform")
    artifacts = artifacts.resolve(strict=True)
    for item in data["packages"]:
        payload = read_regular(artifacts / item["filename"], item["size"])
        verify_sha256(payload, item["sha256"])
        _native_evidence(item, artifacts, data["inputs"])
    executable = resolve_executable(uv or "uv")
    directory = directory.absolute()
    environment = _installer_environment()
    with tempfile.TemporaryDirectory(prefix="rrt-runtime-install-") as temporary:
        cwd = Path(temporary)
        requirements = cwd / "requirements.txt"
        requirements.write_bytes(runtime_requirements(policy))
        run_command(executable, ["venv", "--no-config", "--no-python-downloads", "--python", sys.executable, str(directory)], cwd=cwd, env=environment)
        python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run_command(
            executable,
            [
                "pip",
                "sync",
                "--no-config",
                "--no-python-downloads",
                "--python",
                str(python),
                "--no-index",
                "--find-links",
                str(artifacts),
                "--require-hashes",
                "--no-build",
                "--link-mode",
                "copy",
                "--strict",
                str(requirements),
            ],
            cwd=cwd,
            env=environment,
        )
    verify_runtime(policy, environment=directory, artifacts=artifacts)
    return python


def resolve_runtime(directory: Path, requirements: tuple[str, ...], *, uv: Path | None = None) -> Path:
    """Create a new dedicated virtual project and resolve its PyPI-only lock.

    Does not install, build, run checkout code or grant release authorization.
    A failed resolution leaves the new project available for inspection.
    """
    parsed = _requirements(list(requirements))
    if directory.exists() or directory.is_symlink():
        raise ValueError("runtime resolution requires a new directory; existing locks are never overwritten")
    executable = resolve_executable(uv or "uv")
    environment = _installer_environment()
    with tempfile.TemporaryDirectory(prefix="rrt-runtime-resolve-") as temporary:
        directory.mkdir(parents=True)
        root = directory.resolve(strict=True)
        manifest = (
            '[project]\nname = "release-runtime"\nversion = "0.0.0"\n'
            f'requires-python = "=={_context()["python"]}"\n'
            f"dependencies = {json.dumps([str(requirement) for requirement in parsed])}\n"
            "[tool.uv]\npackage = false\n[tool.uv.workspace]\n"
        ).encode("utf-8")
        replace_many(
            {
                root / "pyproject.toml": manifest,
                root / "dependency-sources.toml": b'schema = 1\n[projects.runtime]\necosystem = "python"\n',
            }
        )
        environment["UV_PROJECT_ENVIRONMENT"] = str(Path(temporary) / "uncreated")
        run_command(
            executable,
            ["lock", "--no-config", "--no-build", "--no-python-downloads", "--python", sys.executable, "--directory", str(root), "--project", str(root)],
            cwd=Path(temporary),
            env=environment,
        )
    inspect_project(root, DependencyProject("python"))
    return root / "uv.lock"


def record_runtime_policy(root: Path, destination: Path, *, provider: SourceProvider | None = None, build_directory: Path | None = None) -> RuntimePolicy:
    """Authenticate the dedicated lock, installed wheels and current build receipts.

    Retain wheels, hash requirements and a manifest for independent review/pinning.
    No signing keys or machine-local receipt stores are exported.
    """
    root = root.resolve(strict=True)
    destination = destination.absolute()
    environment = root / ".venv"
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(root):
        raise ValueError("runtime recording requires a new bundle directory outside the runtime project")
    entry = InputSnapshot.capture(tuple(root / name for name in ("pyproject.toml", "uv.lock", "dependency-sources.toml")))
    policy = load_policy(root)
    if policy is None or policy.projects != (DependencyProject("python"),) or policy.selections:
        raise ValueError("runtime recording requires exactly one dedicated Python project without source overrides")
    document = tomllib.loads(read_regular(root / "pyproject.toml", 4 * 1024 * 1024).decode("utf-8"))
    uv = table(table(document.get("tool", {}), "tool").get("uv", {}), "uv")
    if uv.get("package") is not False or "build-system" in document:
        raise ValueError("runtime project must be virtual with tool.uv.package=false and no build backend")
    requirements = [str(requirement) for requirement in _requirements(table(document.get("project"), "runtime project").get("dependencies"))]
    graph = inspect_project(root, policy.projects[0])
    entry.verify()
    _check_lock(root)
    sources = verify_graph(graph, provider=provider or HttpSourceProvider())
    configuration = read_regular(environment / "pyvenv.cfg", 65536)
    inventory, observations = _inventory(environment)
    inputs = {name: sha256(read_regular(root / name, 4 * 1024 * 1024)) for name in ("pyproject.toml", "uv.lock", "dependency-sources.toml")}
    packages = []
    updates = {}
    metadata = {}
    native = []
    total_bytes = 0
    for name, observed in sorted(observations.items()):
        version = observed["version"]
        matches = [
            (dependency, source)
            for dependency, source in zip(graph.dependencies, sources, strict=True)
            if dependency.source.package == name and dependency.version == version
        ]
        if len(matches) != 1:
            raise ValueError(f"{name}: installed runtime version differs from its dedicated lock")
        dependency, source = matches[0]
        if policy.source_builds is not None and name in policy.source_builds.packages:
            build_version, sdist = locked_sdist(graph, name)
            receipt = verify_build_receipt(root, name, build_version, sdist, directory=build_directory)
            receipt_path = build_store(root, build_directory) / (_entry(root, name) + ".json")
            receipt_bytes = read_regular(receipt_path, 4 * 1024 * 1024)
            envelope = json.loads(receipt_bytes)
            if envelope["payload"]["context"]["inputs"] != inputs:
                raise ValueError("runtime native-build inputs changed during recording")
            provenance = {
                "kind": "source-build",
                "source": {"url": sdist["url"], "sha256": str(sdist["hash"])[7:], "size": sdist["size"]},
                "receipt_sha256": sha256(receipt_bytes),
                "build_log_sha256": envelope["payload"]["build_log_sha256"],
                "inputs": inputs,
            }
            payload, filename = receipt.payload, receipt.wheel.name
            updates[destination / (sha256(receipt_bytes) + ".receipt.json")] = receipt_bytes
            log_hash = envelope["payload"]["build_log_sha256"]
            log = read_regular(build_store(root, build_directory) / (log_hash + ".log"), 4 * 1024 * 1024)
            verify_sha256(log, log_hash)
            updates[destination / (log_hash + ".log")] = log
            native.append((name, build_version, sdist, sha256(payload), receipt_path, receipt_bytes))
        else:
            tags = {tag for raw in Parser().parsestr(inventory[name].wheel or "").get_all("Tag", []) for tag in parse_tag(raw)}
            candidates = [
                artifact
                for artifact in source.artifacts
                if artifact.filename.endswith(".whl")
                and parse_wheel_filename(artifact.filename)[3] == tags
                and (artifact.url, artifact.sha256, artifact.size) in dependency.artifacts
            ]
            if len(candidates) != 1:
                raise ValueError(f"{name}: runtime needs exactly one matching locked published wheel")
            artifact = candidates[0]
            if artifact.size > MAX_NATIVE_BYTES or total_bytes + artifact.size > MAX_BUNDLE_BYTES:
                raise ValueError("runtime wheel exceeds its byte limit")
            if provider is None:
                from research_repo_tools._registry_requests import read_registry

                payload = read_registry(artifact.url, limit=artifact.size)
            else:
                payload = provider.read(artifact.url)
            if not isinstance(payload, bytes) or len(payload) != artifact.size:
                raise ValueError("runtime wheel size differs from its authenticated registry identity")
            verify_sha256(payload, artifact.sha256)
            filename = artifact.filename
            provenance = {"kind": "registry", "url": artifact.url}
        total_bytes += len(payload)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("runtime wheels exceed the aggregate byte limit")
        if not parse_wheel_filename(filename)[3].intersection(sys_tags()):
            raise ValueError("runtime wheel is incompatible with the executing Python/platform")
        verify_wheel_payload(name, payload, observed, scripts=environment / ("Scripts" if os.name == "nt" else "bin"), limit=MAX_NATIVE_BYTES)
        metadata[name] = _wheel_metadata(payload, name, version)
        packages.append({"name": name, "version": version, "filename": filename, "sha256": sha256(payload), "size": len(payload), "provenance": provenance})
        updates[destination / filename] = payload
    _closure(requirements, metadata)
    payload = deterministic_json({"schema": SCHEMA, "context": _context(), "requirements": requirements, "inputs": inputs, "packages": packages})
    result = parse_runtime_policy(payload, expected_sha256=sha256(payload))
    updates[destination / "runtime.json"] = payload
    updates[destination / "requirements.txt"] = runtime_requirements(result)

    def unchanged() -> None:
        entry.verify()
        graph.snapshot.verify()
        if _inventory(environment)[1] != observations or read_regular(environment / "pyvenv.cfg", 65536) != configuration:
            raise ValueError("runtime installation changed during recording")
        for name, version, sdist, wheel_hash, receipt_path, receipt_bytes in native:
            receipt = verify_build_receipt(root, name, version, sdist, directory=build_directory)
            if sha256(receipt.payload) != wheel_hash or read_regular(receipt_path, 4 * 1024 * 1024) != receipt_bytes:
                raise ValueError("runtime native-build evidence changed during recording")

    # Reserve the new directory after acquisition, so a destination created by
    # another writer since the entry check is never treated as replaceable data.
    try:
        destination.mkdir(parents=True)
    except FileExistsError as error:
        raise ValueError("runtime recording requires a new bundle directory") from error
    try:
        replace_many(updates, verify=unchanged)
    except BaseException:
        try:
            destination.rmdir()
        except OSError:
            # Preserve nonempty recovery state and the original failure.
            pass
        raise
    return result
