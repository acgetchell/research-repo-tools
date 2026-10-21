"""Load the reviewed standard-library release implementation without installing it."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    raise SystemExit(runpy.run_path(str(Path(__file__).resolve().parents[1] / "src/research_repo_tools/release_validation.py"))["main"]())
