"""Line-preserving Rust Markdown/rustdoc fence inputs for native Semgrep."""

import re
from pathlib import Path

__all__ = ["rust_blocks"]
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
ATTRIBUTE = re.compile(r"(?:no_run|ignore(?:-.+)?|should_panic|compile_fail|test_harness|standalone_crate|edition(?:2015|2018|2021|2024)|E[0-9]{4})")


def _documentation(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if path.suffix != ".rs":
        return lines
    result = []
    depth = 0
    for raw in lines:
        stripped = raw.lstrip()
        if not depth and stripped.startswith(("///", "//!")) and not stripped.startswith("////"):
            line = stripped[3:].removeprefix(" ")
        elif not depth and stripped.startswith(("/**", "/*!")) and not stripped.startswith("/***"):
            depth = 1
            line = stripped[3:].removeprefix(" ")
        elif depth:
            line = stripped.removeprefix("*").removeprefix(" ") if not stripped.startswith("*/") else stripped
        else:
            line = ""
        if depth:
            # Preserve lines inside nested block comments without ending the
            # outer rustdoc at the first nested terminator.
            depth += line.count("/*") - line.count("*/")
            if depth <= 0:
                depth = 0
                line = line.rsplit("*/", 1)[0]
        result.append(line)
    return result


def rust_blocks(path: Path) -> tuple[str, ...]:
    """Extract fenced snippets; blank padding retains original line numbers.

    Recognize Rust, implicit Rust and rustdoc attribute fences, including hidden
    `# ` lines. Other-language fences stay excluded. This is a documentation
    adapter, not a Rust parser: macro-generated docs and #[doc = ...] are outside
    its scope. An unclosed selected fence fails rather than losing coverage.
    """
    blocks = []
    active = None
    delimiter = ""
    for number, line in enumerate(_documentation(path)):
        fence = FENCE.match(line)
        if fence:
            marker, info = fence.groups()
            if delimiter and marker[0] == delimiter[0] and len(marker) >= len(delimiter) and not info.strip():
                if active is not None:
                    blocks.append("\n".join(active) + "\n")
                active = None
                delimiter = ""
                continue
            if not delimiter:
                delimiter = marker
                tags = set(re.split(r"[,\s]+", info.strip())) - {""}
                if "rust" in tags or all(ATTRIBUTE.fullmatch(tag) for tag in tags):
                    active = [""] * (number + 1)
                continue
        if active is not None:
            stripped = line.lstrip()
            indentation = line[: len(line) - len(stripped)]
            if stripped.startswith("##"):
                content = indentation + stripped[1:]
            elif stripped.rstrip() == "#":
                content = ""
            elif stripped.startswith("# "):
                content = indentation + stripped[2:]
            else:
                content = line
            active.append(content)
    if active is not None:
        raise ValueError(f"unclosed Rust documentation fence: {path}")
    return tuple(blocks)
