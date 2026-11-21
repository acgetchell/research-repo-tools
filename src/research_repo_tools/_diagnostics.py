"""Standard-library exception rendering shared with the install-free runner."""

import re
import subprocess
from urllib.parse import urlsplit


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
    return _single_line(result) if single_line else result


def _single_line(result: str) -> str:
    """Bound a diagnostic after removing terminal escapes and folding control characters."""
    result = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", result)
    result = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result)
    result = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", result)
    result = " ".join(result.split())
    return result if len(result) <= 400 else result[:399] + "…"


def redact_credentials(value: str | bytes) -> str:
    """Redact GitHub tokens and strip user information, queries, and fragments from URLs.

    Apply to rendered subprocess diagnostics before logging them. This cannot
    identify arbitrary secrets in free text; never include tokens in commands.
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    def url(match: re.Match[str]) -> str:
        try:
            parsed = urlsplit(match[0])
            if not parsed.hostname:
                return "<redacted-url>"
            if not (parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None):
                return match[0]
            host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            port = f":{parsed.port}" if parsed.port is not None else ""
            return f"{parsed.scheme}://{host}{port}{parsed.path}?<redacted>"
        except ValueError:
            return "<redacted-url>"

    value = re.sub(r"(?i)https?://[^\s\"<>]+", url, value)
    return re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b", "<redacted-token>", value).strip()


def workflow_diagnostic(message: str, *, single_line: bool = False) -> str:
    """Redact registry and release credentials before optionally shortening the message."""
    safe = redact_credentials(message)
    return _single_line(safe) if single_line else safe


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
