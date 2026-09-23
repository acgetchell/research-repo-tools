"""Audit locked third-party requirements without treating this checkout as a release."""

import subprocess
import sys
import tempfile
from pathlib import Path

from research_repo_tools.process import ExecutableNotFoundError, format_exception_diagnostics, run_safe_command

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="research-repo-tools-audit-") as directory:
        requirements = Path(directory) / "requirements.txt"
        run_safe_command(
            "uv",
            ["export", "--locked", "--all-groups", "--no-emit-project", "--no-hashes", "--format", "requirements-txt", "--output-file", str(requirements)],
            cwd=ROOT,
            timeout=120,
        )
        return run_safe_command(
            sys.executable,
            [
                "-m",
                "pip_audit",
                "--strict",
                "--no-deps",
                "--disable-pip",
                "--cache-dir",
                str(Path(directory) / "cache"),
                "--requirement",
                str(requirements),
            ],
            cwd=ROOT,
            timeout=300,
            check=False,
            capture_output=False,
        ).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ExecutableNotFoundError, OSError, subprocess.SubprocessError) as error:
        print(f"Dependency audit failed: {format_exception_diagnostics(error)}", file=sys.stderr)
        raise SystemExit(1) from error
