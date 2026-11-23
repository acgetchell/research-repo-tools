"""Retain research-repo-tools release evidence and the reviewed draft identity."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from research_repo_tools import release_assets
from research_repo_tools._console import console_text, write_stdout
from research_repo_tools._diagnostics import format_exception_diagnostics
from research_repo_tools.files import _publish_new_file
from research_repo_tools.process import run_command_bytes
from research_repo_tools.release_summary import render_distribution_summary
from research_repo_tools.release_validation import REPOSITORY, inspect_evidence, redact_release_diagnostic


def record_evidence(root: Path, issue: str) -> str:
    """Post the verified summary or return the URL of an identical retained comment."""
    if re.fullmatch(r"[1-9][0-9]*", issue) is None:
        raise ValueError("expected a positive release tracker issue number")
    result = inspect_evidence(root)
    proof = result["evidence_artifact"]
    summary = render_distribution_summary(result)
    body = f"### {result['tag']} release evidence\n\n```text\n{summary}Signed evidence artifact: {proof['id']}, {proof['digest']}\n```"
    pages = run_command_bytes("gh", ["api", "--paginate", "--slurp", f"repos/{REPOSITORY}/issues/{issue}/comments?per_page=100"], cwd=root)
    for page in json.loads(pages.stdout):
        for comment in page:
            match comment:
                case {"body": stored, "html_url": str(url)} if stored == body:
                    return url
    posted = run_command_bytes("gh", ["issue", "comment", issue, "--repo", REPOSITORY, "--body-file", "-"], input=body.encode("utf-8"), cwd=root)
    return posted.stdout.decode("utf-8").strip()


def capture_draft(root: Path, tag: str) -> Path:
    """Bind a create-only local target to the merged release PR and current mutable draft."""
    release_assets._stable_tag(tag)
    prs = run_command_bytes(
        "gh",
        [
            "pr",
            "list",
            "--repo",
            REPOSITORY,
            "--state",
            "merged",
            "--base",
            "main",
            "--head",
            f"release/{tag}",
            "--limit",
            "2",
            "--json",
            "isCrossRepository,mergeCommit",
        ],
        cwd=root,
    )
    match json.loads(prs.stdout):
        case [{"isCrossRepository": False, "mergeCommit": {"oid": commit}}]:
            target = release_assets.preflight_release_target(root, REPOSITORY, tag, commit, expected_title=tag)
        case _:
            raise ValueError(f"expected one merged release/{tag} PR from {REPOSITORY} into main")
    path = root.parent / "release-handoffs" / "research-repo-tools" / tag / "target.json"
    try:
        _publish_new_file(path, release_assets.serialize_release_target(target))
    except FileExistsError:
        if release_assets._read_target(path) != target:
            raise ValueError(f"retained target differs from the live draft at {path}; inspect the original handoff") from None
    return path


def main() -> int:
    """Run one maintainer handoff and print its comment URL or target path."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("draft").add_argument("tag")
    commands.add_parser("evidence").add_argument("issue")
    args = parser.parse_args()
    root = Path(os.environ.get("RRT_RELEASE_CHECKOUT", ".")).resolve()
    try:
        output = capture_draft(root, args.tag).as_posix() if args.command == "draft" else record_evidence(root, args.issue)
        write_stdout(f"{output}\n".encode("utf-8"))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(console_text(redact_release_diagnostic(format_exception_diagnostics(error)), sys.stderr), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
