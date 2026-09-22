"""Validated single-line GitHub Actions command-file export."""

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

__all__ = ["export_environment"]


def export_environment(destination: Path, names: Sequence[str], *, environment: Mapping[str, str] | None = None) -> None:
    """Append LF-delimited UTF-8 assignments after validating the entire batch.

    Names are explicit, unique ASCII environment identifiers. Values must be
    present, nonempty, and free of NUL/CR/LF. Spaces and Windows paths survive
    unchanged. Reject GitHub-reserved names and NODE_OPTIONS. No shell or
    multiline command-file syntax is used. The caller owns the command file
    and must exclude concurrent writers. I/O errors may leave a partial append.
    """
    source = os.environ if environment is None else environment
    if not names or isinstance(names, (str, bytes)):
        raise ValueError("environment names must be a nonempty unique sequence")
    seen: set[str] = set()
    lines = []
    for name in names:
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ValueError(f"invalid environment name: {name!r}")
        if name in seen:
            raise ValueError("environment names must be a nonempty unique sequence")
        seen.add(name)
        if name.upper().startswith(("GITHUB_", "RUNNER_")) or name.upper() == "NODE_OPTIONS":
            raise ValueError(f"reserved environment name: {name}")
        value = source.get(name)
        if not isinstance(value, str) or not value or any(character in value for character in "\0\r\n"):
            raise ValueError(f"environment value is missing, empty, or contains NUL/CR/LF: {name}")
        lines.append(f"{name}={value}\n")
    payload = "".join(lines).encode("utf-8")
    if destination.is_symlink():
        raise ValueError(f"environment command file must not be a symlink: {destination}")
    with destination.open("ab") as stream:
        stream.write(payload)
