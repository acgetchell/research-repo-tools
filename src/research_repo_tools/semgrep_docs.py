"""Line-preserving Rust Markdown/rustdoc fence inputs for native Semgrep."""

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["rust_blocks"]
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
ATTRIBUTE = re.compile(r"(?:no_run|ignore(?:-.+)?|should_panic|compile_fail|test_harness|standalone_crate|edition(?:2015|2018|2021|2024)|E[0-9]{4})")


def _source_lines(path: Path) -> list[str]:
    """Use native LF line boundaries, normalizing CRLF without splitting prose."""
    text = path.read_text(encoding="utf-8", newline="").replace("\r\n", "\n")
    if not text:
        return []
    lines = text.split("\n")
    return lines[:-1] if text.endswith("\n") else lines


def _documentation(path: Path, *, preserve_source: bool = False) -> list[str]:
    lines = _source_lines(path)
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
            line = raw if preserve_source else ""
        if depth:
            # Preserve lines inside nested block comments without ending the
            # outer rustdoc at the first nested terminator.
            depth += line.count("/*") - line.count("*/")
            if depth <= 0:
                depth = 0
                line = line.rsplit("*/", 1)[0]
        result.append(line)
    return result


@dataclass(frozen=True, slots=True)
class RustBlock:
    index: int
    start_line: int
    end_line: int
    text: str
    columns: tuple[tuple[int, int, int], ...]

    @property
    def code(self) -> str:
        """Scan only real code; synthetic line padding can change regex matches."""
        return self.text.split("\n", self.start_line - 1)[-1]

    def source_column(self, line: int, column: int) -> int:
        """Map native byte columns past removed rustdoc and hidden-line markers."""
        if line == self.end_line + 1 and column == 1:
            return column
        if not self.start_line <= line <= self.end_line:
            raise ValueError(f"finding lies outside Rust block {self.index + 1}: line {line}")
        prefix, indentation, hidden = self.columns[line - self.start_line]
        return column + prefix + (hidden if column > indentation else 0)


def documentation_blocks(path: Path) -> tuple[RustBlock, ...]:
    """Extract fenced snippets; blank padding retains original line numbers.

    Recognize Rust, implicit Rust and rustdoc attribute fences, including hidden
    `# ` lines. Other-language fences stay excluded. This is a documentation
    adapter, not a Rust parser: macro-generated docs and #[doc = ...] are outside
    its scope. An unclosed selected fence fails rather than losing coverage.
    """
    blocks = []
    active = None
    start_line = 0
    delimiter = ""
    columns = []
    original = _source_lines(path)
    for number, line in enumerate(_documentation(path)):
        fence = FENCE.match(line)
        if fence:
            marker, info = fence.groups()
            if delimiter and marker[0] == delimiter[0] and len(marker) >= len(delimiter) and not info.strip():
                if active is not None:
                    blocks.append(RustBlock(len(blocks), start_line, number, "\n".join(active) + "\n", tuple(columns)))
                active = None
                delimiter = ""
                continue
            if not delimiter:
                delimiter = marker
                tags = set(re.split(r"[,\s]+", info.strip())) - {""}
                if "rust" in tags or all(ATTRIBUTE.fullmatch(tag) for tag in tags):
                    active = [""] * (number + 1)
                    columns = []
                    start_line = number + 2
                continue
        if active is not None:
            stripped = line.lstrip()
            indentation = line[: len(line) - len(stripped)]
            hidden = 0
            if stripped.startswith("##"):
                content = indentation + stripped[1:]
                hidden = 1
            elif stripped.rstrip() == "#":
                content = ""
                hidden = len(line.encode("utf-8"))
                indentation = ""
            elif stripped.startswith("# "):
                content = indentation + stripped[2:]
                hidden = 2
            else:
                content = line
            active.append(content)
            prefix = len(original[number][: original[number].rfind(line)].encode("utf-8")) if line else len(original[number].encode("utf-8"))
            columns.append((prefix, len(indentation.encode("utf-8")), hidden))
    if active is not None:
        raise ValueError(f"unclosed Rust documentation fence: {path}")
    return tuple(blocks)


def rust_blocks(path: Path) -> tuple[str, ...]:
    """Return line-padded Rust snippets, preserving the public extraction API."""
    return tuple(block.text for block in documentation_blocks(path))
