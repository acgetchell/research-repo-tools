"""Check raw Markdown line lengths using Unicode characters in every locale."""

import argparse
import sys
from pathlib import Path

from research_repo_tools.text_lines import inspect_lines

MAX_LINE_LENGTH = 160


def main(argv: list[str] | None = None) -> int:
    """Preserve the existing explicit-path Markdown policy and table exemption."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    failed = False
    for path in args.files:
        try:
            violations = inspect_lines(path, limit=MAX_LINE_LENGTH)
            # This established Markdown-only policy stays separate from the
            # generic all-line gate. Character counting has one implementation.
            with path.open(encoding="utf-8", newline=None) as source:
                tables = {number for number, line in enumerate(source, 1) if line.startswith("|")}
            for violation in violations:
                if violation.line in tables:
                    continue
                print(violation, file=sys.stderr)
                failed = True
        except (OSError, UnicodeError, ValueError) as error:
            print(f"{path}: {error}", file=sys.stderr)
            failed = True
    if failed:
        print("Markdown raw line-length check failed.", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
