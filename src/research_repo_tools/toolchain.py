"""Explicit, versioned tool installation and execution for consumer checkouts."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path

from packaging.specifiers import SpecifierSet

from research_repo_tools.process import ExecutableNotFoundError, format_exception_diagnostics, resolve_executable, run_safe_command
from research_repo_tools.tool_pins import SEMVER
from research_repo_tools.toolchain_config import RUSTUP_VERSION, CargoTool, Toolchain, executable, home, host_target

INSTALL_TIMEOUT = 3600
FAILURES = (ExecutableNotFoundError, OSError, ValueError, subprocess.SubprocessError)


@dataclass(frozen=True)
class Status:
    name: str
    required: str
    actual: str
    path: str
    ok: bool


def _probe_key(paths: list[Path]) -> tuple[object, ...]:
    """Notice environment edits, symlink changes, and replaced managed files."""
    files = []
    for path in paths:
        try:
            stat = path.stat()
            files.append((path, path.resolve(), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_mode))
        except OSError as error:
            files.append((path, error.errno))
    return tuple(sorted(os.environ.items())), tuple(files)


def _probe(
    path: str | Path,
    name: str,
    required: str,
    args: tuple[str, ...] = ("--version",),
    *,
    env: dict[str, str],
    cwd: Path,
    version_label: str | None = None,
) -> Status:
    selected = shutil.which(str(path), path=env.get("PATH", ""))
    if selected is None:
        return Status(name, required, "missing", str(path), False)
    try:
        output = run_safe_command(selected, list(args), env=env, cwd=cwd, timeout=30).stdout.strip()
        platform_suffix = r"(?:\.windows\.\d+)?" if name == "git version" else ""
        label = name if version_label is None else version_label
        prefix = re.escape(label) + r"\s+" if label else ""
        match = re.match(rf"^{prefix}({SEMVER.pattern}){platform_suffix}(?=\s|$)", output)
        if match is None:
            raise ValueError(f"unrecognized {name} version output: {output[:200]}")
        actual = match[1]
        return Status(name, required, actual, selected, actual == required if required else True)
    except FAILURES as error:
        return Status(name, required, format_exception_diagnostics(error, single_line=True), selected, False)


class Runtime:
    """Keep manager homes and Cargo installations separate from user-owned tools.

    Construction and inspection do not create directories or install anything.
    Shared cache keys include host, toolchain, package, and package version.
    """

    def __init__(self, plan: Toolchain):
        self.plan = plan
        self.base = home()
        self.host = host_target()
        self.manager = self.base / "rustup" / RUSTUP_VERSION / self.host
        self.rustup = executable(self.manager / "cargo" / "bin", "rustup")
        self._probes: ContextVar[dict[str, tuple[tuple[object, ...], Status]] | None] = ContextVar("toolchain_probes", default=None)

    @contextmanager
    def _operation(self) -> Iterator[None]:
        """Share probes only within this operation, including nested helpers."""
        if self._probes.get() is not None:
            yield
            return
        token = self._probes.set({})
        try:
            yield
        finally:
            self._probes.reset(token)

    def _cached_probe(self, name: str, paths: list[Path], probe: Callable[[], Status]) -> Status:
        cache = self._probes.get()
        if cache is None:
            return probe()
        previous = cache.get(name)
        if previous is not None:
            key, status = previous
            if key == _probe_key([*paths, *([Path(status.path)] if status.path else [])]):
                return status
        status = probe()
        # Failed discovery may be repaired outside the selected paths, so retry
        # failures rather than caching evidence about an absent interpreter.
        if status.ok:
            cache[name] = (_probe_key([*paths, *([Path(status.path)] if status.path else [])]), status)
        else:
            cache.pop(name, None)
        return status

    def cargo_root(self, tool: CargoTool) -> Path:
        assert self.plan.rust is not None
        return self.base / "cargo" / self.host / self.plan.rust.channel / tool.package / tool.version

    def environment(self) -> dict[str, str]:
        with self._operation():
            python = self.python_status(self.uv_status())
            return self._environment(python)

    def project_environment(self) -> dict[str, str]:
        """Keep project synchronization rooted here while preserving its venv override."""
        env = self.environment()
        for name in ("UV_PROJECT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_ENV_FILE"):
            env.pop(name, None)
        return env

    def _environment(self, python: Status | None = None) -> dict[str, str]:
        """Build probe environments without recursively looking up Python."""
        env = dict(os.environ)
        paths = [str(self.cargo_root(tool) / "bin") for tool in self.plan.cargo]
        if self.plan.rust:
            paths.append(str(self.rustup.parent))
            env.update(
                CARGO_HOME=str(self.manager / "cargo"),
                RUSTUP_HOME=str(self.manager / "toolchains"),
                RUSTUP_TOOLCHAIN=self.plan.rust.channel,
                RUSTUP_AUTO_INSTALL="0",
                RUSTUP_NO_UPDATE_CHECK="1",
            )
        if python is not None and python.ok:
            paths.append(str(Path(python.path).parent))
        uv = self.uv_status()
        if uv.ok:
            paths.append(str(Path(uv.path).parent))
        env["PATH"] = os.pathsep.join([*paths, env.get("PATH", "")])
        return env

    def uv_status(self) -> Status:
        # uv is an external prerequisite, never installed or replaced by setup.
        selected = shutil.which("uv", path=os.environ.get("PATH", ""))
        return self._cached_probe(
            "uv", [Path(selected)] if selected else [], lambda: _probe("uv", "uv", self.plan.uv, env=dict(os.environ), cwd=self.plan.root)
        )

    def python_status(self, uv: Status) -> Status:
        environment = Path(os.environ.get("UV_PROJECT_ENVIRONMENT") or ".venv")
        if not environment.is_absolute():
            environment = self.plan.root / environment
        paths = [environment, environment / "pyvenv.cfg", executable(environment / ("Scripts" if os.name == "nt" else "bin"), "python")]
        if directory := os.environ.get("UV_PYTHON_INSTALL_DIR"):
            paths.append(Path(directory))
        # Include the uv executable: changing it can change interpreter discovery.
        if uv.path:
            paths.append(Path(uv.path))
        return self._cached_probe("python", paths, lambda: self._python_status(uv, environment))

    def _python_status(self, uv: Status, environment: Path) -> Status:
        required = self.plan.python_request
        if not uv.ok:
            return Status("Python", required, "uv unavailable", "", False)
        if (environment / "pyvenv.cfg").is_file():
            status = self._python_probe(executable(environment / ("Scripts" if os.name == "nt" else "bin"), "python"))
            if status.ok:
                return status
        try:
            result = run_safe_command(
                uv.path,
                ["python", "find", "--system", "--managed-python", "--no-python-downloads", required],
                cwd=self.plan.root,
                env=self._environment(),
                timeout=30,
            )
            path = result.stdout.strip()
            if not path or not Path(path).is_absolute():
                raise ValueError("uv python find did not return an absolute interpreter path")
            return self._python_probe(path)
        except FAILURES as error:
            return Status("Python", required, format_exception_diagnostics(error, single_line=True), "", False)

    def _python_probe(self, path: str | Path) -> Status:
        required = self.plan.python_request
        status = _probe(path, "Python", "", env=self._environment(), cwd=self.plan.root)
        return Status("Python", required, status.actual, status.path, status.ok and status.actual in SpecifierSet(required))

    def rust_statuses(self) -> list[Status]:
        rust = self.plan.rust
        if rust is None:
            return []
        env = self.environment()
        manager = _probe(self.rustup, "rustup", RUSTUP_VERSION, env=env, cwd=self.plan.root)
        statuses = [manager]
        if not manager.ok:
            return [*statuses, Status("Rust", rust.channel, "rustup unavailable", "", False)]
        for binary in ("rustc", "cargo"):
            try:
                selected = self._rustup(["which", "--toolchain", rust.channel, binary]).stdout.strip()
                if not selected or not Path(selected).is_absolute():
                    raise ValueError(f"rustup which did not return an absolute {binary} path")
                # Cargo's patch version can differ from the Rust release containing it.
                status = _probe(selected, binary, rust.channel if binary == "rustc" else "", env=env, cwd=self.plan.root)
                statuses.append(status)
            except FAILURES as error:
                statuses.append(Status(binary, rust.channel, format_exception_diagnostics(error, single_line=True), "", False))
        for kind, names in (("component", rust.components), ("target", rust.targets)):
            try:
                installed = self._rustup([kind, "list", "--installed", "--toolchain", rust.channel]).stdout.splitlines()
                for name in names:
                    canonical = name.removesuffix("-preview") if name in {"llvm-tools-preview", "rustfmt-preview", "clippy-preview"} else name
                    present = any(item == canonical or (kind == "component" and item == f"{canonical}-{self.host}") for item in installed)
                    statuses.append(Status(f"Rust {kind} {name}", "installed", "installed" if present else "missing", str(self.rustup), present))
            except FAILURES as error:
                statuses.append(Status(f"Rust {kind}s", ", ".join(names), format_exception_diagnostics(error, single_line=True), str(self.rustup), False))
        return statuses

    def cargo_status(self, tool: CargoTool) -> Status:
        env = self.environment()
        # cargo-machete changes argument parsing when CARGO is set. Probes invoke
        # executables directly, so never inherit Cargo's subcommand dispatch marker.
        env.pop("CARGO", None)
        return _probe(
            executable(self.cargo_root(tool) / "bin", tool.binary),
            tool.version_label or tool.binary,
            tool.version,
            tool.version_args,
            env=env,
            cwd=self.plan.root,
            version_label=tool.version_label,
        )

    def shell_status(self) -> Status:
        """Check the shell used by the packaged Just recipes on the user's PATH."""
        selected = shutil.which("sh")
        if selected is None:
            return Status("sh", "POSIX shell", "missing", "", False)
        try:
            run_safe_command(selected, ["-cu", ":"], cwd=self.plan.root, env=dict(os.environ), timeout=30)
        except FAILURES as error:
            return Status("sh", "POSIX shell", format_exception_diagnostics(error, single_line=True), selected, False)
        return Status("sh", "POSIX shell", "available", selected, True)

    def inspect(self) -> list[Status]:
        with self._operation():
            return self._inspect()

    def _inspect(self) -> list[Status]:
        uv = self.uv_status()
        just = _probe("just", "just", version("rust-just"), env=dict(os.environ), cwd=self.plan.root)
        # Git is a system prerequisite; no implicit installation or repository mutation.
        git = _probe("git", "git version", "", ("--no-pager", "--version"), env=dict(os.environ), cwd=self.plan.root)
        return [uv, self.python_status(uv), just, git, self.shell_status(), *self.rust_statuses(), *(self.cargo_status(tool) for tool in self.plan.cargo)]

    def _rustup(self, args: list[str], *, install: bool = False) -> subprocess.CompletedProcess[str]:
        return run_safe_command(
            str(self.rustup), args, cwd=self.plan.root, env=self.environment(), timeout=INSTALL_TIMEOUT if install else 30, capture_output=not install
        )

    def sync(self) -> None:
        """Converge on declared versions, then verify every selected executable."""
        with self._operation():
            self._sync()

    def _sync(self) -> None:
        uv = self.uv_status()
        if not uv.ok:
            raise RuntimeError(f"uv {self.plan.uv} must be installed and available on PATH before setup; found {uv.actual}")
        shell = self.shell_status()
        if not shell.ok:
            raise RuntimeError(
                f"Just recipes require a working POSIX sh on PATH before setup; found {shell.actual}. "
                "On Windows, add Git for Windows' bin directory (containing sh.exe) to PATH, then rerun setup."
            )
        if not self.python_status(uv).ok:
            self._install(uv.path, ["python", "install", "--no-bin", "--no-registry", self.plan.python_request])
            if not self.python_status(uv).ok:
                raise RuntimeError("Python installation did not supply the declared interpreter")
        rust = self.plan.rust
        if rust:
            if not _probe(self.rustup, "rustup", RUSTUP_VERSION, env=self.environment(), cwd=self.plan.root).ok:
                self._install_rustup()
            states = self.rust_statuses()
            if not all(status.ok for status in states):
                args = ["toolchain", "install", rust.channel, "--profile", rust.profile, "--no-self-update"]
                if rust.components:
                    args += ["--component", ",".join(rust.components)]
                if rust.targets:
                    args += ["--target", ",".join(rust.targets)]
                self._install(str(self.rustup), args)
                failed = [status for status in self.rust_statuses() if not status.ok]
                if failed:
                    raise RuntimeError(f"Rust installation failed verification: {failed}")
            for tool in self.plan.cargo:
                if self.cargo_status(tool).ok:
                    continue
                self._install(
                    str(self.rustup),
                    [
                        "run",
                        rust.channel,
                        "cargo",
                        "install",
                        "--locked",
                        "--version",
                        f"={tool.version}",
                        "--root",
                        str(self.cargo_root(tool)),
                        "--force",
                        tool.package,
                    ],
                )
                result = self.cargo_status(tool)
                if not result.ok:
                    raise RuntimeError(
                        f"{tool.package} installation failed verification: {result.actual}; rerun toolchain sync to repair the managed installation"
                    )

    def _install(self, command: str, args: list[str]) -> None:
        print(f"Installing: {Path(command).name} {' '.join(args)}", file=sys.stderr, flush=True)
        try:
            run_safe_command(command, args, cwd=self.plan.root, env=self.environment(), timeout=INSTALL_TIMEOUT, capture_output=False)
        except FAILURES as error:
            native_hint = ""
            if args[-1:] == ["tectonic"]:
                native_hint = (
                    " Tectonic also requires native font/Unicode/compression/TLS libraries discoverable through pkg-config "
                    "or vcpkg (TECTONIC_DEP_BACKEND=vcpkg on Windows). Provision them using "
                    "https://tectonic-typesetting.github.io/book/latest/howto/build-tectonic/external-dep-install.html."
                )
            raise RuntimeError(
                f"Installation failed: {format_exception_diagnostics(error)}\n"
                "Earlier successful installations are retained. Fix the reported cause and rerun toolchain sync. "
                "Native builds require Xcode Command Line Tools on macOS, a C/C++ compiler and development libraries on Linux, "
                f"or Visual Studio C++ Build Tools and a Windows SDK on Windows.{native_hint}"
            ) from error
        finally:
            # Failed installers can also leave partially changed environments.
            if (cache := self._probes.get()) is not None:
                cache.clear()

    def _install_rustup(self) -> None:
        suffix = ".exe" if os.name == "nt" else ""
        url = f"https://static.rust-lang.org/rustup/archive/{RUSTUP_VERSION}/{self.host}/rustup-init{suffix}"
        with tempfile.TemporaryDirectory(prefix="research-repo-tools-rustup-") as temporary:
            installer = Path(temporary) / f"rustup-init{suffix}"
            with urllib.request.urlopen(url + ".sha256", timeout=30) as response:
                fields = response.read(1024).decode("ascii").split()
                checksum = fields[0] if fields else ""
            if re.fullmatch(r"[a-fA-F0-9]{64}", checksum) is None:
                raise ValueError("rustup checksum response must contain a SHA-256 digest")
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = response.read(64 * 1024 * 1024 + 1)
            if len(payload) > 64 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != checksum.lower():
                raise ValueError("rustup installer checksum mismatch; installer was not executed")
            installer.write_bytes(payload)
            installer.chmod(0o700)
            # The installer also uses its own manager homes when Rust isn't selected yet.
            self._install(str(installer), ["-y", "--no-modify-path", "--default-toolchain", "none", "--profile", "minimal"])


def report(statuses: list[Status], *, json_output: bool = False, stream=None) -> bool:
    if json_output:
        print(json.dumps([asdict(status) for status in statuses], indent=2), file=stream)
    else:
        for status in statuses:
            print(
                f"{'OK' if status.ok else 'FAIL'} {status.name}: expected {status.required or 'available'}; found {status.actual}; path {status.path or '(none)'}",
                file=stream,
            )
    return all(status.ok for status in statuses)


def run_command(runtime: Runtime, command: list[str]) -> int:
    """Use the same managed paths that check verified; never install implicitly."""
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise ValueError("toolchain run requires a command after --")
    if (command[:2] == ["cargo", "upgrade"] or command[0] == "cargo-upgrade") and not any(tool.package == "cargo-edit" for tool in runtime.plan.cargo):
        raise ValueError("cargo upgrade requires an exact cargo-edit pin in [tool.research-repo-tools.toolchain.cargo]; declare it and run just setup")
    if not report(runtime.inspect(), stream=sys.stderr):
        raise ValueError("toolchain is incomplete; run toolchain sync before running commands")
    env = runtime.environment()
    selected = resolve_executable(command[0], cwd=runtime.plan.root, env=env)
    result = run_safe_command(str(selected), command[1:], cwd=runtime.plan.root, env=env, capture_output=False, timeout=None, check=False)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode
