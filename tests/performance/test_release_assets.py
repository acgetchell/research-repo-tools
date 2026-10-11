"""Inert release payloads and modeled authenticated GitHub failures/retries."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from research_repo_tools import release_assets as assets
from research_repo_tools.evidence import deterministic_json


def release(**kwargs) -> assets.GitHubRelease:
    return replace(assets.GitHubRelease("owner/repo", "v1.0.0", 42, True, False, False, ()), **kwargs)


def test_lookup_resolves_draft_by_database_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def gh(root, args):
        calls.append(args)
        if args[0] == "release":
            return deterministic_json({"databaseId": 42, "tagName": "v1.0.0"})
        if "--paginate" in args:
            return deterministic_json([[]])
        assert args == ["api", "repos/owner/repo/releases/42"]
        return deterministic_json(
            {
                "id": 42,
                "tag_name": "v1.0.0",
                "draft": True,
                "immutable": False,
                "prerelease": False,
                "assets": [],
                "name": None,
                "url": "https://api.github.com/repos/owner/repo/releases/42",
            }
        )

    monkeypatch.setattr(assets, "_gh", gh)
    assert assets.lookup_release(tmp_path, "owner/repo", "v1.0.0") == release()
    assert calls[0] == ["release", "view", "v1.0.0", "--repo", "owner/repo", "--json", "databaseId,tagName"]


def test_download_rejects_false_size_without_publishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assets, "lookup_release", lambda *args: release(draft=False, assets=(("baseline.tar.gz", 8, 5, None),)))
    monkeypatch.setattr(assets, "_download", lambda *args: b"changed payload")
    destination = tmp_path / "asset"
    with pytest.raises(ValueError, match="size differs"):
        assets.download_release_asset(tmp_path, "owner/repo", "v1.0.0", "baseline.tar.gz", destination)
    assert not destination.exists()


@pytest.mark.parametrize(
    "program,error",
    [
        ("import sys; sys.stdout.buffer.write(b'x'*11)", ValueError),
        ("import sys; print('partial'); raise SystemExit(7)", subprocess.CalledProcessError),
        ("import time; time.sleep(30)", subprocess.TimeoutExpired),
    ],
)
def test_download_transport_bounds_output_and_rejects_partial_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, program: str, error) -> None:
    popen = subprocess.Popen
    monkeypatch.setattr(assets, "resolve_executable", lambda *args, **kwargs: Path(sys.executable))
    monkeypatch.setattr(assets.subprocess, "Popen", lambda command, **kwargs: popen([sys.executable, "-c", program], **kwargs))
    # Size and exit-status cases must allow interpreter/coverage startup; only
    # the deliberate sleeping child should race a short deadline.
    timeout = 0.3 if error is subprocess.TimeoutExpired else 10
    with pytest.raises(error):
        assets._download(tmp_path, "owner/repo", 3, 10, timeout=timeout)
