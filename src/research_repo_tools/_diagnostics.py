"""Standard-library exception rendering shared with the install-free runner."""

import subprocess


def _diagnostic_stream(value: str | bytes | None) -> str:
    """Return captured subprocess output as stripped, readable text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return value.strip()


def _diagnostic_command(value: object) -> str:
    """Render a subprocess command without Python container syntax."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(part) for part in value)
    return str(value)


def format_exception_diagnostics(error: BaseException, *, single_line: bool = False) -> str:
    """Render nested diagnostics, optionally bounded for a terse CLI operation."""
    result = _format_exception_diagnostics(error)
    result += "".join(f"\n{note}" for note in getattr(error, "__notes__", ()))
    if single_line:
        # Remove terminal escape sequences before folding control characters.
        import re

        result = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", result)
        result = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result)
        result = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", result)
        result = " ".join(result.split())
        if len(result) > 400:
            result = result[:399] + "…"
    return result


def _format_exception_diagnostics(error: BaseException) -> str:
    """Render expected CLI failures without discarding nested diagnostics."""
    if isinstance(error, BaseExceptionGroup):
        lines = [f"{error.message} ({len(error.exceptions)} sub-exceptions):"]
        for index, child in enumerate(error.exceptions, start=1):
            detail_lines = format_exception_diagnostics(child).splitlines() or [child.__class__.__name__]
            lines.append(f"  {index}. {detail_lines[0]}")
            lines.extend(f"     {line}" for line in detail_lines[1:])
        return "\n".join(lines)

    if isinstance(error, subprocess.CalledProcessError):
        parts = [f"command failed with exit status {error.returncode}: {_diagnostic_command(error.cmd)}"]
        stdout = _diagnostic_stream(error.stdout)
        stderr = _diagnostic_stream(error.stderr)
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        return "\n".join(parts)

    if isinstance(error, subprocess.TimeoutExpired):
        parts = [f"command timed out after {error.timeout} seconds: {_diagnostic_command(error.cmd)}"]
        stdout = _diagnostic_stream(error.stdout)
        stderr = _diagnostic_stream(error.stderr)
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        return "\n".join(parts)

    return str(error)
