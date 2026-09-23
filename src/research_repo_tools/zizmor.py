"""One local/CI audit policy around the consumer's pinned zizmor executable.

Credentials travel only in the child environment. Capture scanner output before
redaction; never forward authentication subprocess diagnostics to the CLI.
"""

import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from packaging.requirements import Requirement

from research_repo_tools.config import Config
from research_repo_tools.dependencies import _group_requirements, canonicalize_name
from research_repo_tools.performance import _write_stdout
from research_repo_tools.process import ExecutableNotFoundError, resolve_executable, run_command_bytes
from research_repo_tools.tool_pins import STABLE, parse_tool_version

_CREDENTIALS = ("ZIZMOR_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
_MODE_VARIABLES = ("ZIZMOR_OFFLINE", "ZIZMOR_NO_ONLINE_AUDITS")
_FAILURES = (ExecutableNotFoundError, OSError, subprocess.SubprocessError)


def _scanner(settings: Config, environment: dict[str, str]) -> tuple[Path, str, dict[str, str]]:
    """Reuse managed Cargo paths or verify the locked Python environment's pin."""
    document = tomllib.loads((settings.root / "pyproject.toml").read_text(encoding="utf-8"))
    groups = document.get("dependency-groups", {})
    requirements = [Requirement(value) for value in _group_requirements(groups, "dev")] if "dev" in groups else []
    pins = [requirement for requirement in requirements if canonicalize_name(requirement.name) == "zizmor"]
    cargo = settings.toolchain.cargo.get("zizmor")
    if len(pins) + (cargo is not None) != 1:
        raise ValueError("declare zizmor exactly once: in dependency-groups.dev (including groups), or toolchain.cargo")
    if cargo is not None:
        from research_repo_tools.toolchain import Runtime
        from research_repo_tools.toolchain_config import executable, load

        runtime = Runtime(load(settings))
        tool = next(tool for tool in runtime.plan.cargo if tool.package == "zizmor")
        path = executable(runtime.cargo_root(tool) / "bin", tool.binary)
        environment = runtime.environment()
        required = cargo
    else:
        requirement = pins[0]
        specifiers = list(requirement.specifier)
        if requirement.marker or requirement.url or requirement.extras or len(specifiers) != 1 or specifiers[0].operator != "==":
            raise ValueError("zizmor requires an unconditional exact X.Y.Z pin")
        required = specifiers[0].version
        path = resolve_executable("zizmor", cwd=settings.root, env=environment)
    if STABLE.fullmatch(required) is None:
        raise ValueError("zizmor requires an exact stable X.Y.Z pin")
    return path, required, environment


def _valid_token(value: str) -> bool:
    return bool(value) and value.isascii() and all(33 <= ord(char) <= 126 for char in value)


def _authentication(environment: dict[str, str], root: Path) -> tuple[str | None, str]:
    for name in ("ZIZMOR_GITHUB_TOKEN", "GH_TOKEN"):
        value = environment.get(name, "")
        if value:
            if not _valid_token(value):
                raise ValueError(f"{name} must contain a nonempty ASCII token without whitespace")
            return value, name
    # GITHUB_TOKEN is intentionally not an implicit fourth token source. Match
    # the scanner host even when gh has another default account configured.
    discovery_env = {key: value for key, value in environment.items() if key not in _CREDENTIALS}
    try:
        result = run_command_bytes(
            "gh",
            ["auth", "token", "--hostname", environment.get("GH_HOST", "github.com")],
            cwd=root,
            env=discovery_env,
            input=b"",
            timeout=10,
            check=False,
        )
    except ExecutableNotFoundError:
        return None, "gh is unavailable"
    except OSError, subprocess.SubprocessError:
        return None, "gh authentication discovery failed"
    if result.returncode:
        return None, "gh authentication discovery failed"
    try:
        token = result.stdout.decode("ascii").strip()
    except UnicodeError:
        return None, "gh returned an invalid token"
    return (token, "gh auth token") if _valid_token(token) else (None, "gh returned an empty or invalid token")


def _redact(payload: bytes, secrets: Sequence[str]) -> bytes:
    spellings = {spelling.encode("utf-8") for secret in secrets if secret for spelling in (secret, json.dumps(secret)[1:-1])}
    for spelling in sorted(spellings, key=len, reverse=True):
        payload = payload.replace(spelling, b"[REDACTED]")
    return payload


def check(settings: Config, paths: Sequence[str], *, output_format: str = "plain", offline: bool = False, require_online: bool = False) -> int:
    """Run one scanner, preserving native exit status and separating reports.

    SARIF has zizmor's native semantics: findings do not change its exit status.
    Use a plain-output gate as well when findings must fail CI. No retry ever
    converts a scanner or authentication failure into an offline success.
    """
    persona = settings.zizmor.persona
    if persona is None:
        raise ValueError("declare an explicit zizmor.persona: regular, pedantic, or auditor")
    inputs = [str(settings.path(value).resolve(strict=True)) for value in paths]
    if not inputs:
        raise ValueError("zizmor requires at least one local input path")
    environment = dict(os.environ)
    secrets = [environment[name] for name in _CREDENTIALS if environment.get(name)]
    try:
        scanner, required, environment = _scanner(settings, environment)
    except _FAILURES:
        raise RuntimeError("could not locate the declared zizmor scanner; run just setup") from None
    token, source = (None, "explicit --offline") if offline else _authentication(environment, settings.root)
    if require_online and token is None:
        raise ValueError(f"zizmor online audits required, but authentication is unavailable ({source})")
    # Explicit CLI policy always wins over inherited zizmor mode/token aliases.
    environment = {key: value for key, value in environment.items() if key not in (*_CREDENTIALS, *_MODE_VARIABLES)}
    try:
        version = run_command_bytes(scanner, ["--version"], cwd=settings.root, env=environment, input=b"", timeout=30)
        actual = parse_tool_version(version.stdout.decode("utf-8"), "zizmor")
    except (*_FAILURES, ValueError):
        raise RuntimeError("could not verify the declared zizmor scanner version") from None
    if actual != required:
        raise ValueError(f"zizmor version does not match declared {required}; run just setup")
    args = ["--strict-collection", "--persona", persona, "--format", output_format, "--no-progress", "--color", "never"]
    if token is not None:
        secrets.append(token)
        environment["ZIZMOR_GITHUB_TOKEN"] = token
        args.extend(["--gh-hostname", environment.get("GH_HOST", "github.com")])
        print(f"zizmor: online audits enabled using {source}; version {required}; persona {persona}", file=sys.stderr)
    else:
        args.append("--offline")
        print(f"zizmor: offline audits only ({source}); online audits skipped; version {required}; persona {persona}", file=sys.stderr)
    try:
        result = run_command_bytes(scanner, [*args, "--", *inputs], cwd=settings.root, env=environment, input=b"", timeout=settings.zizmor.timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"zizmor timed out after {settings.zizmor.timeout} seconds; captured output suppressed") from None
    except _FAILURES:
        raise RuntimeError("zizmor execution failed; captured diagnostics suppressed") from None
    _write_stdout(_redact(result.stdout, secrets))
    sys.stderr.write(_redact(result.stderr, secrets).decode("utf-8", errors="replace"))
    return result.returncode if result.returncode >= 0 else 128 - result.returncode
