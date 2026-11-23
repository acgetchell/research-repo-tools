"""Manual steps for replacing an unpublished research-repo-tools candidate."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from release_handoff import _handoff_directory, _require_merged_release

from research_repo_tools import changelog, config, release_assets, release_validation
from research_repo_tools._console import console_text, write_stdout
from research_repo_tools._diagnostics import format_exception_diagnostics
from research_repo_tools._git_checkout import READ_ONLY_OPTIONS, git_context
from research_repo_tools.files import _publish_new_file
from research_repo_tools.process import run_command_bytes
from research_repo_tools.registry import lookup_version
from research_repo_tools.release_validation import REPOSITORY, api, redact_release_diagnostic


def _git(root: Path, *args: str) -> str:
    git, env = git_context(root)
    return run_command_bytes(git, [*READ_ONLY_OPTIONS, *args], cwd=root, env=env).stdout.decode("utf-8").removesuffix("\n")


def _remote_tag(ref: str) -> str:
    match api(f"git/ref/{ref.removeprefix('refs/')}"):
        case {"ref": name, "object": {"type": "tag", "sha": oid}} if name == ref:
            return release_assets._sha(oid)
        case _:
            raise ValueError(f"expected an annotated GitHub tag at {ref}")


def _require_freeze(tag: str) -> None:
    if api("actions/workflows/publish.yml").get("state") != "disabled_manually":
        raise ValueError("freeze publication before replacing the candidate: publish.yml must be disabled")
    if lookup_version("pypi", "research-repo-tools", tag.removeprefix("v")).present:
        raise ValueError(f"{tag} is published on PyPI; choose a new version")


def _draft(root: Path, tag: str) -> dict[str, Any] | None:
    matches = release_assets._release_matches(root, REPOSITORY, tag)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"ambiguous GitHub releases for {tag}")
    entry = matches[0]
    match entry:
        case {"id": int(identifier), "published_at": None}:
            draft = release_assets._parse_release(entry, REPOSITORY, tag, identifier)
            release_assets.require_draft(draft)
            return {
                "id": identifier,
                "assets": [list(asset) for asset in sorted(draft.assets)],
                "states": [list(state) for state in sorted(draft.asset_states)],
            }
        case _:
            raise ValueError(f"{tag} already has a published or malformed GitHub release")


def _save(path: Path, value: dict[str, Any]) -> None:
    contents = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
    try:
        _publish_new_file(path, contents)
    except FileExistsError:
        if path.read_bytes() != contents:
            raise ValueError(f"recovery record differs from {path}; inspect the original candidate") from None


def _load(path: Path, required_step: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise ValueError(f"missing recovery record {path}; complete the {required_step} step first") from None
    if not isinstance(value, dict):
        raise ValueError(f"invalid recovery record at {path}")
    return value


def _record(root: Path, tag: str, ref: str, old: str, path: Path) -> str:
    if _git(root, "rev-parse", "--verify", ref) != old or _git(root, "cat-file", "-t", old) != "tag":
        raise ValueError("local and remote annotated tag objects differ; inspect the original candidate")
    commit = release_assets._sha(_git(root, "rev-parse", f"{old}^{{commit}}"))
    draft = _draft(root, tag)
    _save(path, {"repository": REPOSITORY, "tag": tag, "object": old, "commit": commit, "draft": draft})
    return f"Original {tag} at {commit}, draft {draft['id'] if draft else 'none'}. Saved {path}"


def _reviewed_head(root: Path, tag: str, head: str) -> None:
    _require_merged_release(root, tag, head)
    policy = release_validation.POLICY
    run = release_validation.select_main_run(policy, head, api, attempts=1, interval=0)
    release_validation.validate_jobs(policy, run, release_validation._jobs(run))


def _push_destination(root: Path) -> str:
    destination = _git(root, "remote", "get-url", "--push", "--all", "origin")
    if destination.removesuffix(".git") not in {f"https://github.com/{REPOSITORY}", f"git@github.com:{REPOSITORY}", f"ssh://git@github.com/{REPOSITORY}"}:
        raise ValueError(f"origin must have a single push URL for github.com/{REPOSITORY}")
    return destination


def _push(root: Path, tag: str, ref: str, head: str, plan: Path) -> str:
    saved = _load(plan, "local tag")
    if (saved["tag"], saved["commit"], saved["repository"]) != (tag, head, REPOSITORY):
        raise ValueError("replacement record differs from this release and checkout")
    old, new = release_assets._sha(saved["old"]), release_assets._sha(saved["new"])
    if old == new or (_git(root, "rev-parse", ref), _git(root, "cat-file", "-t", new), _git(root, "rev-parse", f"{new}^{{commit}}")) != (new, "tag", head):
        raise ValueError("local tag differs from the reviewed replacement")
    remote = _remote_tag(ref)
    if remote == new:
        return f"{tag} already points to the reviewed replacement on GitHub."
    if remote != old:
        raise ValueError("remote tag changed since recording; inspect it before proceeding")
    if _draft(root, tag) is not None:
        raise ValueError("the old draft still exists; review and delete the recorded draft before pushing")
    destination = _push_destination(root)
    release_assets.require_release_checkout(root, head)
    _git(root, "push", "--no-follow-tags", "--recurse-submodules=no", f"--force-with-lease={ref}:{old}", destination, f"{new}:{ref}")
    if _remote_tag(ref) != new:
        raise ValueError("could not confirm the pushed tag; inspect GitHub before retrying")
    return f"Pushed {tag} at {head}. Continue with just release-evidence and just release-draft {tag}."


def recover(root: Path, action: str, tag: str) -> str:
    directory = _handoff_directory(root, tag)
    ref = f"refs/tags/{tag}"
    if action == "notes":
        head = _git(root, "rev-parse", "HEAD")
        try:
            release_assets.require_release_checkout(root, head)
        except ValueError as error:
            raise ValueError(
                f"Recovery notes use committed history. Inspect git status, review and commit the release corrections, "
                f"then rerun just release-recover notes {tag}. Checkout check: {error}"
            ) from error
        if _git(root, "cat-file", "-t", ref) != "tag":
            raise ValueError("expected the original annotated tag before regenerating notes")
        env = {**os.environ, "GIT_CLIFF_IGNORE_TAGS": f"^{re.escape(tag)}$"}
        run_command_bytes("just", ["changelog-release", tag], cwd=root, env=env)
        return f"Generated {tag} notes from committed history. Review the changelog diff."

    _require_freeze(tag)
    if action == "push":
        head = release_assets._sha(_git(root, "rev-parse", "HEAD"))
        release_assets.require_release_checkout(root, head)
        return _push(root, tag, ref, head, directory / head / "replacement.json")

    old = _remote_tag(ref)
    path = directory / old / "original.json"
    if action == "record":
        return _record(root, tag, ref, old, path)
    original = _load(path, "record")
    if (original["tag"], original["object"], original["repository"]) != (tag, old, REPOSITORY):
        raise ValueError("original recovery record differs from the remote tag")
    head = release_assets._sha(_git(root, "rev-parse", "HEAD"))
    release_assets.require_release_checkout(root, head)
    if head == original["commit"]:
        raise ValueError("checkout still points to the superseded commit; wait for the corrective PR to merge")
    _reviewed_head(root, tag, head)
    draft = _draft(root, tag)
    if draft is not None and draft != original["draft"]:
        raise ValueError("draft identity or assets changed since recording; inspect the original handoff")
    current = _git(root, "rev-parse", ref)
    plan = directory / head / "replacement.json"
    if plan.exists() and _load(plan, "local tag") != {"repository": REPOSITORY, "tag": tag, "old": old, "new": current, "commit": head}:
        raise ValueError("local or remote tag differs from the recorded replacement; inspect the handoff")
    if current != old and (_git(root, "cat-file", "-t", current), _git(root, "rev-parse", f"{current}^{{commit}}")) != ("tag", head):
        raise ValueError("unexpected local tag; inspect it before retrying")
    _, notes = changelog.tag_with_preflight(config.load(root=root), tag, commit=head, force=True, dry_run=current != old)
    new = release_assets._sha(_git(root, "rev-parse", ref))
    if _git(root, "cat-file", "-t", new) != "tag" or _git(root, "rev-parse", f"{new}^{{commit}}") != head:
        raise ValueError("local replacement tag differs from the corrected commit")
    annotation = _git(root, "for-each-ref", "--format=%(contents)", ref)
    signature = _git(root, "for-each-ref", "--format=%(contents:signature)", ref)
    if annotation.removesuffix(signature).rstrip("\n") != notes.rstrip("\n"):
        raise ValueError("local tag annotation differs from the reviewed release notes")
    _save(plan, {"repository": REPOSITORY, "tag": tag, "old": old, "new": new, "commit": head})
    return f"{notes}\nLocal {tag} at {head}, object {new}. Old draft ID: {original['draft']['id'] if original['draft'] else 'none'}."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "notes", "tag", "push"))
    parser.add_argument("tag")
    args = parser.parse_args()
    try:
        output = recover(Path.cwd().resolve(), args.action, args.tag)
        write_stdout(f"{output}\n".encode("utf-8"))
    except (KeyError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(console_text(redact_release_diagnostic(format_exception_diagnostics(error)), sys.stderr), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
