"""Exercise installed-wheel setup on disposable GitHub-hosted native runners.

Downloads and installs real tools, compiles Rust, and updates the runner user's
shell configuration (or Windows user PATH). Intentionally excluded from just ci.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require_hosted_runner() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("check-setup requires a disposable GitHub-hosted runner; it installs tools and updates the runner user's PATH")


def isolated_environment(directory: Path) -> dict[str, str]:
    # Preserve native compiler/SDK and network configuration, but never inherit
    # another project's interpreter, managed tools, or uv directory overrides.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "RUSTUP_", "CARGO_", "PYTHON")) and key not in {"VIRTUAL_ENV", "CONDA_PREFIX", "ZDOTDIR", "BASH_ENV", "ENV"}
    }
    env.update(
        PYTHONUTF8="1",
        UV_CACHE_DIR=str(directory / "uv-cache"),
        UV_PYTHON_INSTALL_DIR=str(directory / "python"),
        UV_TOOL_DIR=str(directory / "uv-tools"),
        UV_TOOL_BIN_DIR=str(directory / "user-bin"),
        RESEARCH_REPO_TOOLS_HOME=str(directory / "managed-tools"),
    )
    if os.name != "nt":
        # uv checks shell-specific markers before SHELL. Hosted runners can
        # inherit PowerShell's marker even when this check is launched by Bash.
        for marker in ("NU_VERSION", "FISH_VERSION", "BASH_VERSION", "ZSH_VERSION", "KSH_VERSION", "PSModulePath"):
            env.pop(marker, None)
        env["SHELL"] = "/bin/zsh" if sys.platform == "darwin" else "/bin/bash"
    # Drop the outer `uv run` environment from PATH as well as VIRTUAL_ENV.
    excluded = {Path(sys.executable).parent, ROOT / ".venv" / "bin", ROOT / ".venv" / "Scripts"}
    env["PATH"] = os.pathsep.join(entry for entry in env.get("PATH", "").split(os.pathsep) if entry and Path(entry) not in excluded)
    return env


def run(command: list[str], *, cwd: Path, env: dict[str, str], expected: int = 0) -> str:
    print(f"Running: {command}", flush=True)
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=3600)
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", file=sys.stderr, flush=True)
    if result.returncode != expected:
        raise RuntimeError(f"Expected exit {expected}, got {result.returncode}: {command}")
    return result.stdout


def binary(directory: Path, name: str) -> Path:
    return directory / (f"{name}.exe" if os.name == "nt" else name)


def shell_state() -> dict[str, bytes | str | None]:
    if os.name == "nt":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _kind = winreg.QueryValueEx(key, "Path")
        return {"user PATH": value}
    paths = [Path.home() / name for name in (".profile", ".bash_profile", ".bashrc", ".zshenv", ".zprofile", ".zshrc")]
    return {path.name: path.read_bytes() if path.exists() else None for path in paths}


def verify_shell(consumer: Path, env: dict[str, str], expected_just: str) -> dict[str, str]:
    user_bin = Path(env["UV_TOOL_BIN_DIR"])
    if os.name == "nt":
        # Child processes inherit stale PATH. Model a new terminal by refreshing
        # the user segment from the actual registry value written by uv.
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference = 'Stop'; "
            "$env:PATH = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:PATH; "
            "(Get-Command just -CommandType Application).Source; just --version; exit $LASTEXITCODE",
        ]
    else:
        command = [env["SHELL"], "-ic", "command -v just && just --version"]
    output = run(command, cwd=consumer, env=env).strip().splitlines()
    assert len(output) >= 2 and Path(output[-2]) == binary(user_bin, "just") and output[-1] == f"just {expected_just}", output
    # Use the now-verified user command for subsequent checks and repeat setup.
    return {**env, "PATH": str(user_bin) + os.pathsep + env["PATH"]}


def check(dist: Path) -> None:
    require_hosted_runner()
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    uv_version = metadata["tool"]["uv"]["required-version"]
    just_version = next(item.removeprefix("rust-just==") for item in metadata["project"]["dependencies"] if item.startswith("rust-just=="))
    dev_pin = next(item for item in metadata["dependency-groups"]["dev"] if item.startswith("ruff=="))
    wheel = dist / f"research_repo_tools-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise ValueError(f"Build artifact is missing: {wheel}")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv must already be installed")
    with tempfile.TemporaryDirectory(prefix="research-repo-tools-native-") as temporary:
        directory = Path(temporary).resolve()
        consumer = directory / "consumer with spaces"
        consumer.mkdir()
        env = isolated_environment(directory)
        (consumer / "pyproject.toml").write_text(
            '[project]\nname="native-setup-consumer"\nversion="0.1.0"\nrequires-python=">=3.14,<3.15"\n'
            f'[dependency-groups]\ntooling=["research-repo-tools=={version}"]\ndev=[{{include-group="tooling"}}, "{dev_pin}"]\n'
            f'[tool.uv]\npackage=false\nrequired-version="{uv_version}"\n'
            f"[tool.uv.sources]\nresearch-repo-tools={{path={json.dumps(str(wheel.resolve()))}}}\n"
            '[tool.research-repo-tools.toolchain.cargo]\ngit-cliff="2.14.1"\n',
            encoding="utf-8",
        )
        (consumer / ".python-version").write_text("3.14\n", encoding="utf-8")
        (consumer / "rust-toolchain.toml").write_text(
            '[toolchain]\nchannel="1.98.0"\nprofile="minimal"\ncomponents=["rustfmt"]\ntargets=["wasm32-unknown-unknown"]\n', encoding="utf-8"
        )
        run([uv, "lock", "--managed-python"], cwd=consumer, env=env)
        declarations = {name: (consumer / name).read_bytes() for name in (".python-version", "pyproject.toml", "rust-toolchain.toml", "uv.lock")}
        launch = [uv, "run", "--locked", "--managed-python", "--only-group", "tooling", "research-repo-tools"]
        initial = json.loads(run([*launch, "toolchain", "check", "--json"], cwd=consumer, env=env, expected=1))
        assert any(not status["ok"] for status in initial if status["name"] == "rustup"), initial
        assert not Path(env["RESEARCH_REPO_TOOLS_HOME"]).exists(), "read-only check installed tools"
        scripts = consumer / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        assert not binary(scripts, "ruff").exists(), "tooling-only startup installed dev dependencies"
        run([*launch, "setup"], cwd=consumer, env=env)
        assert binary(scripts, "ruff").is_file(), "setup did not synchronize dev dependencies"
        assert not (consumer / "scripts").exists(), "setup generated obsolete bootstrap launchers"
        configured = shell_state()
        active = verify_shell(consumer, env, just_version)
        cli = str(binary(scripts, "research-repo-tools"))
        statuses = json.loads(run([cli, "toolchain", "check", "--json"], cwd=consumer, env=active))
        assert statuses and all(status["ok"] for status in statuses), statuses
        selected = {status["name"]: Path(status["path"]) for status in statuses}
        assert selected["just"] == binary(Path(env["UV_TOOL_BIN_DIR"]), "just")
        assert selected["Python"] == binary(scripts, "python")
        # A system installation must never satisfy the managed Rust/Cargo pins.
        for name in ("rustup", "rustc", "cargo", "git-cliff"):
            assert selected[name].is_relative_to(env["RESEARCH_REPO_TOOLS_HOME"]), selected[name]
        probe = (
            "import json, shutil, sys, research_repo_tools; "
            "print(json.dumps({'python': sys.executable, 'base_prefix': sys.base_prefix, 'package': research_repo_tools.__file__, "
            "**{name: shutil.which(name) for name in ('rustc', 'cargo', 'git-cliff')}}))"
        )
        resolved = json.loads(run([cli, "toolchain", "run", "--", "python", "-c", probe], cwd=consumer, env=active))
        assert Path(resolved["python"]) == selected["Python"]
        assert Path(resolved["base_prefix"]).is_relative_to(env["UV_PYTHON_INSTALL_DIR"]), resolved
        assert Path(resolved["package"]).is_relative_to(consumer), "imported the source checkout instead of the installed wheel"
        for name in ("rustc", "cargo"):
            assert Path(resolved[name]) == binary(selected["rustup"].parent, name), resolved
        assert Path(resolved["git-cliff"]) == selected["git-cliff"], resolved
        (consumer / "smoke.rs").write_text('fn main() { println!("native setup works"); }\n', encoding="utf-8")
        program = binary(consumer, "smoke")
        run([cli, "toolchain", "run", "--", "rustc", "smoke.rs", "-o", str(program)], cwd=consumer, env=active)
        assert run([str(program)], cwd=consumer, env=active).strip() == "native setup works"
        (consumer / "justfile").write_text('help:\n    @echo "native just works"\n', encoding="utf-8")
        just = shutil.which("just", path=active["PATH"])
        assert just is not None
        assert run([just, "help"], cwd=consumer, env=active).strip() == "native just works"
        # Compare installed executable identities, declarations, and shell state.
        # Checks may access caches; access timestamps are deliberately excluded.
        installed = {path: (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns) for path in selected.values()}
        run([cli, "setup"], cwd=consumer, env=active)
        assert installed == {path: (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns) for path in installed}, "repeat setup replaced tools"
        assert declarations == {name: (consumer / name).read_bytes() for name in declarations}, "setup changed declarations or lockfile"
        assert shell_state() == configured, "repeat setup changed shell configuration"
        assert json.loads(run([cli, "toolchain", "check", "--json"], cwd=consumer, env=active)) == statuses
        verify_shell(consumer, env, just_version)
        print("PASS: native wheel setup, managed execution, Rust compilation, user Just, and repeat setup", flush=True)


if __name__ == "__main__":
    check(ROOT / "dist")
