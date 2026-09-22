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
        assert args == ["api", "repos/owner/repo/releases/42"]
        return deterministic_json({"id": 42, "tag_name": "v1.0.0", "draft": True, "immutable": False, "prerelease": False, "assets": []})

    monkeypatch.setattr(assets, "_gh", gh)
    assert assets.lookup_release(tmp_path, "owner/repo", "v1.0.0") == release()
    assert calls[0] == ["release", "view", "v1.0.0", "--repo", "owner/repo", "--json", "databaseId,tagName"]


@pytest.mark.parametrize("field,value", [("draft", False), ("immutable", True), ("prerelease", True)])
def test_invalid_release_state_prevents_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: bool) -> None:
    asset = tmp_path / "baseline.tar.gz"
    asset.write_bytes(b"evidence")
    monkeypatch.setattr(assets, "lookup_release", lambda *args: release(**{field: value}))
    monkeypatch.setattr(assets, "_gh", lambda *args: pytest.fail("unexpected write"))
    with pytest.raises(ValueError, match="mutable"):
        assets.publish_release_asset(tmp_path, "owner/repo", "v1.0.0", asset, publish=True)


@pytest.mark.parametrize("existing", [False, True])
def test_upload_or_identical_retry_publishes_only_after_verified_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool) -> None:
    asset = tmp_path / "baseline.tar.gz"
    asset.write_bytes(b"evidence\r\n")
    calls = []
    state = release(assets=((asset.name, 8, 10, None),) if existing else (("unrelated.txt", 9, 3, None),))
    monkeypatch.setattr(assets, "lookup_release", lambda *args: state)

    def gh(root, args):
        calls.append(args)
        return b"{}"

    def download(root, repository, tag, name, destination, **kwargs):
        calls.append(["download"])
        destination.write_bytes(asset.read_bytes())

    monkeypatch.setattr(assets, "_gh", gh)
    monkeypatch.setattr(assets, "download_release_asset", download)
    assets.publish_release_asset(tmp_path, "owner/repo", "v1.0.0", asset, publish=True)
    assert sum(call[:2] == ["release", "upload"] for call in calls) == int(not existing)
    assert calls[-2] == ["download"]
    assert calls[-1] == ["api", "--method", "PATCH", "repos/owner/repo/releases/42", "--field", "draft=false"]


def test_conflicting_retry_and_changed_draft_never_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asset = tmp_path / "baseline.tar.gz"
    asset.write_bytes(b"evidence")
    initial = release(assets=((asset.name, 8, 8, None),))
    monkeypatch.setattr(assets, "lookup_release", lambda *args: initial)
    monkeypatch.setattr(assets, "_gh", lambda *args: pytest.fail("unexpected release mutation"))
    monkeypatch.setattr(assets, "download_release_asset", lambda *args, **kwargs: args[4].write_bytes(b"different"))
    with pytest.raises(ValueError, match="differs"):
        assets.publish_release_asset(tmp_path, "owner/repo", "v1.0.0", asset, publish=True)
    states = iter((initial, replace(initial, immutable=True)))
    monkeypatch.setattr(assets, "lookup_release", lambda *args: next(states))
    monkeypatch.setattr(assets, "download_release_asset", lambda *args, **kwargs: args[4].write_bytes(b"evidence"))
    with pytest.raises(ValueError, match="mutable"):
        assets.publish_release_asset(tmp_path, "owner/repo", "v1.0.0", asset, publish=True)


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
    with pytest.raises(error):
        assets._download(tmp_path, "owner/repo", 3, 10, timeout=0.3)
