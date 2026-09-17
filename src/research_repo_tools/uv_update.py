"""Update the active uv through its owner and reconcile the project declaration."""

import json
import os
import re
import shutil
import tomllib
from pathlib import Path

from research_repo_tools.files import replace_many
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


def update(root: Path, *, uv: str = "uv") -> str:
    """Upgrade uv explicitly, then publish the verified project pin."""
    manifest = root / "pyproject.toml"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("uv update requires a regular pyproject.toml, not a symlink")
    text = manifest.read_bytes().decode("utf-8")
    updated_manifest(text, "0.12.10")  # Validate the pin before changing the installation.
    executable = shutil.which(uv)
    if executable is None:
        raise ValueError("uv must be installed before updating it")
    current = check_uv(executable=executable)
    output = run_safe_command(executable, ["--version"], timeout=30).stdout
    if "Homebrew" in output:
        prefix = Path(run_safe_command("brew", ["--prefix", "uv"], timeout=30).stdout.strip()).resolve()
        if not Path(executable).resolve().is_relative_to(prefix):
            raise ValueError("active uv does not belong to the detected Homebrew installation")
        run_safe_command("brew", ["upgrade", "uv"], timeout=600, capture_output=False)
    else:
        if not _standalone_installation(Path(executable)):
            raise ValueError(
                f"No matching standalone uv installation receipt for {executable}; automatic upgrades support standalone and Homebrew installations. "
                "For pip, pipx, WinGet, Scoop, MacPorts, Cargo, or another owner, upgrade uv with its original package manager and reconcile "
                "[tool.uv].required-version with uv --version. Alternatively, use the official standalone installer. No upgrade was attempted."
            )
        run_safe_command(executable, ["self", "update", "--no-config"], timeout=600, capture_output=False)
    new = check_uv(executable=executable)
    payloads = {manifest: updated_manifest(text, new).encode("utf-8")}
    replace_many({path: payload for path, payload in payloads.items() if path.read_bytes() != payload})
    print(f"uv {current} -> {new}; project pin reconciled")
    return new
