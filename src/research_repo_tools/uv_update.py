"""Update the active uv through its owner and reconcile the project declaration."""

import re
import shutil
import tomllib
from pathlib import Path

from research_repo_tools.files import replace_many
from research_repo_tools.process import run_safe_command
from research_repo_tools.tool_pins import STABLE, check_uv


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
        # uv checks its own installation receipt and refuses other package managers.
        run_safe_command(executable, ["self", "update", "--no-config"], timeout=600, capture_output=False)
    new = check_uv(executable=executable)
    payloads = {manifest: updated_manifest(text, new).encode("utf-8")}
    replace_many({path: payload for path, payload in payloads.items() if path.read_bytes() != payload})
    print(f"uv {current} -> {new}; project pin reconciled")
    return new
