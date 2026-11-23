"""Exercise the manual recovery recipe with local Git refs and a simulated GitHub/PyPI."""

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_repo_tools.just_inspect import dry_run
from research_repo_tools.process import run_git_bytes

ROOT = Path(__file__).resolve().parents[2]
TAG = "v0.3.2"
REF = f"refs/tags/{TAG}"


def git(root, *args):
    return run_git_bytes(list(args), cwd=root).stdout.decode().strip()


@pytest.fixture
def candidate(tmp_path, monkeypatch, git_mutations_allowed):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    recovery = importlib.import_module("release_recovery")
    root = tmp_path / "release café"
    bare = tmp_path / "origin.git"
    root.mkdir()
    git(tmp_path, "init", "--bare", str(bare))
    git(root, "init", "--initial-branch=main")
    for key, value in (("user.name", "Maintainer"), ("user.email", "test@example.org"), ("commit.gpgsign", "false"), ("tag.gpgSign", "false")):
        git(root, "config", key, value)
    git(root, "remote", "add", "origin", str(bare))
    (root / "pyproject.toml").write_bytes(
        b'[project]\nname="research-repo-tools"\nversion="0.3.2"\n'
        b'[tool.research-repo-tools.release]\ndate-policy="declared"\n'
        b'[tool.research-repo-tools.changelog]\nowner="acgetchell"\nrepository="research-repo-tools"\ndate-source="citation"\n'
    )
    (root / "CITATION.cff").write_bytes(b"version: 0.3.2\ndate-released: 2026-11-23\n")
    (root / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [0.3.2] - 2026-11-23\n\n- Original feature.\n")
    (root / "justfile").write_bytes(b'set positional-arguments\nchangelog-release tag:\n    research-repo-tools changelog generate --tag "$1"\n')
    git(root, "add", ".")
    git(root, "commit", "-m", "feat: original feature")
    git(root, "tag", "-a", TAG, "-m", "Original candidate")
    git(root, "push", "origin", REF)
    old = git(bare, "rev-parse", REF)
    state = {"frozen": True, "published": False, "draft": True}
    record = {
        "id": 73,
        "url": f"https://api.github.com/repos/{recovery.REPOSITORY}/releases/73",
        "tag_name": TAG,
        "name": TAG,
        "draft": True,
        "prerelease": False,
        "immutable": False,
        "published_at": None,
        "assets": [],
    }

    def api(endpoint):
        if endpoint == "actions/workflows/publish.yml":
            return {"state": "disabled_manually" if state["frozen"] else "active"}
        assert endpoint == f"git/ref/tags/{TAG}"
        return {"ref": REF, "object": {"type": "tag", "sha": git(bare, "rev-parse", REF)}}

    monkeypatch.setattr(recovery, "api", api)
    monkeypatch.setattr(recovery, "lookup_version", lambda *args: SimpleNamespace(present=state["published"]))
    monkeypatch.setattr(recovery.release_assets, "_release_matches", lambda *args: [record] if state["draft"] else [])
    monkeypatch.setattr(recovery, "_reviewed_head", lambda directory, tag, head: None)
    monkeypatch.setenv("PATH", os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]]))
    return recovery, root, bare, old, state


def test_recovery_tag_retry_and_leased_push(candidate, monkeypatch):
    recovery, root, bare, old, state = candidate
    state["frozen"] = False
    with pytest.raises(ValueError, match="freeze publication"):
        recovery.recover(root, "record", TAG)
    state.update(frozen=True, published=True)
    with pytest.raises(ValueError, match="published on PyPI"):
        recovery.recover(root, "record", TAG)
    state["published"] = False
    assert "draft 73" in recovery.recover(root, "record", TAG)
    assert "draft 73" in recovery.recover(root, "record", TAG)
    with pytest.raises(ValueError, match="superseded commit"):
        recovery.recover(root, "tag", TAG)
    git(root, "commit", "--allow-empty", "-m", "fix: corrective release")
    head = git(root, "rev-parse", "HEAD")
    assert head in recovery.recover(root, "tag", TAG)
    new = git(root, "rev-parse", REF)
    assert new != old and git(bare, "rev-parse", REF) == old
    assert git(root, "cat-file", "-t", new) == "tag" and git(root, "rev-parse", f"{REF}^{{commit}}") == head
    assert new in recovery.recover(root, "tag", TAG)
    with pytest.raises(ValueError, match="old draft still exists"):
        recovery.recover(root, "push", TAG)
    state["draft"] = False
    with pytest.raises(ValueError, match="single push URL"):
        recovery.recover(root, "push", TAG)

    def race_before_push(directory):
        git(bare, "update-ref", REF, git(bare, "rev-parse", f"{REF}^{{commit}}"), old)
        return bare.as_uri()

    monkeypatch.setattr(recovery, "_push_destination", race_before_push)
    with pytest.raises(subprocess.CalledProcessError):
        recovery.recover(root, "push", TAG)
    assert git(bare, "rev-parse", REF) not in {old, new}
    with pytest.raises(ValueError, match="remote tag changed"):
        recovery.recover(root, "push", TAG)
    git(bare, "update-ref", REF, old, git(bare, "rev-parse", REF))
    monkeypatch.setattr(recovery, "_push_destination", lambda directory: bare.as_uri())
    assert "Pushed" in recovery.recover(root, "push", TAG)
    assert git(bare, "rev-parse", REF) == new
    assert "already points" in recovery.recover(root, "push", TAG)
    path = root.parent / "release-handoffs" / "research-repo-tools" / TAG / old / "original.json"
    assert json.loads(path.read_bytes())["draft"]["id"] == 73


@pytest.mark.skipif(shutil.which("git-cliff") is None, reason="requires the native changelog generator")
def test_recovery_notes_include_original_and_committed_corrections(candidate):
    recovery, root, bare, old, _ = candidate
    assert 'scripts/release_recovery.py "$1" "$2"' in dry_run(ROOT, "release-recover", ["notes", TAG]).stderr
    (root / "fix.txt").write_bytes(b"corrected")
    before = (root / "CHANGELOG.md").read_bytes()
    with pytest.raises(ValueError, match="review and commit the release corrections"):
        recovery.recover(root, "notes", TAG)
    assert (root / "CHANGELOG.md").read_bytes() == before
    git(root, "add", "fix.txt")
    git(root, "commit", "-m", "fix: include committed correction")
    recovery.recover(root, "notes", TAG)
    notes = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert notes.count("## [0.3.2]") == 1 and "2026-11-23" in notes
    assert "original feature" in notes.lower() and "include committed correction" in notes.lower()
    assert git(root, "rev-parse", REF) == git(bare, "rev-parse", REF) == old
