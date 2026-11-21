"""Standard-library output boundaries shared by installed and source runners."""

import json
import sys
from typing import TextIO


def console_text(text: str, stream: TextIO) -> str:
    """Escape only characters the caller's text stream cannot represent."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="backslashreplace").decode(encoding)


def write_stdout(output: bytes) -> None:
    """Preserve machine-output bytes independently of console encoding/newlines."""
    # Preserve ordering if a caller previously buffered text. Text-only capture
    # streams receive decoded UTF-8, as they have no byte-transport boundary.
    sys.stdout.flush()
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(output)
    else:
        sys.stdout.write(output.decode("utf-8"))


def write_json_stdout(value: object, *, indent: int | None = None, sort_keys: bool = False) -> None:
    """Emit a complete JSON result in UTF-8 with one final LF."""
    text = json.dumps(value, ensure_ascii=True, allow_nan=False, indent=indent, sort_keys=sort_keys)
    write_stdout((text + "\n").encode("utf-8"))
