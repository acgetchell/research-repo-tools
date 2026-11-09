"""Explicitly rebuild registry sdists and authenticate their local wheel outputs.

Build hooks execute verified upstream code in fresh environments; virtualenvs
are not security sandboxes. Receipts describe the trusted builder's observation,
not a claim of reproducibility or a complete hermetic system dependency closure.
"""

import io
import os
import shutil
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import override
from urllib.parse import urlsplit

from research_repo_tools import _dependency_probe
from research_repo_tools.dependency_build_policy import ExternalBuildInput, SourceBuildPolicy, native_platform
from research_repo_tools.dependency_build_receipts import MAX_WHEEL_BYTES, VerifiedBuild, _publish_build_receipt, canonical, context, digest, read_regular
from research_repo_tools.dependency_graphs import DependencyGraph, inspect_project, verify_graph
from research_repo_tools.dependency_policy import load_policy
from research_repo_tools.dependency_sources import HttpSourceProvider, SourceProvider, string, table
from research_repo_tools.process import get_safe_executable, run_safe_command


class _ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.netloc not in {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
            raise ValueError("external build input redirected outside GitHub release storage")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _external(input: ExternalBuildInput) -> tuple[dict[str, object], bytes]:
    with urllib.request.build_opener(_ReleaseRedirect()).open(input.url, timeout=60) as response:
        payload = response.read(MAX_WHEEL_BYTES + 1)
    if len(payload) > MAX_WHEEL_BYTES or digest(payload) != input.sha256:
        raise ValueError("external build input differs from its pinned SHA-256")
    if input.url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            matches = [entry for entry in archive.infolist() if entry.filename == input.member]
            if len(matches) != 1 or matches[0].is_dir() or matches[0].file_size > MAX_WHEEL_BYTES:
                raise ValueError("external build payload is missing, ambiguous, or oversized")
            data = archive.read(matches[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            matches = [entry for entry in archive.getmembers() if entry.name == input.member]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > MAX_WHEEL_BYTES:
                raise ValueError("external build payload is missing, ambiguous, or oversized")
            stream = archive.extractfile(matches[0])
            if stream is None:
                raise ValueError("external build payload cannot be read")
            with stream:
                data = stream.read(MAX_WHEEL_BYTES + 1)
    return {**asdict(input), "payload_sha256": digest(data)}, data


def locked_sdist(graph: DependencyGraph, package: str) -> tuple[str, dict[str, object]]:
    lock = tomllib.loads((graph.root / "uv.lock").read_text(encoding="utf-8"))
    matches = [item for item in lock["package"] if item["name"] == package]
    if len(matches) != 1:
        raise ValueError(f"{package}: source build requires one locked version")
    entry = matches[0]
    if entry.get("source") != {"registry": "https://pypi.org/simple"}:
        raise ValueError("source builds require a locked PyPI source")
    source = dict(table(entry.get("sdist"), "locked source archive"))
    source.pop("upload-time", None)
    if set(source) != {"url", "hash", "size"}:
        raise ValueError("source build requires a locked source archive URL, size and SHA-256")
    return string(entry.get("version"), "locked version"), source


def _build_environment(root: Path, temporary: Path, policy: SourceBuildPolicy, uv: Path) -> tuple[Path, dict[str, str], dict[str, object]]:
    environment = {
        key: value
        for key, value in os.environ.items()
        # setuptools locates vswhere through ProgramFiles; vswhere's COM service
        # needs ProgramData to discover installed MSVC instances (vswhere#236).
        # Match case-insensitively while retaining the native spelling.
        if key.upper()
        in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"}
    }
    home = temporary / "home"
    home.mkdir()
    environment.update({"HOME": str(home), "USERPROFILE": str(home), "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1"})
    build_env = temporary / "environment"
    run_safe_command(
        str(uv), ["venv", "--no-config", "--no-python-downloads", "--python", sys.executable, str(build_env)], cwd=temporary, env=environment, timeout=120
    )
    python = build_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = temporary / "requirements.txt"
    result = run_safe_command(
        str(uv),
        [
            "export",
            "--locked",
            "--no-config",
            "--no-sources",
            "--no-emit-project",
            "--no-emit-workspace",
            "--only-group",
            policy.group,
            "--format",
            "requirements-txt",
            "--project",
            str(root),
        ],
        cwd=temporary,
        env=environment,
        timeout=120,
    )
    requirements.write_text(result.stdout, encoding="utf-8", newline="\n")
    run_safe_command(
        str(uv),
        [
            "pip",
            "install",
            "--no-config",
            "--no-cache",
            "--python",
            str(python),
            "--require-hashes",
            "--only-binary",
            ":all:",
            "--index-url",
            "https://pypi.org/simple",
            "-r",
            str(requirements),
        ],
        cwd=temporary,
        env=environment,
        timeout=300,
    )
    return (
        python,
        environment,
        {
            "uv_sha256": digest(read_regular(uv, MAX_WHEEL_BYTES)),
            "requirements_sha256": digest(requirements.read_bytes()),
            "installed": _tool_inventory(build_env),
        },
    )


def _tool_inventory(build_env: Path) -> dict[str, object]:
    from importlib.metadata import distributions

    sites = (build_env / "Lib/site-packages",) if os.name == "nt" else tuple((build_env / "lib").glob("python*/site-packages"))
    inventory = {}
    for dist in distributions(path=[str(site) for site in sites]):
        name = dist.metadata["Name"]
        observed = _dependency_probe.inspect_installation(name, paths=sites)
        if observed["direct_url"] is not None:
            raise ValueError("build tools must be installed from locked registry wheels")
        inventory[name] = {"version": observed["version"], "files_sha256": digest(canonical(observed["files"]))}
    return inventory


def prepare_dependency_builds(root: Path, *, directory: Path | None = None, provider: SourceProvider | None = None) -> tuple[Path, ...]:
    """Build every explicitly selected sdist, then install authenticated wheels.

    No receipt is issued for pre-existing installed bytes. A failed install may
    leave partial environment changes; the release gate still checks every file.
    Signing keys and retained wheels remain machine-local, outside the checkout.
    """
    root = root.resolve(strict=True)
    policy = load_policy(root)
    if policy is None:
        raise ValueError("declare dependency projects in dependency-sources.toml")
    if policy.source_builds is None:
        return ()
    project = next(project for project in policy.projects if project.ecosystem == "python")
    graph = inspect_project(root, project)
    if graph.root != root:
        raise ValueError("prepare source builds from the declared Python project root")
    build_policy = policy.source_builds
    before = context(root)
    uv = Path(get_safe_executable("uv")).resolve()
    verify_graph(graph, provider=provider)
    source_provider = provider or HttpSourceProvider()
    completed: list[tuple[str, str, dict[str, object], Path, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="verified-dependency-build-") as temporary_name:
        temporary = Path(temporary_name)
        python, environment, tools = _build_environment(root, temporary, build_policy, uv)
        for package in build_policy.packages:
            version, source = locked_sdist(graph, package)
            url, size, expected = source["url"], source["size"], source["hash"]
            if not isinstance(url, str) or type(size) is not int or not isinstance(expected, str):
                raise ValueError("malformed locked source archive")
            payload = source_provider.read(url)
            if len(payload) != size or "sha256:" + digest(payload) != expected:
                raise ValueError(f"{package}: source archive differs from the verified lock")
            filename = Path(urlsplit(url).path).name
            source_path = temporary / filename
            source_path.write_bytes(payload)
            output = temporary / package
            output.mkdir()
            inputs = [item for item in build_policy.external if item.package == package and item.platform == native_platform()]
            if any(item.package == package for item in build_policy.external) and not inputs:
                raise ValueError(f"{package}: no external build inputs declared for {native_platform()}")
            external = [_external(item) for item in inputs]
            print(f"Building verified source: {package}=={version}", file=sys.stderr, flush=True)
            result = run_safe_command(
                str(uv),
                [
                    "build",
                    "--no-config",
                    "--no-cache",
                    "--offline",
                    "--no-sources",
                    "--no-build-isolation",
                    "--no-python-downloads",
                    "--python",
                    str(python),
                    "--wheel",
                    "--out-dir",
                    str(output),
                    str(source_path),
                ],
                cwd=temporary,
                env=environment,
                timeout=600,
            )
            wheels = list(output.glob("*.whl"))
            if source_path.read_bytes() != payload or _tool_inventory(temporary / "environment") != tools["installed"]:
                raise ValueError("source archive or locked build tools changed during the build")
            if len(wheels) != 1:
                raise ValueError(f"{package}: build must produce exactly one wheel")
            wheel = wheels[0]
            with zipfile.ZipFile(wheel) as archive:
                for item, (_, data) in zip(inputs, external, strict=True):
                    if archive.namelist().count(item.wheel_member) != 1 or archive.read(item.wheel_member) != data:
                        raise ValueError(f"{package}: built external payload differs from the verified release asset")
            build_log = canonical({"stdout": result.stdout, "stderr": result.stderr})
            record: dict[str, object] = {
                "source": source,
                "tools": tools,
                "external": [record for record, _ in external],
                "build_log_sha256": digest(build_log),
            }
            completed.append((package, version, record, wheel, build_log))
        graph.snapshot.verify()
        if context(root) != before:
            raise ValueError("source-build inputs changed during preparation")
        # Signing occurs in the parent only after all build hooks have completed.
        verified: list[VerifiedBuild] = [
            _publish_build_receipt(root, name, version, record, wheel, build_log=log, directory=directory) for name, version, record, wheel, log in completed
        ]
        wheelhouse = temporary / "wheelhouse"
        wheelhouse.mkdir()
        for item in verified:
            shutil.copyfile(item.wheel, wheelhouse / item.wheel.name)
        target = root / os.environ.get("UV_PROJECT_ENVIRONMENT", ".venv")
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError("source-build installation must use the normal contained environment")
        target_python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run_safe_command(
            str(uv),
            [
                "pip",
                "install",
                "--no-config",
                "--no-cache",
                "--no-index",
                "--no-deps",
                "--reinstall",
                "--python",
                str(target_python),
                "--find-links",
                str(wheelhouse),
                *(f"{name}=={version}" for name, version, _, _, _ in completed),
            ],
            cwd=temporary,
            env=environment,
            timeout=300,
        )
        graph.snapshot.verify()
    from research_repo_tools.dependency_artifacts import verify_python_environment

    verify_python_environment(graph, build_directory=directory)
    return tuple(item.wheel for item in verified)
