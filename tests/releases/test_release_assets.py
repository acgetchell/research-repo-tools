"""Release assets must retain validated identity across draft and publication."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TAG = "v0.1.1"
COMMIT = "a" * 40
WHEEL = "research_repo_tools-0.1.1-py3-none-any.whl"
SDIST = "research_repo_tools-0.1.1.tar.gz"
BUNDLE = "release-attestation.json"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("release_assets", ROOT / "scripts/release_assets.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event():
    return {
        "action": "published",
        "repository": {"full_name": "acgetchell/research-repo-tools"},
        "release": {
            "id": 17,
            "tag_name": TAG,
            "draft": False,
            "prerelease": False,
            "assets": [{"id": i, "name": name} for i, name in enumerate((WHEEL, SDIST, BUNDLE), 1)],
        },
    }


@pytest.fixture
def downloads(script, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[:2] == ["gh", "api"]
        assert args[2].startswith("repos/acgetchell/research-repo-tools/releases/assets/")
        kwargs["stdout"].write(b"downloaded asset")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(script.subprocess, "run", run)
    return calls


def test_release_downloads_by_id_and_requires_each_signed_subject(script, tmp_path, monkeypatch, downloads):
    calls = []
    monkeypatch.setattr(script, "api", lambda endpoint: event()["release"])
    monkeypatch.setattr(script, "gh", lambda *args: calls.append(args) or "")
    output = tmp_path / "verified-dist"
    script.verify(event(), COMMIT, output)
    assert len(downloads) == 3
    assert {path.name for path in output.iterdir()} == {WHEEL, SDIST}
    assert (tmp_path / BUNDLE).read_bytes() == b"downloaded asset"
    assert len(calls) == 2
    for name, call in zip((WHEEL, SDIST), calls, strict=True):
        assert call[:3] == ("attestation", "verify", str(output / name))
        expected = {
            "--bundle": str(tmp_path / BUNDLE),
            "--repo": "acgetchell/research-repo-tools",
            "--signer-workflow": "acgetchell/research-repo-tools/.github/workflows/prepare-release.yml",
            "--signer-digest": COMMIT,
            "--source-digest": COMMIT,
            "--source-ref": "refs/tags/v0.1.1",
        }
        for flag, value in expected.items():
            assert call[call.index(flag) + 1] == value
        assert "--deny-self-hosted-runners" in call


@pytest.mark.parametrize("failed_subject", [WHEEL, SDIST])
def test_bad_signature_or_digest_fails_closed(script, tmp_path, monkeypatch, downloads, failed_subject):
    monkeypatch.setattr(script, "api", lambda endpoint: event()["release"])

    def gh(*args):
        if args[2].endswith(failed_subject):
            raise subprocess.CalledProcessError(1, args, stderr="attestation verification failed")
        return ""

    monkeypatch.setattr(script, "gh", gh)
    with pytest.raises(subprocess.CalledProcessError):
        script.verify(event(), COMMIT, tmp_path / "verified-dist")


@pytest.mark.parametrize("change", ["draft", "prerelease", "retagged", "missing", "extra", "duplicate", "unsafe-name", "invalid-id"])
def test_live_release_cannot_replace_event_identity_or_inventory(script, tmp_path, monkeypatch, change):
    current = copy.deepcopy(event()["release"])
    if change in {"draft", "prerelease"}:
        current[change] = True
    elif change == "retagged":
        current["tag_name"] = "v0.1.2"
    elif change == "missing":
        current["assets"].pop()
    elif change == "extra":
        current["assets"].append({"id": 4, "name": "extra.whl"})
    elif change == "duplicate":
        current["assets"][0] = current["assets"][1]
    elif change == "unsafe-name":
        current["assets"][0]["name"] = "../other.whl"
    else:
        current["assets"][0]["id"] = "../other"
    monkeypatch.setattr(script, "api", lambda endpoint: current)
    with pytest.raises(ValueError):
        script.verify(event(), COMMIT, tmp_path / "verified-dist")
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


@pytest.mark.parametrize("change", ["repository", "action", "draft", "prerelease", "tag", "release-id", "commit"])
def test_invalid_events_fail_before_remote_access(script, tmp_path, monkeypatch, change):
    payload = event()
    commit = COMMIT
    if change == "repository":
        payload["repository"]["full_name"] = "someone/else"
    elif change == "action":
        payload["action"] = "edited"
    elif change in {"draft", "prerelease"}:
        payload["release"][change] = True
    elif change == "tag":
        payload["release"]["tag_name"] = "v0.1.1-rc.1"
    elif change == "release-id":
        payload["release"]["id"] = True
    else:
        commit = "main"
    monkeypatch.setattr(script, "api", lambda endpoint: pytest.fail("unexpected API request"))
    with pytest.raises(ValueError):
        script.verify(payload, commit, tmp_path / "verified-dist")


@pytest.fixture
def staged_files(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in (WHEEL, SDIST):
        (dist / name).write_bytes(name.encode())
    bundle = tmp_path / "signed.json"
    bundle.write_bytes(b"signed bundle")
    return dist, bundle


@pytest.mark.parametrize("existing", [False, True])
def test_stage_creates_or_resumes_empty_draft_without_publishing(script, monkeypatch, staged_files, existing):
    dist, bundle = staged_files
    draft = {"tag_name": TAG, "draft": True, "prerelease": False, "assets": []}
    calls = []

    def gh(*args):
        calls.append(args)
        return json.dumps([[draft] if existing else []]) if args[0] == "api" else ""

    monkeypatch.setattr(script, "gh", gh)
    script.stage(TAG, dist, bundle)
    creations = [call for call in calls if call[:2] == ("release", "create")]
    assert len(creations) == (0 if existing else 1)
    if creations:
        assert "--draft" in creations[0] and "--verify-tag" in creations[0] and "--notes-from-tag" in creations[0]
        assert "--repo" not in creations[0]  # Incompatible with --notes-from-tag.
    upload = calls[-1]
    assert upload[:3] == ("release", "upload", TAG)
    assert "--clobber" not in upload
    assert set(upload[-3:]) == {str(dist / WHEEL), str(dist / SDIST), str(dist.parent / BUNDLE)}


@pytest.mark.parametrize("state", ["published", "prerelease", "partial-assets"])
def test_stage_refuses_to_overwrite_existing_release(script, monkeypatch, staged_files, state):
    dist, bundle = staged_files
    draft = {"tag_name": TAG, "draft": state != "published", "prerelease": state == "prerelease", "assets": []}
    if state == "partial-assets":
        draft["assets"] = [{"name": WHEEL}]
    calls = []
    monkeypatch.setattr(script, "gh", lambda *args: calls.append(args) or json.dumps([[draft]]))
    with pytest.raises(ValueError):
        script.stage(TAG, dist, bundle)
    assert len(calls) == 1 and calls[0][0] == "api"


def test_listing_error_does_not_create_release(script, monkeypatch, staged_files):
    def gh(*args):
        assert args[0] == "api"
        raise subprocess.CalledProcessError(1, args, stderr="unauthorized")

    monkeypatch.setattr(script, "gh", gh)
    with pytest.raises(subprocess.CalledProcessError):
        script.stage(TAG, *staged_files)


@pytest.mark.parametrize("defect", ["extra", "empty", "missing"])
def test_stage_validates_local_distribution_inventory(script, monkeypatch, staged_files, defect):
    dist, bundle = staged_files
    if defect == "extra":
        (dist / "other.whl").write_bytes(b"unexpected")
    elif defect == "empty":
        (dist / WHEEL).write_bytes(b"")
    else:
        (dist / SDIST).unlink()
    monkeypatch.setattr(script, "gh", lambda *args: pytest.fail("unexpected GitHub access"))
    with pytest.raises(ValueError):
        script.stage(TAG, dist, bundle)


def test_workflows_gate_signing_and_upload_on_validation():
    import yaml

    # BaseLoader treats GitHub's `on` as a string rather than YAML 1.1 boolean.
    prepare = yaml.load((ROOT / ".github/workflows/prepare-release.yml").read_text(), Loader=yaml.BaseLoader)
    publish = yaml.load((ROOT / ".github/workflows/publish.yml").read_text(), Loader=yaml.BaseLoader)
    assert prepare["on"] == {"push": {"tags": ["v*"]}}
    for job in [*prepare["jobs"].values(), *publish["jobs"].values()]:
        assert job.get("continue-on-error", "false") == "false"
        for step in job.get("steps", []):
            assert "if" not in step  # Every release guard and transfer must run after prior success.
            assert step.get("continue-on-error", "false") == "false"
    ancestor = 'git --no-pager merge-base --is-ancestor "$GITHUB_SHA" origin/main'
    unchanged_tag = 'test "$(git --no-pager rev-parse "$GITHUB_REF^{commit}")" = "$GITHUB_SHA"'
    preflight_runs = [step.get("run", "").strip() for step in prepare["jobs"]["preflight"]["steps"]]
    assert ancestor in preflight_runs
    assert prepare["jobs"]["validate"]["needs"] == "preflight"
    assert "if" not in prepare["jobs"]["validate"]
    stage = prepare["jobs"]["stage"]
    assert set(stage["needs"]) == {"preflight", "validate"}
    assert "if" not in stage  # Default success() must gate signature generation.
    assert "environment" not in stage
    assert prepare["jobs"]["validate"]["uses"] == "$/.github/workflows/ci.yml"
    steps = stage["steps"]
    download = next(step for step in steps if step.get("uses", "").startswith("actions/download-artifact@"))
    signature = next(step for step in steps if step.get("uses", "").startswith("actions/attest@"))
    upload = next(step for step in steps if "release_assets.py stage" in step.get("run", ""))
    assert steps.index(download) < steps.index(signature) < steps.index(upload)
    assert download["with"]["artifact-ids"] == "${{ needs.validate.outputs.artifact-id }}"
    assert publish["on"] == {"release": {"types": ["published"]}}
    verify = publish["jobs"]["verify"]
    assert "!github.event.release.prerelease" in verify["if"]
    assert "!github.event.release.draft" in verify["if"]
    assert "permissions" not in verify
    assert publish["permissions"] == {"contents": "read"}
    verify_runs = [step.get("run", "").strip() for step in verify["steps"]]
    identity = f"{ancestor}\n{unchanged_tag}"
    provenance = "python3 scripts/release_assets.py verify"
    assert identity in verify_runs and provenance in verify_runs
    transfer = next(index for index, step in enumerate(verify["steps"]) if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert verify_runs.index(identity) < verify_runs.index(provenance) < transfer
    final = publish["jobs"]["publish"]
    assert final["needs"] == "verify" and "if" not in final
    assert final["environment"]["name"] == "pypi"
    assert final["permissions"] == {"id-token": "write"}
    assert len(final["steps"]) == 2
    assert final["steps"][0]["with"]["artifact-ids"] == "${{ needs.verify.outputs.artifact-id }}"
    assert final["steps"][1]["uses"].startswith("pypa/gh-action-pypi-publish@")


def test_cli_reports_signature_failure_diagnostics(script, tmp_path, monkeypatch, capsys):
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps(event()))

    def fail(*args):
        raise subprocess.CalledProcessError(1, ["gh"], stderr=b"wrong source commit")

    monkeypatch.setattr(script, "verify", fail)
    monkeypatch.setattr(script.sys, "argv", ["release_assets.py", "verify", "--event", str(payload), "--commit", COMMIT])
    assert script.main() == 1
    assert "wrong source commit" in capsys.readouterr().err
