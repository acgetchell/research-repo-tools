"""Update the active uv through its owner and reconcile the project declaration."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.files import replace_if_unchanged
from research_repo_tools.process import run_safe_command
from research_repo_tools.tool_pins import STABLE, check_uv


def _standalone_installation(executable: Path) -> bool:
    """Require the first uv install receipt to identify this exact executable.

    Follow uv/axoupdater's receipt search order, including explicit overrides.
    Unknown receipt layouts fail closed; uv still validates the full receipt
    before self-updating. A receipt for another installation grants no ownership.
    """
    if "AXOUPDATER_CONFIG_WORKING_DIR" in os.environ:
        prefixes = [Path.cwd()]
    elif "AXOUPDATER_CONFIG_PATH" in os.environ:
        prefixes = [Path(os.environ["AXOUPDATER_CONFIG_PATH"])]
    else:
        prefixes = []
        if "XDG_CONFIG_HOME" in os.environ:
            prefixes.append(Path(os.environ["XDG_CONFIG_HOME"]) / "uv")
        if os.name == "nt":
            if "LOCALAPPDATA" in os.environ:
                prefixes.append(Path(os.environ["LOCALAPPDATA"]) / "uv")
        else:
            prefixes.append(Path.home() / ".config" / "uv")
    for prefix in prefixes:
        receipt = prefix / "uv-receipt.json"
        try:
            data = json.loads(receipt.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            continue
        except OSError, ValueError:
            return False
        if not isinstance(data, dict) or data.get("install_layout") != "flat":
            return False
        install_prefix = data.get("install_prefix")
        binaries = data.get("binaries")
        if not isinstance(install_prefix, str) or not install_prefix or "\x00" in install_prefix or not Path(install_prefix).is_absolute():
            return False
        name = "uv.exe" if os.name == "nt" else "uv"
        if not isinstance(binaries, list) or name not in binaries:
            return False
        return (Path(install_prefix) / name).resolve() == executable.resolve()
    return False


def updated_manifest(text: str, new: str) -> str:
    """Change one conventional uv pin, preserving formatting and other settings."""
    before = tomllib.loads(text)
    tool = before.get("tool", {})
    if not isinstance(tool, dict):
        raise ValueError("tool must be a table")
    uv = tool.get("uv", {})
    if not isinstance(uv, dict):
        raise ValueError("tool.uv must be a table")
    old = uv.get("required-version")
    if not isinstance(old, str) or not old.startswith("==") or not STABLE.fullmatch(old[2:]):
        raise ValueError('uv updates require [tool.uv] required-version = "==X.Y.Z"')
    if not STABLE.fullmatch(new) or tuple(map(int, new.split("."))) < (0, 12, 10):
        raise ValueError("uv updates require a stable version >=0.12.10")
    table = re.search(r"(?ms)^\[tool\.uv\][ \t]*(?:#[^\r\n]*)?\r?\n(?P<body>.*?)(?=^\[|\Z)", text)
    if table is None:
        raise ValueError("uv updates require a conventional [tool.uv] table")
    body, count = re.subn(
        r"""(?m)^(required-version\s*=\s*)(["'])==[^"'\r\n]+\2""",
        lambda match: f"{match[1]}{match[2]}=={new}{match[2]}",
        table["body"],
    )
    if count != 1:
        raise ValueError("uv updates require one required-version assignment in [tool.uv]")
    candidate = text[: table.start("body")] + body + text[table.end("body") :]
    uv["required-version"] = f"=={new}"
    if tomllib.loads(candidate) != before:
        raise ValueError("uv update would change unrelated project settings")
    return candidate


@dataclass(frozen=True, slots=True)
class _ToolInstallation:
    receipt: Path
    constraints: dict
    interpreter: tuple[tuple[str, str], ...]


def _uv_tool_installation(executable: Path) -> _ToolInstallation | None:
    """Bind a uv tool receipt to both its entrypoint and environment binary.

    POSIX symlinks resolve into the environment. Windows entrypoints can be
    copies, so compare their bytes with the binary in the receipted environment.
    Receipt constraints remain owned by uv's native tool upgrade operation.
    """
    output = run_safe_command(str(executable), ["tool", "dir", "--no-config"], timeout=30).stdout.strip()
    directory = Path(output)
    if not output or not directory.is_absolute():
        raise ValueError("uv tool dir did not return an absolute installation directory")
    environment = directory / "uv"
    receipt = environment / "uv-receipt.toml"
    if not receipt.exists():
        return None
    data = tomllib.loads(receipt.read_bytes().decode("utf-8"))
    tool = data.get("tool")
    if not isinstance(tool, dict) or not isinstance(entries := tool.get("entrypoints"), list):
        raise ValueError(f"invalid uv tool ownership receipt: {receipt}")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("name") in {"uv", "uv.exe"}]
    if len(matches) != 1 or not isinstance(installed := matches[0].get("install-path"), str) or not Path(installed).is_absolute():
        raise ValueError(f"uv tool receipt requires one absolute uv entrypoint: {receipt}")
    owned = Path(installed)
    binary = environment / ("Scripts" if os.name == "nt" else "bin") / ("uv.exe" if os.name == "nt" else "uv")
    if not owned.is_file() or not binary.is_file() or owned.resolve() != executable.resolve():
        return None
    if binary.read_bytes() != executable.read_bytes():
        raise ValueError("active uv differs from its receipted uv tool environment")
    configuration = environment / "pyvenv.cfg"
    if not configuration.is_file():
        raise ValueError("uv tool receipt has no owning Python environment")
    interpreter: dict[str, str] = {}
    for line in configuration.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if key in {"home", "implementation", "version_info", "executable", "include-system-site-packages"}:
            if not separator or key in interpreter:
                raise ValueError("uv tool environment has malformed interpreter identity")
            interpreter[key] = value.strip()
    if not interpreter.get("home"):
        raise ValueError("uv tool environment has no interpreter home")
    return _ToolInstallation(receipt, tool, tuple(sorted(interpreter.items())))


def update(root: Path, *, uv: str = "uv", dry_run: bool = False) -> str:
    """Upgrade uv explicitly, then publish the verified project pin."""
    manifest = root / "pyproject.toml"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("uv update requires a regular pyproject.toml, not a symlink")
    original = manifest.read_bytes()
    text = original.decode("utf-8")
    updated_manifest(text, "0.12.10")  # Validate the pin before changing the installation.
    executable = shutil.which(uv)
    if executable is None:
        raise ValueError("uv must be installed before updating it")
    current = check_uv(executable=executable)
    if tuple(map(int, current.split("."))) < (0, 12, 10):
        raise ValueError("uv update requires a stable installed version >=0.12.10; no update attempted")
    output = run_safe_command(executable, ["--version"], timeout=30).stdout
    tool_receipt = None
    if "Homebrew" in output:
        reported_prefix = Path(run_safe_command("brew", ["--prefix", "uv"], timeout=30).stdout.strip())
        if not reported_prefix.is_absolute():
            raise ValueError("Homebrew uv prefix must be absolute")
        prefix = reported_prefix.resolve()
        if not Path(executable).resolve().is_relative_to(prefix):
            raise ValueError("active uv does not belong to the detected Homebrew installation")
        inventory = run_safe_command("brew", ["list", "--versions", "uv"], timeout=30).stdout.split()
        if len(inventory) != 2 or inventory[0] != "uv" or inventory[1].split("_", 1)[0] != current:
            raise ValueError("active uv disagrees with the Homebrew installation inventory; no upgrade attempted")
        owner, command, arguments = "homebrew", "brew", ["upgrade", "uv"]
    elif _standalone_installation(Path(executable)):
        owner, command, arguments = "standalone", executable, ["self", "update", "--no-config"]
    else:
        tool_receipt = _uv_tool_installation(Path(executable))
        if tool_receipt is None:
            raise ValueError(
                f"No matching uv installation receipt for {executable}; automatic upgrades support standalone, Homebrew, and uv tool installations. "
                "For pip, pipx, WinGet, Scoop, MacPorts, Cargo, or another owner, upgrade uv with its original package manager and reconcile "
                "[tool.uv].required-version with uv --version. Alternatively, use the official standalone installer. No upgrade was attempted."
            )
        owner, command, arguments = "uv-tool", executable, ["tool", "upgrade", "uv", "--no-config", "--prerelease", "disallow", "--no-python-downloads"]
    print(f"uv owner: {owner}; operation: {command} {' '.join(arguments)}; current stable version: {current}", flush=True)
    if dry_run:
        print(f"Preview: [tool.uv].required-version will follow the verified stable result (currently =={current}); no update or write.")
        return current
    environment = {key: value for key, value in os.environ.items() if key.upper() not in {"UV_PYTHON", "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON"}}
    try:
        run_safe_command(command, arguments, timeout=600, capture_output=False, env=environment)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"uv {owner} update failed; project pin unchanged. Inspect the owner's installation and retry: {error}") from error
    try:
        new = check_uv(executable=executable)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            f"uv {owner} operation completed, but verification failed; installation may have changed, project pin unchanged; inspect and retry: {error}"
        ) from error
    if tuple(map(int, new.split("."))) < tuple(map(int, current.split("."))):
        raise ValueError("uv owner returned an older version; installation changed, project pin unchanged; inspect and retry")
    if tool_receipt is not None:
        try:
            after = _uv_tool_installation(Path(executable))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise RuntimeError(
                f"uv tool operation completed, but ownership verification failed; installation may have changed, project pin unchanged; inspect and retry: {error}"
            ) from error
        retained = ("python", "requirements", "constraints", "overrides", "excludes", "build-constraint-dependencies")
        if (
            after is None
            or after.receipt != tool_receipt.receipt
            or after.interpreter != tool_receipt.interpreter
            or any(after.constraints.get(key) != tool_receipt.constraints.get(key) for key in retained)
        ):
            raise ValueError("uv tool ownership or interpreter/requirement constraints changed; installation changed, project pin unchanged; inspect and retry")
    payload = updated_manifest(text, new).encode("utf-8")
    if payload != original:
        try:
            replace_if_unchanged(manifest, original, payload)
        except (OSError, ValueError) as error:
            raise RuntimeError(f"uv {new} installed, but project pin publication failed; preserve local edits, repair the cause, and rerun: {error}") from error
    print(f"uv {current} -> {new}; {'unchanged' if payload == original else 'project pin reconciled'}")
    return new
