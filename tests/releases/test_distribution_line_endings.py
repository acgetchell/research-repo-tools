"""Read-only Git integration for resources checked against shared distributions."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("resource", ["NOTICE.md", "docs/provenance.json"])
def test_windows_checkout_preserves_packaged_resource_bytes(resource: str) -> None:
    """Git's Windows newline setting must preserve the canonical archive bytes."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required to exercise checkout filters")
    command = [git, "--no-pager", "-c", "core.autocrlf=true", "-c", f"core.attributesFile={os.devnull}"]
    env = {**os.environ, "GIT_ATTR_NOSYSTEM": "1"}

    def read(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([*command, *args], cwd=ROOT, env=env, capture_output=True, check=False, timeout=30)

    checkout = read("rev-parse", "--show-toplevel")
    if checkout.returncode or Path(os.fsdecode(checkout.stdout).strip()).resolve() != ROOT:
        pytest.skip("A source checkout is required; source archives have no Git filters")

    # Use an existing tracked blob as the input to the real checkout filter.
    # cat-file reads objects and renders bytes; it changes no files, index, or refs.
    canonical = read("cat-file", "blob", f"HEAD:{resource}")
    canonical.check_returncode()
    assert b"\n" in canonical.stdout and b"\r\n" not in canonical.stdout
    rendered = read("cat-file", "--filters", f"--path={resource}", f"HEAD:{resource}")
    rendered.check_returncode()
    assert rendered.stdout == canonical.stdout, f"Windows checkout changes the bytes of {resource}"
