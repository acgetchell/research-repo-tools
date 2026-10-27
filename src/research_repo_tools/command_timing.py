"""Exploratory wall-clock command timing, separate from Criterion evidence."""

import base64
import hashlib
import math
import os
import platform
import re
import statistics
import sys
import time
import tomllib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from research_repo_tools import __version__
from research_repo_tools.evidence import _digest, _load_json, _object, _string, deterministic_json
from research_repo_tools.files import _validate_distinct_paths, replace_many
from research_repo_tools.host_metadata import capture_host, parse_host, serialize_host
from research_repo_tools.process import _windows_environment, resolve_executable, run_command_bytes, run_command_live
from research_repo_tools.publication import _inventory, _path
from research_repo_tools.publication_config import _table
from research_repo_tools.release_policy import _relative_path
from research_repo_tools.source_exports import _names as _source_names

__all__ = ["COMMAND_TIMING_SCHEMA", "measure_commands", "publish_command_timing", "summarize_commands", "validate_command_timing"]

COMMAND_TIMING_SCHEMA = "research-repo-tools/exploratory-command-timing/v1"
_POLICY = {"schema", "sources", "cases", "preflight", "warmups", "repeats", "timeout", "environment", "probes"}
_EXECUTION = {
    "order": "sorted-case-then-warmups-then-samples",
    "stdin": "inherited",
    "output": "inherited",
    "shell": False,
    "cleanup": "direct-child",
    "environment": "inherited-with-overlay",
    "elapsed": "launch-and-wait-including-runner-overhead",
}


def _integer(value: object, context: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{context} must be an integer in [{low}, {high}]")
    return value


def _deadline(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 86400 or not math.isfinite(value):
        raise ValueError("command timing timeout must be finite seconds in (0, 86400]")
    return float(value)


def _argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("command requires a nonempty literal argv array of strings without NUL")
    command = []
    for item in value:
        if not isinstance(item, str) or "\0" in item:
            raise ValueError("command requires a nonempty literal argv array of strings without NUL")
        item.encode("utf-8")
        command.append(item)
    if not command[0]:
        raise ValueError("command executable must not be empty")
    return tuple(command)


def _identifier(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) is None:
        raise ValueError("case/probe ID must be 1–128 ASCII letters, digits, dots, underscores or hyphens, starting with a letter or digit")
    return value


@dataclass(frozen=True, slots=True)
class _Plan:
    sources: tuple[str, ...]
    cases: tuple[tuple[str, tuple[str, ...]], ...]
    preflight: tuple[tuple[str, ...], ...]
    warmups: int
    repeats: int
    timeout: float
    environment: tuple[tuple[str, str], ...]
    probes: tuple[tuple[str, tuple[str, ...]], ...]

    def policy(self) -> dict[str, object]:
        return dict(
            schema=1,
            sources=self.sources,
            cases=[dict(id=name, command=command) for name, command in self.cases],
            preflight=self.preflight,
            warmups=self.warmups,
            repeats=self.repeats,
            timeout=self.timeout,
            environment=dict(self.environment),
            probes=dict(self.probes),
        )


def _plan(value: object) -> _Plan:
    raw = _table(value, "command timing", {"schema", "sources", "cases"}, _POLICY - {"schema", "sources", "cases"})
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("command timing schema must be integer 1")
    sources = _argv(raw["sources"])
    if any(not name for name in sources) or len(set(sources)) != len(sources):
        raise ValueError("sources must be unique nonempty root-relative paths or globs")
    for pattern in sources:
        _relative_path(pattern, patterns=True)
    cases = raw["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 256:
        raise ValueError("command timing requires 1–256 cases")
    selected = []
    for value in cases:
        item = _table(value, "timing case", {"id", "command"})
        selected.append((_identifier(item["id"]), _argv(item["command"])))
    if len(dict(selected)) != len(selected):
        raise ValueError("command timing case IDs must be unique")
    preflight = raw.get("preflight", [])
    if not isinstance(preflight, list) or len(preflight) > 64:
        raise ValueError("preflight must contain at most 64 commands")
    environment = _object(raw.get("environment", {}), "timing environment")
    for name, item in environment.items():
        if not name or "=" in name or "\0" in name or not isinstance(item, str) or "\0" in item:
            raise ValueError("environment requires nonempty names without '=' or NUL and string values without NUL")
    if len({name.upper() for name in environment}) != len(environment):
        raise ValueError("environment names must be distinct on Windows too")
    probes = _object(raw.get("probes", {}), "timing probes")
    if len(probes) > 64:
        raise ValueError("at most 64 tool probes are supported")
    return _Plan(
        tuple(sorted(sources)),
        tuple(sorted(selected)),
        tuple(_argv(item) for item in preflight),
        _integer(raw.get("warmups", 1), "warmups", 0, 100),
        _integer(raw.get("repeats", 5), "repeats", 1, 1000),
        _deadline(raw.get("timeout", 300)),
        tuple(sorted((name, str(item)) for name, item in environment.items())),
        tuple(sorted((_identifier(name), _argv(command)) for name, command in probes.items())),
    )


def _file(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": digest, "size": path.stat().st_size}


def _sources(root: Path, plan: _Plan, configuration: str) -> dict[str, object]:
    names = sorted({configuration, *_inventory(root, plan.sources)})
    _source_names(None, tuple(names))
    _validate_distinct_paths(tuple(_path(root, name) for name in names))
    return {name: _file(_path(root, name)) for name in names}


def _command(root: Path, argv: tuple[str, ...], env: Mapping[str, str]) -> dict[str, object]:
    executable = resolve_executable(argv[0], cwd=root, env=env)
    return {"argv": argv, "executable": str(executable), **_file(executable)}


def _acquire(root: Path, configuration: str) -> tuple[bytes, Callable[[], None]]:
    root = root.resolve(strict=True)
    declaration = _path(root, configuration).read_bytes()
    plan = _plan(tomllib.loads(declaration.decode("utf-8")))
    sources = _sources(root, plan, configuration)
    overlay = dict(plan.environment)
    env = {**os.environ, **overlay} if sys.platform != "win32" else {**_windows_environment(os.environ), **_windows_environment(overlay)}
    receipts: dict[str, object] = {}
    observations = []

    def execute(key: str, argv: tuple[str, ...], stage: str, index: int) -> None:
        receipt = _command(root, argv, env)
        if key in receipts and receipts[key] != receipt:
            raise ValueError(f"timed executable changed during acquisition: {key}")
        receipts[key] = receipt
        started = time.perf_counter_ns()
        result = run_command_live(Path(str(receipt["executable"])), argv[1:], cwd=root, env=env, timeout=plan.timeout)
        elapsed = time.perf_counter_ns() - started
        _integer(elapsed, "elapsed_ns", 1, 10**30)
        observations.append(dict(command=key, stage=stage, index=index, elapsed_ns=elapsed, returncode=result.returncode, status="success"))

    for index, argv in enumerate(plan.preflight):
        execute(f"preflight/{index}", argv, "preflight", index)
    # Built executables are resolved after preflight. No guessed paths or stale
    # timing files are read. Every duration below is a fresh direct-child run.
    tools = {}
    for name, argv in plan.probes:
        receipt = _command(root, argv, env)
        result = run_command_bytes(Path(str(receipt["executable"])), argv[1:], cwd=root, env=env, timeout=plan.timeout, input=b"")
        receipts[f"probe/{name}"] = receipt
        tools[name] = dict(
            stdout_base64=base64.b64encode(result.stdout).decode("ascii"),
            stderr_base64=base64.b64encode(result.stderr).decode("ascii"),
            returncode=result.returncode,
            status="success",
        )
    for name, argv in plan.cases:
        for stage, count in (("warmup", plan.warmups), ("sample", plan.repeats)):
            for index in range(count):
                execute(f"case/{name}", argv, stage, index)

    def verify() -> None:
        if _path(root, configuration).read_bytes() != declaration or _sources(root, plan, configuration) != sources:
            raise ValueError("command timing source/declaration changed during acquisition or publication")
        for key, value in receipts.items():
            receipt = _object(value, "command receipt")
            if {**receipt, **_file(Path(str(receipt["executable"])))} != receipt:
                raise ValueError(f"command executable changed before publication: {key}")

    verify()
    clock = time.get_clock_info("perf_counter")
    payload = deterministic_json(
        dict(
            schema=COMMAND_TIMING_SCHEMA,
            purpose="exploratory-only",
            unit="ns",
            run_id=str(uuid.uuid4()),
            completed_at=datetime.now(UTC).isoformat(),
            root=str(root),
            configuration=configuration,
            policy=plan.policy(),
            execution=_EXECUTION,
            source=sources,
            commands=receipts,
            observations=observations,
            tools=tools,
            host=_load_json(serialize_host(capture_host(root)), "host"),
            timer=dict(
                name="time.perf_counter_ns",
                implementation=clock.implementation,
                monotonic=clock.monotonic,
                adjustable=clock.adjustable,
                resolution_seconds=clock.resolution,
            ),
            runner=dict(
                package_version=__version__,
                python=sys.version,
                executable=sys.executable,
                implementation=platform.python_implementation(),
                platform=sys.platform,
            ),
            environment_sha256=hashlib.sha256(deterministic_json(env)).hexdigest(),
        )
    )
    validate_command_timing(payload)
    verify()
    return payload, verify


def measure_commands(root: Path, configuration: str) -> bytes:
    """Run trusted schema-1 commands and return a complete exploratory JSON report.

    No report file is read or written. Failed commands/timeouts propagate their
    native exceptions; changed inputs and invalid observations raise ValueError.
    Only declared source files are identified; this is not a release attestation.
    """
    return _acquire(root, configuration)[0]


def publish_command_timing(root: Path, configuration: str, output: str) -> bytes:
    """Acquire fresh observations and atomically publish to an absent output.

    Existing reports are rejected before commands run. Source inputs and command
    identities are rechecked after staging; exclusive writer control is required.
    """
    root = root.resolve(strict=True)
    target = _path(root, output)
    if target.exists():
        raise ValueError("timing output already exists; choose a new path to preserve observations")
    plan = _plan(tomllib.loads(_path(root, configuration).read_text(encoding="utf-8")))
    sources = _sources(root, plan, configuration)
    _validate_distinct_paths((target, *(_path(root, name) for name in sources)))
    payload, verify = _acquire(root, configuration)

    def verify_publication() -> None:
        if _path(root, output).exists():
            raise ValueError("timing output appeared during acquisition; refusing to overwrite it")
        _validate_distinct_paths((target, *(_path(root, name) for name in sources)))
        verify()

    replace_many({target: payload}, verify=verify_publication)
    return payload


def _report(payload: bytes) -> tuple[_Plan, list[dict[str, object]]]:
    raw = _table(
        _load_json(payload, "command timing report"),
        "command timing report",
        {
            "schema",
            "purpose",
            "unit",
            "run_id",
            "completed_at",
            "root",
            "configuration",
            "policy",
            "execution",
            "source",
            "commands",
            "observations",
            "tools",
            "host",
            "timer",
            "runner",
            "environment_sha256",
        },
    )
    if (raw["schema"], raw["purpose"], raw["unit"]) != (COMMAND_TIMING_SCHEMA, "exploratory-only", "ns") or deterministic_json(
        raw["execution"]
    ) != deterministic_json(_EXECUTION):
        raise ValueError("unsupported exploratory command timing schema, units or execution policy")
    if str(uuid.UUID(_string(raw["run_id"], "run ID"))) != raw["run_id"]:
        raise ValueError("invalid timing run ID")
    if datetime.fromisoformat(_string(raw["completed_at"], "completed_at")).utcoffset() != UTC.utcoffset(None):
        raise ValueError("completed_at must be UTC")
    _absolute_path(raw["root"], "root")
    runner = _table(raw["runner"], "runner", {"package_version", "python", "executable", "implementation", "platform"})
    for key, value in runner.items():
        _string(value, f"runner {key}")
    _absolute_path(runner["executable"], "runner executable")
    if runner["platform"] not in {"darwin", "linux", "win32"}:
        raise ValueError("command timing requires a supported native runner platform")
    source_path = PureWindowsPath if runner["platform"] == "win32" else PurePosixPath
    plan = _plan(_table(raw["policy"], "recorded timing policy", _POLICY))
    source = _object(raw["source"], "source")
    configuration = _string(raw["configuration"], "configuration")
    if configuration not in source or len(source) < 1:
        raise ValueError("source inventory must include configuration")
    # Retained reports must be readable on another host after source cleanup.
    # Validate portable spelling without resolving paths on the reader's host.
    _source_names(None, tuple(source))
    for pattern in plan.sources:
        if not any(source_path(name).full_match(pattern) for name in source):
            raise ValueError("recorded source inventory omits a declared pattern")
    if any(name != configuration and not any(source_path(name).full_match(pattern) for pattern in plan.sources) for name in source):
        raise ValueError("recorded source inventory has undeclared files")
    for name, value in source.items():
        from research_repo_tools.publication import _publication_name

        _publication_name(name)
        item = _table(value, "source file", {"sha256", "size"})
        _digest(item["sha256"], "source digest")
        _integer(item["size"], "source size", 0, 10**30)
    expected = {f"preflight/{index}": argv for index, argv in enumerate(plan.preflight)}
    expected.update({f"case/{name}": argv for name, argv in plan.cases})
    expected.update({f"probe/{name}": argv for name, argv in plan.probes})
    commands = _object(raw["commands"], "commands", set(expected))
    for key, value in commands.items():
        item = _table(value, "command receipt", {"argv", "executable", "sha256", "size"})
        if _argv(item["argv"]) != expected[key]:
            raise ValueError("recorded argv differs from declared command")
        _absolute_path(item["executable"], "resolved executable")
        _digest(item["sha256"], "executable digest")
        _integer(item["size"], "executable size", 0, 10**30)
    tools = _object(raw["tools"], "tools", {name for name, _ in plan.probes})
    for value in tools.values():
        item = _table(value, "tool probe", {"stdout_base64", "stderr_base64", "returncode", "status"})
        _success(item)
        for key in ("stdout_base64", "stderr_base64"):
            encoded = item[key]
            if not isinstance(encoded, str):
                raise ValueError("probe bytes must be base64 strings")
            base64.b64decode(encoded, validate=True)
    parse_host(deterministic_json(raw["host"]))
    timer = _table(raw["timer"], "timer", {"name", "implementation", "monotonic", "adjustable", "resolution_seconds"})
    if timer["name"] != "time.perf_counter_ns" or timer["monotonic"] is not True or timer["adjustable"] is not False:
        raise ValueError("timing requires the monotonic perf_counter_ns clock")
    _string(timer["implementation"], "clock implementation")
    _deadline(timer["resolution_seconds"])
    _digest(raw["environment_sha256"], "effective environment digest")
    sequence = [(f"preflight/{index}", "preflight", index) for index in range(len(plan.preflight))]
    sequence.extend(
        (f"case/{name}", stage, index)
        for name, _ in plan.cases
        for stage, count in (("warmup", plan.warmups), ("sample", plan.repeats))
        for index in range(count)
    )
    values = raw["observations"]
    if not isinstance(values, list) or len(values) != len(sequence):
        raise ValueError("timing observations are incomplete")
    observations = []
    for value, identity in zip(values, sequence, strict=True):
        item = _table(value, "observation", {"command", "stage", "index", "elapsed_ns", "returncode", "status"})
        if (item["command"], item["stage"], item["index"]) != identity or type(item["index"]) is not int:
            raise ValueError("timing observations have wrong order, case, stage or index")
        _integer(item["elapsed_ns"], "elapsed_ns", 1, 10**30)
        _success(item)
        observations.append(item)
    return plan, observations


def _success(item: dict[str, object]) -> None:
    if type(item["returncode"]) is not int or item["returncode"] != 0 or item["status"] != "success":
        raise ValueError("command timing requires successful preflights, probes, warmups and samples")


def _absolute_path(value: object, context: str) -> None:
    text = _string(value, context)
    if not (PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute()):
        raise ValueError(f"{context} must be a retained absolute native path")


def validate_command_timing(payload: bytes) -> None:
    """Reject unsupported, malformed, nonfinite, failed or incomplete reports.

    Offline structural validation does not establish current-source freshness,
    authentic execution, scientific validity or cross-host comparability.
    """
    _report(payload)


def summarize_commands(payload: bytes) -> str:
    """Render sorted case counts and elapsed seconds from every retained sample."""
    plan, observations = _report(payload)
    lines = ["Exploratory command timing (wall seconds; no comparability claim)", "case\tsamples\tmin_s\tmedian_s\tmax_s"]
    for name, _ in plan.cases:
        values = [
            _integer(item["elapsed_ns"], "elapsed_ns", 1, 10**30) for item in observations if item["command"] == f"case/{name}" and item["stage"] == "sample"
        ]
        lines.append(f"{name}\t{len(values)}\t{min(values) / 1e9:.9f}\t{statistics.median(values) / 1e9:.9f}\t{max(values) / 1e9:.9f}")
    return "\n".join(lines) + "\n"
