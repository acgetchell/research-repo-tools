"""Paired warm workflow measurements; no Git mutations or global lazy imports.

Use `just bench-python-workflows --help`. Snapshots supply importable src trees;
interpreters must already contain the same locked dependencies and metadata.
Historical sources can be measured on 3.14; this release requires 3.15.
"""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _source_modules(source: Path) -> Iterator[Path]:
    """Enumerate Python inputs without suppressing directory traversal failures."""

    def walk_error(error: OSError) -> None:
        """Propagate the path and reason from the failed directory scan."""
        raise error

    for directory, directories, names in source.walk(on_error=walk_error, follow_symlinks=True):
        for name in directories:
            child = directory / name
            if child.is_symlink() or child.is_junction():
                target = child.resolve(strict=True)
                if any(parent.resolve(strict=True) == target for parent in (directory, *directory.parents)):
                    raise ValueError(f"directory cycle in measured Python source: {child}")
        for name in names:
            path = directory / name
            if path.suffix in {".py", ".pyi"}:
                if not path.is_file():
                    raise ValueError(f"measured Python source must be a regular file: {path}")
                yield path


def source_digest(source: Path) -> str:
    """Hash Python module paths and contents to bind timings to the measured implementation."""
    entries = [(path.relative_to(source).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()) for path in _source_modules(source)]
    # Preserve POSIX component ordering for the v1 digest on every host.
    entries.sort(key=lambda entry: entry[0].split("/"))
    return hashlib.sha256(json.dumps(entries, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def verify_inputs(sources: dict[Path, str], script: Path, script_digest: str) -> None:
    """Reject changes to the timing script or selected sources during acquisition."""
    for source, expected in sources.items():
        if source_digest(source) != expected:
            raise ValueError(f"measured source changed during the experiment: {source}")
    if hashlib.sha256(script.read_bytes()).hexdigest() != script_digest:
        raise ValueError(f"measurement harness changed during the experiment: {script}")


def execute(command: list[str], *, env: dict[str, str], label: str, cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a labeled timed subprocess and surface failures with the operation's label."""
    try:
        # Match Python producers to the UTF-8 capture, including historical
        # 3.14 children under legacy Windows code pages. Native tools ignore
        # these Python-only stream settings.
        child_env = {**env, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(command, env=child_env, cwd=cwd, check=False, capture_output=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired as error:
        detail = "\n".join(
            f"{name}:\n{stream.decode('utf-8', errors='replace') if isinstance(stream, bytes) else stream or ''}"
            for name, stream in (("stdout", error.stdout), ("stderr", error.stderr))
        )
        raise RuntimeError(f"{label} timed out after {timeout} seconds: {command!r}\n{detail}") from error
    except OSError as error:
        raise RuntimeError(f"{label} could not launch: {command!r}: {error}") from error
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}: {command!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def write_output(output: Path, document: dict[str, object]) -> None:
    """Publish complete UTF-8/LF evidence through the shared atomic writer."""
    # Only the current controller publishes output. Historical workers do not
    # import this release's implementation or its newer Python syntax.
    from research_repo_tools.files import replace_many

    payload = (json.dumps(document, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if not output.parent.is_dir():
        raise FileNotFoundError(f"measurement output parent does not exist: {output.parent}")
    replace_many({output: payload})


def check_output(output: Path, sources: list[Path], executables: list[Path], script: Path) -> None:
    """Reject destinations that would change the experiment's inputs or follow a leaf link."""
    if output.is_symlink() or output.is_junction() or not output.parent.is_dir() or (output.exists() and not output.is_file()):
        raise ValueError(f"measurement output must be a regular file in an existing directory: {output}")
    target = output.resolve()
    if target.suffix.lower() in {".py", ".pyi"} and any(target.is_relative_to(source) for source in sources):
        raise ValueError(f"measurement output overlaps measured source: {output}")
    if target.exists():
        if any(target.samefile(path) for path in (script, *executables)):
            raise ValueError(f"measurement output overlaps the harness or an executable: {output}")
        if any(path.samefile(target) for source in sources for path in _source_modules(source)):
            raise ValueError(f"measurement output overlaps measured source: {output}")


def _probe_record(output: str, context: str) -> dict[str, Any]:
    """Read the worker's trailing JSON record after any streamed validator output."""
    lines = output.rstrip().splitlines()
    if not lines:
        raise ValueError(f"{context} returned no result")
    try:
        record = json.loads(lines[-1])
    except ValueError as error:
        raise ValueError(f"{context} returned invalid JSON") from error
    if not isinstance(record, dict):
        raise ValueError(f"{context} must return a JSON object")
    return record


def check_origin(source: Path) -> None:
    """Require the benchmark worker to import research-repo-tools from the selected sources."""
    import research_repo_tools

    if Path(research_repo_tools.__file__).resolve() != (source / "research_repo_tools/__init__.py").resolve():
        raise ValueError(f"worker imported a different research_repo_tools source: {research_repo_tools.__file__}; expected {source}")


def worker(mode: str, root: Path, inventory: Path) -> None:
    """Measure the selected Python workflow using the frozen file inventory."""
    from unittest.mock import patch

    from research_repo_tools import config, python_checks, selection

    names = tuple(json.loads(inventory.read_text(encoding="utf-8")))
    payload = b"".join(os.fsencode(name) + b"\0" for name in names)
    resolutions = 0
    invocations = 0
    native_resolve = Path.resolve
    native_run = python_checks.run_command_live
    native_select = python_checks.select_files
    selected = ()
    executables: set[str] = set()

    def resolve(path, *args, **kwargs):
        nonlocal resolutions
        resolutions += 1
        return native_resolve(path, *args, **kwargs)

    def run(*args, **kwargs):
        nonlocal invocations
        invocations += 1
        executables.add(str(args[0]))
        return native_run(*args, **kwargs)

    def select(*args, **kwargs):
        nonlocal selected
        selected = native_select(*args, **kwargs)
        return selected

    with patch.object(selection, "run_git_bytes", return_value=subprocess.CompletedProcess([], 0, payload, b"")):
        if mode in {"selection", "selection-warm"}:
            iterations = 20 if mode == "selection-warm" else 1
            if mode == "selection-warm":
                for _ in range(5):
                    selection.select_files(root, include=("*.py", "*.pyi"))
            start = time.perf_counter()
            for _ in range(iterations):
                selected = selection.select_files(root, include=("*.py", "*.pyi"))
            elapsed = (time.perf_counter() - start) / iterations
            with patch.object(Path, "resolve", resolve):
                counted = selection.select_files(root, include=("*.py", "*.pyi"))
            if counted != selected:
                raise ValueError("selection changed between timing and resolution counting")
        else:
            with patch.object(python_checks, "run_command_live", run), patch.object(python_checks, "select_files", select):
                start = time.perf_counter()
                status = python_checks.run(config.load(root=root), "check")
                elapsed = time.perf_counter() - start
            if status:
                raise RuntimeError(f"validation failed: {status}")
    if selected != tuple(sorted(names)):
        raise ValueError("selected inventory differs from the complete benchmark fixture")
    print(
        json.dumps(
            {
                "seconds": elapsed,
                "resolve_calls": resolutions,
                "validator_invocations": invocations,
                "selected_files": len(selected),
                "validator_executables": sorted(executables),
            }
        )
    )


def main() -> None:
    """Collect paired workflow timings and optionally write the raw evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", help="src directory, in paired comparison order; repeat for before/after")
    parser.add_argument("--python", type=Path, action="append", help="prepared interpreter; repeat for historical comparisons")
    parser.add_argument("--output", type=Path, help="write raw JSON review evidence outside current guidance documents")
    parser.add_argument("--files", type=int, default=468)
    parser.add_argument("--trials", type=int, default=9)
    parser.add_argument("--jit-pair", action="store_true", help="compare on/off within each JIT-capable build; also measure warmed selection")
    parser.add_argument("--worker", choices=("selection", "selection-warm", "validation"), help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--inventory", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.root is None or args.inventory is None or not args.source or len(args.source) != 1:
            parser.error("worker requires root, inventory, and one source")
        check_origin(args.source[0])
        worker(args.worker, args.root, args.inventory)
        return
    if args.files < 1 or args.trials < 1:
        parser.error("files and trials must be positive")
    sources = [path.resolve(strict=True) for path in (args.source or [Path(__file__).resolve().parents[1] / "src"])]
    if len(set(sources)) != len(sources):
        parser.error("source directories must be distinct")
    for source in sources:
        if not (source / "research_repo_tools/__init__.py").is_file():
            parser.error(f"source must contain research_repo_tools/__init__.py: {source}")
    pythons = [path.absolute() for path in (args.python or [Path(sys.executable)])]
    # Explicitly select ordinary import behavior and isolate caller redirections.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "UV_")) and key != "VIRTUAL_ENV"}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHON_COLORS="0", NO_COLOR="1")
    records = []
    script = Path(__file__).resolve()
    if args.output is not None:
        check_output(args.output, sources, pythons, script)
    script_digest = hashlib.sha256(script.read_bytes()).hexdigest()
    source_digests = {source: source_digest(source) for source in sources}
    with tempfile.TemporaryDirectory(prefix="python-workflows-") as directory:
        root = Path(directory).resolve()
        names = [f"group {index % 12}/nested/café {index:04}.py" for index in range(args.files)]
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"def value() -> int:\n    return 1\n")
        inventory = root / "inventory.json"
        inventory.write_text(json.dumps(names), encoding="utf-8", newline="\n")
        for python in pythons:
            tools_env = {**env, "PATH": str(python.parent) + os.pathsep + env.get("PATH", "")}
            variants = ("off", "on") if args.jit_pair else ("default",)
            metadata = {}
            for variant in variants:
                probe_env = {**tools_env, **({"PYTHON_JIT": "1" if variant == "on" else "0"} if variant != "default" else {})}
                probe = execute(
                    [
                        str(python),
                        "-c",
                        "import json,sys,yaml,shutil; from importlib.metadata import version; j=getattr(sys,'_jit',None); "
                        "print(json.dumps(dict(python=sys.version, executable=sys.executable, "
                        "jit_available=bool(j and j.is_available()), jit_enabled=bool(j and j.is_enabled()), "
                        "libyaml=yaml.__with_libyaml__, tools={n:version(n) for n in ('packaging','PyYAML','ruff','ty')}, "
                        "tool_paths={n:shutil.which(n) for n in ('ruff','ty')})))",
                    ],
                    env=probe_env,
                    label=f"interpreter probe {python} JIT={variant}",
                )
                info = _probe_record(probe.stdout, "interpreter probe")
                if (
                    not isinstance(info.get("python"), str)
                    or not info["python"].strip()
                    or not isinstance(info.get("tools"), dict)
                    or not isinstance(info.get("tool_paths"), dict)
                    or any(not isinstance(info["tools"].get(name), str) for name in ("ruff", "ty"))
                    or any(name not in info["tool_paths"] or not isinstance(info["tool_paths"][name], (str, type(None))) for name in ("ruff", "ty"))
                ):
                    raise ValueError(f"{python}: interpreter probe requires version and native tool metadata")
                metadata[variant] = info
                if args.jit_pair and (info.get("jit_available") is not True or info.get("jit_enabled") is not (variant == "on")):
                    parser.error("JIT comparison requires the same capable build to honor PYTHON_JIT=0/1")
            native_tools = {}
            for name in ("ruff", "ty"):
                path = metadata[variants[0]]["tool_paths"][name]
                if path is None:
                    raise ValueError(f"{python}: native {name} is missing from the measurement PATH")
                if args.output is not None and args.output.exists() and args.output.samefile(path):
                    raise ValueError(f"measurement output overlaps the native {name} executable")
                result = execute([path, "--version"], env=tools_env, label=f"native {name} probe for {python}")
                if result.stdout.split()[:2] != [name, metadata[variants[0]]["tools"][name]]:
                    raise ValueError(f"{python}: native {name} version differs from installed metadata: {result.stdout.strip()}")
                native_tools[name] = {"path": path, "version_output": result.stdout.strip(), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            for source in sources:
                probe = execute(
                    [str(python), "-c", "import research_repo_tools; print(research_repo_tools.__file__)"],
                    env={**tools_env, "PYTHONPATH": str(source)},
                    label=f"source probe {source} with {python}",
                )
                if Path(probe.stdout.strip()).resolve() != (source / "research_repo_tools/__init__.py").resolve():
                    raise ValueError(f"{python}: imported source differs from {source}: {probe.stdout.strip()}")
            cases = [(source, variant) for source in sources for variant in variants]
            for mode in ("selection", *(("selection-warm",) if args.jit_pair else ()), "validation", "help", "version"):
                rows = {case: [] for case in cases}
                diagnostics = {}
                for trial in range(args.trials + 1):
                    ordered = cases if trial % 2 == 0 else list(reversed(cases))
                    for source, variant in ordered:
                        case_env = {**tools_env, "PYTHONPATH": str(source), **({"PYTHON_JIT": "1" if variant == "on" else "0"} if variant != "default" else {})}
                        command = (
                            [str(python), str(script), "--worker", mode, "--root", str(root), "--inventory", str(inventory), "--source", str(source)]
                            if mode in {"selection", "selection-warm", "validation"}
                            else [str(python), "-m", "research_repo_tools", "--" + mode]
                        )
                        verify_inputs(source_digests, script, script_digest)
                        start = time.perf_counter()
                        result = execute(command, env=case_env, cwd=root, timeout=300, label=f"{mode} source={source} interpreter={python} JIT={variant}")
                        elapsed = time.perf_counter() - start
                        verify_inputs(source_digests, script, script_digest)
                        if mode in {"selection", "selection-warm", "validation"}:
                            record = _probe_record(result.stdout, f"{mode} worker")
                            elapsed = record.get("seconds")
                            if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not 0 <= elapsed <= sys.float_info.max:
                                raise ValueError(f"{mode} worker requires finite nonnegative seconds")
                            if any(
                                type(record.get(field)) is not int or record[field] < 0
                                for field in ("selected_files", "validator_invocations", "resolve_calls")
                            ):
                                raise ValueError(f"{mode} worker requires nonnegative integer counts")
                            if record["selected_files"] != len(names):
                                raise ValueError(f"{mode} worker reported an incomplete fixture inventory")
                            diagnostics[source, variant] = record
                            if mode == "validation" and record.get("validator_executables") != sorted(tool["path"] for tool in native_tools.values()):
                                raise ValueError("validation used different executables from the recorded native tools")
                        if trial:
                            rows[source, variant].append(elapsed)
                for source, variant in cases:
                    record = {
                        "source": str(source),
                        "source_sha256": source_digests[source],
                        "mode": mode,
                        "jit_variant": variant,
                        "interpreter": metadata[variant],
                        "native_tools": native_tools,
                        "samples_seconds": rows[source, variant],
                        "median_seconds": statistics.median(rows[source, variant]),
                        "diagnostics": diagnostics.get((source, variant)),
                    }
                    records.append(record)
                    print(f"{metadata[variant]['python'].split()[0]} {source} {mode} JIT={variant}: {record['median_seconds']:.6f}s", flush=True)
            for name, tool in native_tools.items():
                if hashlib.sha256(Path(tool["path"]).read_bytes()).hexdigest() != tool["sha256"]:
                    raise ValueError(f"native {name} changed during the experiment")
    verify_inputs(source_digests, script, script_digest)
    output = {
        "host": platform.platform(),
        "files": args.files,
        "trials": args.trials,
        "inventory_sha256": hashlib.sha256(json.dumps(names).encode()).hexdigest(),
        "records": records,
        "harness_sha256": script_digest,
        "source_hash_format": "relative-name-and-content-digest/v1",
        "notes": "One warmup discarded; alternating paired order; setup/imports excluded from selection and validation timings. "
        "selection-warm uses five untimed selections then averages twenty calls within each fresh worker. "
        "Help/version include process startup. Diagnostic resolve counts collected outside timing. "
        "Git NUL inventory modeled to avoid fixture mutations; all files and native validators real. "
        "Source and harness identities verified before/after workers; native tools verified against installed versions. "
        "JIT comparisons are only the explicit same-build off/on pairs.",
    }
    if args.output:
        write_output(args.output, output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"measure-python-workflows: {error}", file=sys.stderr)
        raise SystemExit(1) from None
