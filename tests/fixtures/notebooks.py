"""Notebook cell and repository fixtures used by notebook consumers."""

import os
from pathlib import Path

from research_repo_tools.process import run_git_bytes


def code(source="value = 1\n", cell_id="calculate-value"):
    """Build a code cell with an explicit ID, empty outputs, and no execution count."""
    return {"cell_type": "code", "id": cell_id, "metadata": {}, "source": source, "outputs": [], "execution_count": None}


def fixture_git(root: Path, *args: str):
    """Run Git in a notebook fixture with local configuration and disabled hooks."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return run_git_bytes(
        ["--no-pager", "-c", "core.autocrlf=false", "-c", f"core.hooksPath={root / 'disabled-hooks'}", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        env=env,
    )
