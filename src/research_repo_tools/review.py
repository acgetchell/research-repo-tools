"""Opt-in CodeRabbit review with a verified default base and consumer instructions."""

import re
import subprocess
import sys
from pathlib import Path

from research_repo_tools.process import ExecutableNotFoundError, get_safe_executable, run_git_command, run_safe_command

DEFAULT_BASE = "origin/main"
_FETCH_GUIDANCE = "Run 'git fetch origin', then retry 'just review'."


def _instructions(root: Path) -> list[str]:
    agents = root / "AGENTS.md"
    if not agents.is_file():
        raise ValueError(f"review requires {agents}")
    candidates = [root / name for name in (".coderabbit.yaml", ".coderabbit.yml")]
    present = [path for path in candidates if path.exists()]
    if len(present) != 1 or not present[0].is_file():
        raise ValueError("review requires exactly one regular .coderabbit.yaml or .coderabbit.yml file at the consumer root")
    return [str(agents), str(present[0])]


def _commit(output: str) -> str:
    value = output.strip()
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value):
        raise ValueError("Git did not return exactly one full commit ID")
    return value


def _verify_base(root: Path, base: str) -> None:
    if not base or base.startswith("-") or any(character.isspace() or character == "\0" for character in base):
        raise ValueError("review base must be a nonempty Git reference without whitespace or a leading '-'")
    ref = "refs/remotes/origin/main" if base == DEFAULT_BASE else base
    try:
        local = _commit(run_git_command(["--no-pager", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], cwd=root).stdout)
    except (subprocess.SubprocessError, ValueError) as error:
        guidance = _FETCH_GUIDANCE if base == DEFAULT_BASE else "Select an existing local commit or reference."
        raise ValueError(f"Cannot resolve review base {base!r}. {guidance}") from error
    if base != DEFAULT_BASE:
        return
    try:
        result = run_git_command(["--no-pager", "ls-remote", "--exit-code", "origin", "refs/heads/main"], cwd=root)
        fields = result.stdout.split()
        if len(fields) != 2 or fields[1] != "refs/heads/main":
            raise ValueError("expected exactly one refs/heads/main remote reference")
        remote = _commit(fields[0])
    except (subprocess.SubprocessError, ValueError) as error:
        raise ValueError("Cannot verify origin/main against the remote; review was not started. Check origin and network access, then retry.") from error
    if local != remote:
        raise ValueError(f"origin/main is stale. {_FETCH_GUIDANCE}")


def run(root: Path, *, base: str | None) -> int:
    """Review a branch, or only uncommitted changes when base is None.

    Both instruction files are required at the configured consumer root. Git
    inspection is read-only. CodeRabbit inherits the terminal streams and has
    no wrapper timeout; its status is returned, with signals mapped to 128+N.
    """
    instructions = _instructions(root)
    try:
        executable = get_safe_executable("coderabbit")
    except ExecutableNotFoundError as error:
        raise ExecutableNotFoundError(
            "CodeRabbit CLI is required on PATH. Install and authenticate it explicitly; see https://docs.coderabbit.ai/cli."
        ) from error
    try:
        if base is not None:
            _verify_base(root, base)
        args = ["review", "--agent", "--include-untracked"]
        args += [f"--base={base}"] if base is not None else ["--uncommitted"]
        args += ["--config", *instructions]
        result = run_safe_command(executable, args, cwd=root, capture_output=False, check=False, timeout=None)
    except KeyboardInterrupt:
        print("CodeRabbit review interrupted.", file=sys.stderr)
        return 130
    return result.returncode if result.returncode >= 0 else 128 - result.returncode
