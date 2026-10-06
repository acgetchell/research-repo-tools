"""Read-only publication preflight for this repository's stable PyPI releases.

The workflow separately verifies main-branch ancestry. Common metadata and
changelog validation remain owned by the shared package implementation.
"""

import argparse
import sys
from pathlib import Path

from research_repo_tools import config, release_metadata, release_publishing

ROOT = Path(__file__).resolve().parents[1]


def check(root: Path, tag: str) -> int:
    """Require this project's canonical stable tag and complete release metadata."""
    try:
        package = release_metadata.read_package_info(root)
        if package.name != "research-repo-tools":
            raise ValueError("publication requires project.name = research-repo-tools")
        release_publishing.check_metadata(config.load(root=root), tag)
    except (OSError, ValueError) as error:
        print(f"Release preflight failed: {error}", file=sys.stderr)
        return 1
    print(f"Ready to validate distributions for {tag}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    return check(args.root.resolve(), args.tag)


if __name__ == "__main__":
    raise SystemExit(main())
