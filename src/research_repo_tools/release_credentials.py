"""Limit managed-release credentials to the parent process's API requests."""

import os
from collections.abc import Mapping

from research_repo_tools.process import format_exception_diagnostics

VARIABLES = ("GITHUB_TOKEN", "GH_TOKEN")


def environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy a child environment without either release-lookup variable.

    Compare names case-insensitively, matching Windows environment semantics.
    Never mutate the caller's environment or remove unrelated build settings.
    """
    return {name: value for name, value in (os.environ if source is None else source).items() if name.upper() not in VARIABLES}


def redact(text: str, child: Mapping[str, str] | None = None) -> str:
    """Mask parent and child release credentials while preserving stream formatting."""
    environments = (os.environ,) if child is None else (os.environ, child)
    secrets = {value for source in environments for name, value in source.items() if name.upper() in VARIABLES and value}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def diagnostics(error: BaseException, *, single_line: bool = False) -> str:
    """Redact both inherited lookup credentials before formatting diagnostics."""
    detail = redact(format_exception_diagnostics(error))
    return format_exception_diagnostics(RuntimeError(detail), single_line=single_line)
