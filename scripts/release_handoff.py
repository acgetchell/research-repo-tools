"""Retain research-repo-tools release evidence and the reviewed draft identity."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from typing import Any

from research_repo_tools import release_assets, release_validation
from research_repo_tools._console import console_text, write_stdout
from research_repo_tools._diagnostics import format_exception_diagnostics
from research_repo_tools._git_checkout import READ_ONLY_OPTIONS, git_context
from research_repo_tools.files import _publish_new_file
from research_repo_tools.process import run_command_bytes
from research_repo_tools.release_summary import render_distribution_summary
from research_repo_tools.release_validation import REPOSITORY, api, redact_release_diagnostic


def _handoff_directory(root: Path, tag: str) -> Path:
    return root.parent / "release-handoffs" / "research-repo-tools" / release_assets._stable_tag(tag)


def _inspect_release(root: Path) -> dict[str, Any]:
    """Verify the exact pushed tag, including from a clean descendant checkout."""
    tag, head = release_validation._checkout_identity(root)
    commit = release_assets._resolve_tag_commit(tag, release_validation.api)
    if commit != head:
        if _merged_release_commit(root, tag) != commit:
            raise ValueError("remote release tag differs from the reviewed release commit")
        git, environment = git_context(root)
        ancestry = subprocess.run(
            [git, *READ_ONLY_OPTIONS, "merge-base", "--is-ancestor", commit, head], cwd=root, env=environment, capture_output=True, timeout=30
        )
        if ancestry.returncode != 0:
            raise ValueError("cannot establish that the remote release tag is an ancestor of the clean checkout")
    with TemporaryDirectory() as temporary:
        proof = release_validation._retrieve_evidence(tag, commit, Path(temporary) / "dist", attempts=1, interval=0)
    if release_validation._checkout_identity(root) != (tag, head):
        raise ValueError("checkout changed during evidence inspection; retry from the reviewed release commit")
    return {**release_validation._json(proof["manifest"]), "tag": tag, "evidence_artifact": proof["evidence_artifact"]}


def _tracker(root: Path, tag: str) -> str | None:
    """Select only a unique open issue with the repository's release tracker title."""
    pages = run_command_bytes("gh", ["api", "--paginate", "--slurp", f"repos/{REPOSITORY}/issues?state=open&per_page=100"], cwd=root)
    title = f"release: validate and publish research-repo-tools {tag}"
    matches = [issue["number"] for page in json.loads(pages.stdout) for issue in page if "pull_request" not in issue and issue["title"] == title]
    if len(matches) > 1:
        raise ValueError(f"multiple open release trackers match {tag}")
    return str(matches[0]) if matches else None


def _post_summary(root: Path, issue: str, body: str) -> str:
    """Post the summary or return the URL of its existing comment."""
    pages = run_command_bytes("gh", ["api", "--paginate", "--slurp", f"repos/{REPOSITORY}/issues/{issue}/comments?per_page=100"], cwd=root)
    for page in json.loads(pages.stdout):
        for comment in page:
            match comment:
                case {"body": stored, "html_url": str(url)} if stored == body:
                    return url
    posted = run_command_bytes("gh", ["issue", "comment", issue, "--repo", REPOSITORY, "--body-file", "-"], input=body.encode("utf-8"), cwd=root)
    return posted.stdout.decode("utf-8").strip()


def record_evidence(root: Path, issue: str | None = None) -> str:
    """Retain verified evidence locally and optionally mirror it to a release issue."""
    if issue is not None and re.fullmatch(r"[1-9][0-9]*", issue) is None:
        raise ValueError("expected a positive release tracker issue number")
    result = _inspect_release(root)
    tag, run, proof = result["tag"], result["run"], result["evidence_artifact"]
    summary = render_distribution_summary(result) + f"Signed evidence artifact: {proof['id']}, {proof['digest']}\n"
    body = f"### {tag} release evidence\n\n```text\n{summary}```"
    path = _handoff_directory(root, tag) / f"evidence-{run['id']}-{run['run_attempt']}.md"
    payload = (body + "\n").encode("utf-8")
    try:
        _publish_new_file(path, payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError(f"retained evidence differs for the same run and attempt: {path}") from None
    if issue is not None:
        return _post_summary(root, issue, body)
    receipt = f"{summary}Saved evidence: {path}"
    try:
        tracker = _tracker(root, tag)
        if tracker is not None:
            receipt += f"\nRelease tracker: {_post_summary(root, tracker, body)}"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        diagnostic = redact_release_diagnostic(format_exception_diagnostics(error))
        print(console_text(f"Optional issue handoff incomplete: {diagnostic}", sys.stderr), file=sys.stderr)
    return receipt


def _merged_release_commit(root: Path, tag: str) -> str:
    """Resolve the reviewed commit for the release branch in this repository."""
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
            return release_assets._sha(commit)
        case _:
            raise ValueError(f"expected one merged release/{tag} PR from {REPOSITORY} into main")


def _wait_for_preparation(tag: str, commit: str) -> None:
    """Wait for the unique tag-push preparation run before accepting its draft."""
    endpoint = f"actions/workflows/prepare-release.yml/runs?event=push&branch={tag}&head_sha={commit}&per_page=2"
    visible_by = monotonic() + 120
    run_id = None
    print(f"Checking GitHub draft preparation for {tag} at {commit[:7]}...", file=sys.stderr)
    while True:
        result = api(endpoint)
        runs = result["workflow_runs"]
        if run_id is None and result["total_count"] == 0 and runs == []:
            if monotonic() >= visible_by:
                raise ValueError(f"GitHub has not listed preparation for {tag} at {commit}; verify the tag push and retry just release-draft {tag}")
            sleep(10)
            continue
        if result["total_count"] != 1 or len(runs) != 1:
            raise ValueError(f"expected one tag-push preparation run for {tag} at {commit}; inspect the workflow history")
        run = runs[0]
        if (run["path"], run["event"], run["head_branch"], run["head_sha"]) != (".github/workflows/prepare-release.yml", "push", tag, commit):
            raise ValueError("draft preparation run differs from the expected workflow, tag or commit")
        identifier = run["id"]
        if type(identifier) is not int or identifier <= 0 or (run_id is not None and identifier != run_id):
            raise ValueError("draft preparation run identity is invalid or changed while waiting")
        url = f"https://github.com/{REPOSITORY}/actions/runs/{identifier}"
        if run["status"] == "completed":
            if run["conclusion"] != "success":
                raise ValueError(f"draft preparation ended with {run['conclusion']}: {url}")
            print(f"Draft preparation passed: {url}", file=sys.stderr)
            return
        if run["status"] not in {"requested", "queued", "pending", "waiting", "in_progress"}:
            raise ValueError(f"unexpected draft preparation status {run['status']}: {url}")
        if run_id is None:
            print(f"Waiting for draft preparation: {url}", file=sys.stderr)
        run_id = identifier
        sleep(15)


def capture_draft(root: Path, tag: str) -> Path:
    """Wait for the reviewed release's preparation, then capture or revalidate its draft."""
    commit = _merged_release_commit(root, tag)
    _wait_for_preparation(tag, commit)
    target = release_assets.preflight_release_target(root, REPOSITORY, tag, commit, expected_title=tag)
    path = _handoff_directory(root, tag) / "target.json"
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
    commands.add_parser("evidence").add_argument("issue", nargs="?")
    commands.add_parser("target-path").add_argument("tag")
    args = parser.parse_args()
    root = Path(os.environ.get("RRT_RELEASE_CHECKOUT", ".")).resolve()
    try:
        if args.command == "draft":
            output = capture_draft(root, args.tag).as_posix()
        elif args.command == "target-path":
            output = (_handoff_directory(root, args.tag) / "target.json").as_posix()
        else:
            output = record_evidence(root, args.issue)
        write_stdout(f"{output}\n".encode("utf-8"))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(console_text(redact_release_diagnostic(format_exception_diagnostics(error)), sys.stderr), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
