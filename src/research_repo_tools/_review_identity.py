"""Read-only Git scope selection over the native path observation capability."""

import ntpath
import os
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools._git_checkout import READ_ONLY_OPTIONS
from research_repo_tools.evidence import deterministic_json, sha256
from research_repo_tools.path_identity import MissingPath, NativePathPolicy, PathInventory, PathObservation, fingerprint_paths, observe_paths
from research_repo_tools.process import run_git_bytes


@dataclass(frozen=True, slots=True)
class ReviewIdentity:
    worktree: Path
    digest: str
    head: str
    base_commit: str | None
    selected_paths: int
    branch: str | None = None


def _git(root: Path, args: list[str], env: dict[str, str]) -> bytes:
    return run_git_bytes([*READ_ONLY_OPTIONS, *args], cwd=root, env=env).stdout


def _records(data: bytes) -> list[bytes]:
    if data and not data.endswith(b"\0"):
        raise ValueError("Git returned an unterminated path inventory")
    return data[:-1].split(b"\0") if data else []


def _metadata(root: Path, base: str | None, env: dict[str, str]) -> dict[str, bytes]:
    values = {
        "head": _git(root, ["rev-parse", "--verify", "HEAD^{commit}"], env).strip(),
        "branch": _git(root, ["rev-parse", "--symbolic-full-name", "HEAD"], env),
        "index": _git(root, ["ls-files", "--stage", "-z"], env),
        "tree": _git(root, ["ls-tree", "-r", "-z", "HEAD"], env),
        "untracked": _git(root, ["ls-files", "--others", "--exclude-standard", "-z"], env),
        "config": _git(root, ["config", "--null", "--list", "--show-origin", "--show-scope"], env),
        "git_directory": _git(root, ["rev-parse", "--absolute-git-dir"], env),
    }
    if base is not None:
        # --base overrides this preference and CodeRabbit persists it itself.
        # Keep every other config record, including origin and scope, bound.
        records = _records(values["config"])
        if len(records) % 3:
            raise ValueError("Git returned malformed configuration records")
        values["config"] = b"".join(
            b"\0".join(records[index : index + 3]) + b"\0"
            for index in range(0, len(records), 3)
            if records[index + 2].partition(b"\n")[0] != b"coderabbit.basebranch"
        )
        values["base"] = _git(root, ["rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"], env).strip()
        values["merge_base"] = _git(root, ["merge-base", "--all", "HEAD", values["base"].decode("ascii")], env)
    return values


def capture(root: Path, base: str | None, instructions: list[str], *, provider: str, env: dict[str, str]) -> ReviewIdentity:
    """Conservatively bind the entire worktree, including staged deletions.

    Submodules and unmerged indexes are unsupported. Native symlink targets are
    observed without following them. This detects ordinary concurrent edits; it
    cannot freeze the checkout or CodeRabbit's later selection/upload.
    """
    raw_root = _git(root, ["rev-parse", "--show-toplevel"], env)
    if not raw_root.endswith(b"\n"):
        raise ValueError("Git returned an unterminated worktree path")
    raw_root = raw_root.removesuffix(b"\n")
    if os.name == "nt":
        raw_root = raw_root.removesuffix(b"\r")
    worktree = Path(os.fsdecode(raw_root)).resolve(strict=True)
    if not root.is_relative_to(worktree):
        raise ValueError("review root is outside the selected Git worktree")
    before = _metadata(worktree, base, env)
    paths = set(_records(before["untracked"]))
    for record in _records(before["index"]):
        header, separator, name = record.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3 or fields[0] not in {b"100644", b"100755", b"120000"} or fields[2] != b"0":
            raise ValueError("review identity requires a merged index without submodules or sparse-directory entries")
        paths.add(name)
    head_paths = set()
    for record in _records(before["tree"]):
        header, separator, name = record.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3 or fields[0] not in {b"100644", b"100755", b"120000"} or fields[1] != b"blob":
            raise ValueError("review identity does not support submodules")
        head_paths.add(name)
    paths.update(os.fsencode(Path(path).relative_to(worktree).as_posix()) for path in instructions)
    deleted = head_paths - paths
    if len(paths) + len(deleted) > 100000:
        raise ValueError("review inventory exceeds the path budget")
    current = observe_paths(worktree, sorted(paths))
    if current.policy is NativePathPolicy.WINDOWS:
        # Case-only renames have one native path. HEAD/index metadata still
        # binds both Git spellings; do not add an alias as a missing leaf.
        native_names = {ntpath.normcase(item.name.decode("utf-8")) for item in current.observations}
        deleted = {name for name in deleted if ntpath.normcase(name.decode("utf-8")) not in native_names}
    # HEAD-only names are staged deletions, even when a replacement now uses
    # that name as a directory or a file replaces one of its former parents.
    inventory = PathInventory(current.policy, (*current.observations, *(PathObservation(name, MissingPath()) for name in deleted)))
    observed = fingerprint_paths(inventory)
    if before != _metadata(worktree, base, env):
        raise ValueError("Git review selection changed during observation; stop writers and request a new review")
    # Hash configuration/environment values; never persist credentials or URLs.
    stable_env = {os.fsencode(name).hex(): os.fsencode(value).hex() for name, value in env.items() if name not in {"_", "SHLVL"}}
    digest = sha256(
        deterministic_json(
            {
                "schema": "research-repo-tools/review-identity/v1",
                "root": os.fsencode(root).hex(),
                "worktree": os.fsencode(worktree).hex(),
                "metadata": {name: sha256(value) for name, value in before.items()},
                "paths": observed,
                "scope": ["branch", os.fsencode(base).hex()] if base is not None else ["uncommitted"],
                "options": [os.fsencode(option).hex() for option in ["--agent", "--include-untracked", "--config", *instructions]],
                "provider": provider,
                "environment": sha256(deterministic_json(stable_env)),
            }
        )
    )
    branch = os.fsdecode(before["branch"].removesuffix(b"\n"))
    return ReviewIdentity(
        worktree, digest, before["head"].decode("ascii"), before.get("base", b"").decode("ascii") or None, len(inventory.observations), branch or None
    )
