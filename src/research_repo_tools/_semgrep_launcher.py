"""Bound Semgrep's unlimited-stack arithmetic in its own POSIX child process.

Semgrep #11979 divides Python 3.15's positive RLIM_INFINITY by three. Give
the scanner the same maximum finite stack budget its Python 3.14 algorithm
would select instead. No caller limits or installed dependencies are changed.
Remove this adapter when the pinned scanner incorporates upstream #11987.
"""

import os
import sys
from pathlib import Path


def stack_limits(soft: int, hard: int, infinity: int) -> tuple[int, int]:
    """Preserve finite limits; model the old scanner budget for unlimited hard."""
    if hard != infinity:
        return soft, hard
    candidates = [1_000_000_000]
    if soft != infinity:
        candidates.extend((soft, soft * 5, soft * 10, soft * 100))
    budget = max(value for value in candidates if infinity < 0 or value < infinity)
    return (budget if soft == infinity else soft), budget


def command(binary: Path) -> tuple[str, ...]:
    if os.name != "nt":
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
        if stack_limits(soft, hard, resource.RLIM_INFINITY) != (soft, hard):
            return sys.executable, "-I", "-X", "utf8", "-m", "research_repo_tools._semgrep_launcher", str(binary)
    return (str(binary),)


def main() -> None:
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    selected = stack_limits(soft, hard, resource.RLIM_INFINITY)
    if selected != (soft, hard):
        resource.setrlimit(resource.RLIMIT_STACK, selected)
    binary, *arguments = sys.argv[1:]
    os.execv(binary, [binary, *arguments])


if __name__ == "__main__":
    main()
