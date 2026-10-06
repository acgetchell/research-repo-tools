"""Versioned host observations and explicit profiling declarations.

Missing observations stay None. Reading retained metadata never probes this host.
Compatibility and benchmark eligibility remain consumer decisions.
"""

import json
import math
import os
import platform
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path

from research_repo_tools.evidence import _load_json, _object, _string, deterministic_json
from research_repo_tools.process import ExecutableNotFoundError, cpu_description, run_command
from research_repo_tools.publication import _path
from research_repo_tools.publication_config import _table

__all__ = ["HOST_SCHEMA", "HostMetadata", "capture_host", "capture_profile", "parse_host", "serialize_host"]

HOST_SCHEMA = "research-repo-tools/host/v1"
_PROBE_ERRORS = (OSError, subprocess.SubprocessError, ExecutableNotFoundError, UnicodeError, ValueError)


def _count(value: object) -> int | None:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if type(value) is int and value > 0 else None


@dataclass(frozen=True, slots=True)
class HostMetadata:
    os: str | None
    architecture: str | None
    cpu: str | None
    physical_cores: int | None
    logical_threads: int | None
    memory_bytes: int | None
    tools: tuple[tuple[str, str | None], ...] = ()

    def __post_init__(self) -> None:
        for name in ("os", "architecture", "cpu"):
            value = getattr(self, name)
            if value is not None:
                _string(value, name)
        for name in ("physical_cores", "logical_threads", "memory_bytes"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{name} must be a positive integer or null")
        tools = tuple(sorted((_string(name, "tool name"), value) for name, value in self.tools))
        if len(dict(tools)) != len(tools):
            raise ValueError("tool names must be unique")
        for name, value in tools:
            if value is not None and (not isinstance(value, str) or not value.strip() or "\0" in value):
                raise ValueError(f"tool {name} requires nonempty text or null")
        object.__setattr__(self, "tools", tools)


def serialize_host(host: HostMetadata) -> bytes:
    return deterministic_json({"schema": HOST_SCHEMA, **asdict(host), "tools": dict(host.tools)})


def parse_host(payload: bytes) -> HostMetadata:
    raw = _object(
        _load_json(payload, "host metadata"),
        "host metadata",
        {"schema", "os", "architecture", "cpu", "physical_cores", "logical_threads", "memory_bytes", "tools"},
    )
    if raw.pop("schema") != HOST_SCHEMA:
        raise ValueError("unsupported host metadata schema")

    # Constructors enforce nullable types too; do not coerce retained observations.
    def text(value: object) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("host text must be a string or null")
        return value

    def count(value: object) -> int | None:
        if value is not None and type(value) is not int:
            raise ValueError("host count must be an integer or null")
        return value

    return HostMetadata(
        text(raw["os"]),
        text(raw["architecture"]),
        text(raw["cpu"]),
        count(raw["physical_cores"]),
        count(raw["logical_threads"]),
        count(raw["memory_bytes"]),
        tuple((key, text(value)) for key, value in _object(raw["tools"], "tools").items()),
    )


def _probe(command: tuple[str, ...], root: Path, env: Mapping[str, str], timeout: int) -> str | None:
    try:
        return run_command(command[0], command[1:], cwd=root, env=env, timeout=timeout, input="").stdout.strip() or None
    except _PROBE_ERRORS:
        return None


def _linux_counts(cpuinfo: str, meminfo: str) -> tuple[int | None, int | None]:
    cores = set()
    complete = True
    for block in cpuinfo.strip().split("\n\n"):
        fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        fields = {key.strip(): value.strip() for key, value in fields.items()}
        if "processor" not in fields:
            continue
        if not fields.get("physical id") or not fields.get("core id"):
            complete = False
        else:
            cores.add((fields["physical id"], fields["core id"]))
    memory = None
    for line in meminfo.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "MemTotal:" and parts[2] == "kB" and (count := _count(parts[1])):
            memory = count * 1024
    return (len(cores) if cores and complete else None), memory


def capture_host(root: Path, *, probes: tuple[tuple[str, tuple[str, ...]], ...] = (), env: Mapping[str, str] | None = None, timeout: int = 30) -> HostMetadata:
    """Observe system-wide counts and configured versions using bounded processes.

    Counts describe the host, not process affinity, container quotas or available RAM.
    Tool failures and unavailable OS facilities produce unknown values independently.
    """
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("probe timeout must be a positive integer")
    if len(dict(probes)) != len(probes):
        raise ValueError("probe names must be unique")
    for name, command in probes:
        _string(name, "probe name")
        if not command or isinstance(command, str) or any(not isinstance(part, str) or not part or "\0" in part for part in command):
            raise ValueError("probe commands require a nonempty argument vector")
    environment = {**(os.environ if env is None else env), "LC_ALL": "C", "LANG": "C"}
    root = root.resolve(strict=True)
    cores, threads, memory = _hardware_counts(platform.system(), root, environment, timeout)
    try:
        cpu = cpu_description()
    except _PROBE_ERRORS:
        cpu = "unavailable"
    return HostMetadata(
        platform.platform().strip() or None,
        platform.machine().strip() or None,
        None if cpu == "unavailable" else " ".join(cpu.split()),
        cores,
        threads,
        memory,
        tuple((name, _probe(command, root, environment, timeout)) for name, command in probes),
    )


def _hardware_counts(system: str, root: Path, environment: Mapping[str, str], timeout: int) -> tuple[int | None, int | None, int | None]:
    cores = memory = None
    threads = _count(os.cpu_count())
    if system == "Darwin":
        cores = _count(_probe(("sysctl", "-n", "hw.physicalcpu"), root, environment, timeout))
        threads = _count(_probe(("sysctl", "-n", "hw.logicalcpu"), root, environment, timeout)) or threads
        memory = _count(_probe(("sysctl", "-n", "hw.memsize"), root, environment, timeout))
    elif system == "Linux":

        def read(name: str) -> str:
            try:
                return Path(name).read_text(encoding="utf-8")
            except OSError, UnicodeError:
                return ""

        cores, memory = _linux_counts(read("/proc/cpuinfo"), read("/proc/meminfo"))
    elif system == "Windows":
        script = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
            "$ErrorActionPreference='Stop'; $c=@(Get-CimInstance Win32_Processor); "
            "@{cores=[long]($c | Measure-Object NumberOfCores -Sum).Sum; "
            "threads=[long]($c | Measure-Object NumberOfLogicalProcessors -Sum).Sum; "
            "memory=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory} | ConvertTo-Json -Compress"
        )
        for shell in ("pwsh", "powershell"):
            output = _probe((shell, "-NoProfile", "-NonInteractive", "-Command", script), root, environment, timeout)
            try:
                data = _object(_load_json((output or "null").encode(), "Windows hardware"), "Windows hardware")
                cores, memory = _count(data.get("cores")), _count(data.get("memory"))
                threads = _count(data.get("threads")) or threads
                break
            except ValueError:
                continue
    return cores, threads, memory


def _toml_json(value: object) -> object:
    """Project native TOML scalars into JSON; source text remains authoritative."""
    if isinstance(value, dict):
        return {key: _toml_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_toml_json(item) for item in value]
    if isinstance(value, datetime | date | time):
        return {"toml_type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"toml_type": "float", "value": str(value)}
    return value


def capture_profile(root: Path, configuration: str) -> bytes:
    """Capture schema-1 profiling TOML into versioned JSON; no output side effects.

    Native Rust/Cargo files are parsed as TOML and retained as declarations. They
    are not resolved build settings or measured versions. Context is explicit.
    """
    root = root.resolve(strict=True)
    raw = _table(
        tomllib.loads(_path(root, configuration).read_text(encoding="utf-8")),
        "profile",
        {"schema"},
        {"context", "probes", "rust-toolchain", "cargo-manifest", "measurement", "release"},
    )
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("profiling configuration schema must be integer 1")
    if ("measurement" in raw) != ("release" in raw):
        raise ValueError("profiling source capture requires both measurement configuration and release label")
    context = _object(raw.get("context", {}), "profiling context")
    for name, value in context.items():
        _string(name, "context key")
        _string(value, "context value")
    probes = []
    for name, command in _object(raw.get("probes", {"rustc": ["rustc", "-vV"], "cargo": ["cargo", "--version"]}), "probes").items():
        if not isinstance(command, list):
            raise ValueError("profiling probes require argument arrays")
        probes.append((name, tuple(_string(arg, "probe argument") for arg in command)))
    declarations = {}
    for key in ("rust-toolchain", "cargo-manifest"):
        if key in raw:
            name = _string(raw[key], key)
            text = _path(root, name).read_bytes().decode("utf-8")
            declarations[key] = {"path": name, "text": text, "toml": _toml_json(tomllib.loads(text))}
    source = None
    if "measurement" in raw:
        from research_repo_tools.common_measurement import CommonHarnessPlan
        from research_repo_tools.measurement import capture_provenance, load_measurement

        selected = load_measurement(root, _string(raw["measurement"], "measurement"))
        settings = selected.measurement if isinstance(selected, CommonHarnessPlan) else selected
        provenance = capture_provenance(root, settings, _string(raw["release"], "release"), mode="profiling")
        source = {**asdict(provenance), "context": dict(provenance.context)}
    return deterministic_json(
        {
            "schema": "research-repo-tools/profile/v1",
            "context": context,
            "declarations": declarations,
            "source": source,
            "host": json.loads(serialize_host(capture_host(root, probes=tuple(probes)))),
        }
    )
