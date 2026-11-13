"""Digest-pinned, platform-specific Python runtime graphs.

The expected digest is an independent trust input. This checks installed bytes;
it cannot authenticate the interpreter or code already imported at bootstrap.
"""

import io
import json
import os
import platform
import sys
import sysconfig
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from importlib.machinery import all_suffixes
from importlib.metadata import MetadataNotFound
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from research_repo_tools._dependency_probe import MAX_NATIVE_BYTES, distribution_inventory, inspect_distribution
from research_repo_tools.dependency_build_receipts import read_regular
from research_repo_tools.dependency_provenance import verify_wheel_payload
from research_repo_tools.evidence import _digest, _load_json, sha256, verify_sha256

__all__ = ["RuntimePolicy", "parse_runtime_policy", "runtime_requirements", "verify_executing_runtime", "verify_runtime"]

SCHEMA = "research-repo-tools/runtime-policy/v1"
MAX_POLICY_BYTES = 4 * 1024 * 1024
MAX_PACKAGES = 256
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024


def _context() -> dict[str, str]:
    return {
        "implementation": sys.implementation.name,
        "python": platform.python_version(),
        "platform": sysconfig.get_platform(),
        "abi": str(sysconfig.get_config_var("SOABI")),
    }


def _fields(value: Any, names: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError(f"runtime {label} has missing or unknown fields")
    return value


def _requirements(value: Any) -> tuple[Requirement, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_PACKAGES or any(not isinstance(item, str) for item in value):
        raise ValueError("runtime requires a bounded nonempty requirement list")
    result = tuple(Requirement(item) for item in value)
    if any(item.url is not None for item in result):
        raise ValueError("runtime requirements must use registry names, not URLs or development sources")
    return result


def _pypi_artifact(value: Any) -> None:
    from research_repo_tools.registry import pypi_destination

    if not isinstance(value, str) or urlsplit(pypi_destination(value)).netloc != "files.pythonhosted.org":
        raise ValueError("runtime artifacts must use the public PyPI file origin")


@dataclass(frozen=True, slots=True, init=False)
class RuntimePolicy:
    """Validated original bytes, pinned by a caller-supplied trusted digest."""

    payload: bytes

    def __init__(self) -> None:
        raise TypeError("use parse_runtime_policy with an independently trusted digest")

    @property
    def digest(self) -> str:
        return sha256(self.payload)


def parse_runtime_policy(payload: bytes, *, expected_sha256: str) -> RuntimePolicy:
    """Authenticate exact manifest bytes against a protected, external selection."""
    if not isinstance(payload, bytes) or len(payload) > MAX_POLICY_BYTES:
        raise ValueError("runtime policy exceeds its byte limit")
    verify_sha256(payload, expected_sha256)
    data = _fields(_load_json(payload, "runtime policy"), {"schema", "context", "requirements", "inputs", "packages"}, "policy")
    if data["schema"] != SCHEMA:
        raise ValueError("unsupported runtime policy schema")
    context = _fields(data["context"], {"implementation", "python", "platform", "abi"}, "context")
    if any(not isinstance(value, str) or not value for value in context.values()):
        raise ValueError("runtime context requires nonempty strings")
    _requirements(data["requirements"])
    inputs = _fields(data["inputs"], {"pyproject.toml", "uv.lock", "dependency-sources.toml"}, "inputs")
    for value in inputs.values():
        _digest(value, "runtime input")
    packages = data["packages"]
    if not isinstance(packages, list) or not 0 < len(packages) <= MAX_PACKAGES:
        raise ValueError("runtime policy requires a bounded nonempty package graph")
    names = set()
    total = 0
    for raw in packages:
        item = _fields(raw, {"name", "version", "filename", "sha256", "size", "provenance"}, "package")
        name, version, filename = item["name"], item["version"], item["filename"]
        if not isinstance(name, str) or canonicalize_name(name, validate=True) != name or name in names:
            raise ValueError("runtime package names must be canonical and distinct")
        names.add(name)
        if not isinstance(version, str) or str(Version(version)) != version:
            raise ValueError("runtime version must be canonical")
        if not isinstance(filename, str) or "/" in filename or "\\" in filename or ":" in filename:
            raise ValueError("runtime wheel requires a plain filename")
        wheel_name, wheel_version, _, _ = parse_wheel_filename(filename)
        if wheel_name != name or wheel_version != Version(version):
            raise ValueError("runtime wheel identity differs from its package")
        _digest(item["sha256"], "runtime wheel")
        if type(item["size"]) is not int or not 0 < item["size"] <= MAX_NATIVE_BYTES:
            raise ValueError("runtime wheel exceeds its byte limit")
        total += item["size"]
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("runtime wheels exceed the aggregate byte limit")
        provenance = item["provenance"]
        if not isinstance(provenance, dict):
            raise ValueError("runtime provenance must be an object")
        if provenance.get("kind") == "registry":
            _fields(provenance, {"kind", "url"}, "registry provenance")
            _pypi_artifact(provenance["url"])
        elif provenance.get("kind") == "source-build":
            _fields(provenance, {"kind", "source", "receipt_sha256", "build_log_sha256", "inputs"}, "build provenance")
            source = _fields(provenance["source"], {"url", "sha256", "size"}, "build source")
            _pypi_artifact(source["url"])
            _digest(source["sha256"], "runtime source")
            if type(source["size"]) is not int or source["size"] <= 0:
                raise ValueError("runtime source requires a positive size")
            for key in ("receipt_sha256", "build_log_sha256"):
                _digest(provenance[key], key)
            if provenance["inputs"] != inputs:
                raise ValueError("runtime native-build evidence is stale for the selected inputs")
        else:
            raise ValueError("runtime package requires registry or authenticated source-build provenance")
    result = object.__new__(RuntimePolicy)
    object.__setattr__(result, "payload", payload)
    return result


def runtime_requirements(policy: RuntimePolicy) -> bytes:
    """Exact offline wheel selection for uv pip sync --require-hashes."""
    data = json.loads(policy.payload)
    return "".join(f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n" for item in data["packages"]).encode("ascii")


def _layout(environment: Path) -> Path:
    if environment.is_symlink() or environment.is_junction():
        raise ValueError("runtime environment must not be a link")
    configuration = read_regular(environment / "pyvenv.cfg", 65536).decode("utf-8")
    fields: dict[str, str] = {}
    for line in configuration.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            key, value = key.strip().casefold(), value.strip()
            if key in fields:
                raise ValueError("runtime pyvenv.cfg contains duplicate settings")
            fields[key] = value
    if fields.get("include-system-site-packages", "").casefold() != "false":
        raise ValueError("runtime requires include-system-site-packages explicitly set to false")
    versions = [fields[key] for key in ("version_info", "version") if key in fields]
    base_home = Path(getattr(sys, "_base_executable", sys.executable)).resolve().parent
    home = Path(fields.get("home", ""))
    shared_interpreter = home.is_absolute() and home.is_dir() and home.samefile(base_home)
    # uv can retain a major.minor declaration for a managed interpreter whose
    # patch version changed. Only the already trusted running base can resolve
    # that ambiguity without executing a possibly hostile target interpreter.
    if not versions or any(
        Version(value) != Version(platform.python_version()) and not (value == f"{sys.version_info.major}.{sys.version_info.minor}" and shared_interpreter)
        for value in versions
    ):
        raise ValueError("runtime pyvenv.cfg Python version differs from the selected policy context")
    sites: list[Path] = [environment / "Lib/site-packages"] if os.name == "nt" else list((environment / "lib").glob("python*/site-packages"))
    sites = [site for site in sites if site.is_dir()]
    if len(sites) != 1 or not sites[0].resolve().is_relative_to(environment.resolve()):
        raise ValueError("runtime requires one contained native site-packages directory")
    for parent in (sites[0], *sites[0].parents):
        if parent == environment:
            break
        if parent.is_symlink() or parent.is_junction():
            raise ValueError("runtime site-packages must not traverse links")
    return sites[0]


def _inventory(environment: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    site = _layout(environment)
    count = 0
    distribution_count = 0
    present = []
    startup_modules = {module + suffix.casefold() for module in ("sitecustomize", "usercustomize") for suffix in all_suffixes()}

    def fail_walk(error: OSError) -> None:
        raise error

    for directory, directories, filenames in site.walk(on_error=fail_walk):
        for name in (*directories, *filenames):
            path = directory / name
            count += 1
            if count > 100000 or path.is_symlink() or path.is_junction():
                raise ValueError("runtime contains links or exceeds its file-count limit")
            if directory == site and name.casefold().endswith((".dist-info", ".egg-info")):
                distribution_count += 1
                if distribution_count > MAX_PACKAGES:
                    raise ValueError("runtime package inventory exceeds its bounds")
        if directory == site and any(name.casefold() in {"sitecustomize", "usercustomize"} for name in directories):
            raise ValueError("runtime policy does not permit Python startup hooks")
        for name in filenames:
            path = directory / name
            if not path.is_file():
                raise ValueError("runtime requires regular installed files")
            metadata_file = name.casefold() in {"metadata", "pkg-info", "record", "wheel", "direct_url.json", "sources.txt", "installed-files.txt"}
            if (
                metadata_file and directory.name.casefold().endswith((".dist-info", ".egg-info")) or name.casefold().endswith(".egg-info")
            ) and path.stat().st_size > MAX_POLICY_BYTES:
                raise ValueError("runtime distribution metadata exceeds its byte limit")
            present.append(path.relative_to(site).as_posix())
    # Bound and reject unsafe filesystem entries before importlib reads metadata
    # and RECORD into memory. The distribution probe is intentionally inert.
    try:
        inventory = distribution_inventory((site,))
    except MetadataNotFound as error:
        raise ValueError("runtime distribution metadata is missing") from error
    if not 0 < len(inventory) <= MAX_PACKAGES:
        raise ValueError("runtime package inventory exceeds its bounds")
    files = {path.as_posix() for dist in inventory.values() for path in dist.records if ".." not in path.parts}
    for relative in present:
        name = Path(relative).name.casefold()
        if relative not in files or name.endswith(".pyc"):
            raise ValueError(f"runtime contains an unexpected file or cached bytecode: {relative}; recreate without bytecode compilation")
        if name.endswith((".pth", ".start")) or "/" not in relative and name in startup_modules:
            raise ValueError("runtime policy does not permit Python startup hooks")
    observations = {}
    for name, dist in inventory.items():
        if dist.direct_url is not None:
            raise ValueError("runtime policy requires registry-style wheel installations without direct_url.json")
        observations[name] = inspect_distribution(dist, limit=MAX_NATIVE_BYTES, shared_files=frozenset(files))
    return inventory, observations


def _closure(requirements: list[str], metadata: dict[str, Any]) -> None:
    pending = list(_requirements(requirements))
    seen: set[tuple[str, str]] = set()
    selected = set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        if requirement.url is not None:
            raise ValueError("runtime dependencies cannot select direct sources")
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        if name not in metadata or Version(metadata[name]["Version"]) not in requirement.specifier:
            raise ValueError(f"runtime graph has a missing or incompatible dependency: {requirement}")
        selected.add(name)
        for extra in {"", *(canonicalize_name(extra) for extra in requirement.extras)}:
            if (name, extra) in seen:
                continue
            seen.add((name, extra))
            if extra and extra not in {canonicalize_name(value) for value in metadata[name].get_all("Provides-Extra", [])}:
                raise ValueError(f"runtime package {name} does not declare extra {extra}")
            for raw in metadata[name].get_all("Requires-Dist", []):
                dependency = Requirement(raw)
                if dependency.marker is None or dependency.marker.evaluate({"extra": extra}):
                    dependency.marker = None
                    pending.append(dependency)
    if selected != metadata.keys():
        raise ValueError("runtime graph contains unexpected dependencies outside the selected requirement closure")


def _wheel_metadata(payload: bytes, name: str, version: str) -> Any:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        paths = [path for path in archive.namelist() if len(Path(path).parts) == 2 and path.endswith(".dist-info/METADATA")]
        if len(paths) != 1 or archive.getinfo(paths[0]).file_size > MAX_POLICY_BYTES:
            raise ValueError("runtime wheel has missing or excessive metadata")
        metadata = BytesParser().parsebytes(archive.read(paths[0]))
    if canonicalize_name(metadata.get("Name", "")) != name or metadata.get("Version") != version:
        raise ValueError("runtime wheel metadata identity differs")
    if Version(platform.python_version()) not in SpecifierSet(metadata.get("Requires-Python", "")):
        raise ValueError("runtime wheel requires a different Python version")
    return metadata


def _native_evidence(item: dict[str, Any], artifacts: Path, inputs: dict[str, str]) -> None:
    provenance = item["provenance"]
    if provenance["kind"] != "source-build":
        return
    receipt = read_regular(artifacts / (provenance["receipt_sha256"] + ".receipt.json"), MAX_POLICY_BYTES)
    log = read_regular(artifacts / (provenance["build_log_sha256"] + ".log"), MAX_POLICY_BYTES)
    verify_sha256(receipt, provenance["receipt_sha256"])
    verify_sha256(log, provenance["build_log_sha256"])
    envelope = _fields(_load_json(receipt, "runtime build receipt"), {"payload", "mac"}, "build receipt")
    record = _fields(
        envelope["payload"], {"schema", "context", "package", "version", "source", "tools", "external", "build_log_sha256", "wheel"}, "build record"
    )
    source = provenance["source"]
    if (
        record["schema"] != "research-repo-tools/source-build/v1"
        or record["package"] != item["name"]
        or record["version"] != item["version"]
        or record["source"] != {"url": source["url"], "hash": "sha256:" + source["sha256"], "size": source["size"]}
        or not isinstance(record["context"], dict)
        or record["context"].get("inputs") != inputs
        or record["wheel"] != {"filename": item["filename"], "sha256": item["sha256"]}
        or record["build_log_sha256"] != provenance["build_log_sha256"]
        or not isinstance(record["tools"], dict)
        or not record["tools"]
        or not isinstance(record["external"], list)
    ):
        raise ValueError("runtime native-build evidence is stale or inconsistent with the approved policy")


def verify_runtime(policy: RuntimePolicy, *, environment: Path, artifacts: Path) -> tuple[str, ...]:
    """Verify an inert external venv against its independently selected policy.

    Artifact authentication is retained at manifest approval time. This does not
    replace fresh release dependency, native validation or registry checks.
    """
    data = json.loads(policy.payload)
    if data["context"] != _context():
        raise ValueError("runtime policy belongs to a different Python/platform")
    configuration = read_regular(environment / "pyvenv.cfg", 65536)
    _, observed = _inventory(environment)
    packages: dict[str, Any] = {item["name"]: item for item in data["packages"]}
    if observed.keys() != packages.keys():
        raise ValueError("runtime installed graph has missing or unexpected distributions")
    metadata = {}
    for name, item in packages.items():
        if observed[name]["version"] != item["version"]:
            raise ValueError(f"{name}: selected runtime version drift")
        payload = read_regular(artifacts / item["filename"], item["size"])
        if len(payload) != item["size"]:
            raise ValueError("runtime wheel size differs")
        verify_sha256(payload, item["sha256"])
        if not parse_wheel_filename(item["filename"])[3].intersection(sys_tags()):
            raise ValueError("runtime wheel is incompatible with the executing Python/platform")
        _native_evidence(item, artifacts, data["inputs"])
        verify_wheel_payload(name, payload, observed[name], scripts=environment / ("Scripts" if os.name == "nt" else "bin"), limit=MAX_NATIVE_BYTES)
        metadata[name] = _wheel_metadata(payload, name, item["version"])
    _closure(data["requirements"], metadata)
    if _inventory(environment)[1] != observed or read_regular(environment / "pyvenv.cfg", 65536) != configuration:
        raise ValueError("runtime installed bytes changed during verification")
    return tuple(sorted(packages))


def verify_executing_runtime(policy: RuntimePolicy, *, artifacts: Path) -> tuple[str, ...]:
    """Check the actual isolated installed runtime before invoking a writer."""
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise ValueError("runtime policy verification requires Python -I -B")
    environment = Path(sys.prefix)
    site = _layout(environment).resolve()
    if not Path(__file__).resolve().is_relative_to(site):
        raise ValueError("runtime checker must execute from the selected installed environment")
    allowed = {site, Path(sysconfig.get_path("stdlib")).resolve(), Path(sysconfig.get_path("platstdlib")).resolve()}
    if dynamic_library := sysconfig.get_config_var("DESTSHARED"):
        allowed.add(Path(dynamic_library).resolve())
    native_library = Path(sys.base_prefix) / "DLLs"
    abi_thread = "t" if sysconfig.get_config_var("Py_GIL_DISABLED") and os.name != "nt" else ""
    standard_zip = (
        Path(sys.base_prefix) if os.name == "nt" else Path(sys.base_prefix) / sys.platlibdir
    ) / f"python{sys.version_info.major}{sys.version_info.minor}{abi_thread}.zip"
    # CPython's Windows getpath also inserts the base executable directory.
    # These exact interpreter roots are bootstrap trust, not package locations.
    interpreter_roots = {Path(sys.base_prefix).resolve(), Path(sys.executable).parent.resolve()} if os.name == "nt" else set()
    for entry in sys.path:
        path = Path(entry).resolve()
        if path not in {*allowed, *interpreter_roots, native_library.resolve(), standard_zip.resolve()}:
            raise ValueError("runtime import path contains an external package location")
    return verify_runtime(policy, environment=environment, artifacts=artifacts)
