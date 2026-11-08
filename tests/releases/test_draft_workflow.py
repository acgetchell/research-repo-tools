"""Execute the documented writer against inert handoff/provider fixtures."""

from pathlib import Path

import pytest
import yaml

from research_repo_tools import release_drafts, release_interfaces, release_validation
from research_repo_tools.release_assets import ReleaseTarget, parse_release_target
from research_repo_tools.release_receipts import TagSource, retrieve_draft_receipt
from tests.releases import public_receipts_consumer

SHA = "a" * 40
REPOSITORY = "example/consumer"


def test_documented_receipt_policy_retrieves_the_example_target(tmp_path, monkeypatch):
    guide = Path(__file__).resolve().parents[2] / "docs/PREPARING_DRAFTS.md"
    text = guide.read_text(encoding="utf-8")
    source = text.split("```yaml\n", 1)[1].split("\n```", 1)[0]
    document = text.split("```toml\n", 1)[1].split("\n```", 1)[0]
    configuration = tmp_path / "receipt.toml"
    configuration.write_text(document.replace("OWNER/REPO", "example/package"), encoding="utf-8", newline="\n")
    policy = release_interfaces.load_receipt_policy(configuration)
    monkeypatch.setattr(public_receipts_consumer, "POLICY", policy)
    provider = public_receipts_consumer.Provider()
    provider.workflow_text = source.encode("utf-8")
    monkeypatch.setattr(release_validation.subprocess, "run", provider.execute)
    result = retrieve_draft_receipt(policy, TagSource("refs/tags/v1.2.3", SHA), provider.read)
    assert result.target == parse_release_target(result.target_bytes) == ReleaseTarget("example/package", "v1.2.3", 91, SHA, "v1.2.3")


@pytest.mark.parametrize(
    "scenario",
    (
        "first-run",
        "prior-run",
        "prior-run-next-page",
        "missing-current",
        "duplicate-run",
        "incomplete-history",
        "wrong-workflow",
        "artifact-digest",
        "attempt-changed-during-download",
    ),
)
def test_documented_writer_requires_original_handoff_and_first_tag_intent(tmp_path, monkeypatch, scenario):
    guide = Path(__file__).resolve().parents[2] / "docs/PREPARING_DRAFTS.md"
    workflow = yaml.safe_load(guide.read_text(encoding="utf-8").split("```yaml\n", 1)[1].split("\n```", 1)[0])
    command = workflow["jobs"]["prepare"]["steps"][1]["run"]
    source = command.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_RUN_ID": "101",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REF": "refs/tags/v1.2.3",
        "GITHUB_SHA": SHA,
        "RELEASE_TAG": "v1.2.3",
        "RELEASE_COMMIT": SHA,
        "HANDOFF_ID": "31",
        "HANDOFF_SHA256": "b" * 64,
    }.items():
        monkeypatch.setenv(name, value)
    current = {"id": 101, "workflow_id": 7, "run_attempt": 1, "event": "push", "head_branch": "v1.2.3", "head_sha": SHA}
    artifact = {
        "id": 31,
        "expired": False,
        "name": "draft-handoff-101-1",
        "digest": "sha256:" + "b" * 64,
        "workflow_run": {"id": 101, "head_sha": SHA},
    }
    history = [dict(current)]
    if scenario == "prior-run":
        history.append({**current, "id": 100})
    elif scenario == "prior-run-next-page":
        history.extend({**current, "id": index, "head_branch": "v0.1.0"} for index in range(1, 100))
        history.append({**current, "id": 100})
    elif scenario == "missing-current":
        history = []
    elif scenario == "duplicate-run":
        history.append(dict(current))
    elif scenario == "wrong-workflow":
        history[0]["workflow_id"] = 8
    elif scenario == "artifact-digest":
        artifact["digest"] = "sha256:" + "c" * 64
    calls = []

    def api(endpoint, *, repository):
        assert repository == REPOSITORY
        calls.append(endpoint)
        if endpoint == "actions/artifacts/31":
            return dict(artifact)
        if endpoint == "actions/runs/101":
            return dict(current)
        assert endpoint.startswith("actions/workflows/7/runs?per_page=100&page=")
        page = int(endpoint.rsplit("=", 1)[1])
        total = len(history) + (1 if scenario == "incomplete-history" else 0)
        return {"total_count": total, "workflow_runs": history[(page - 1) * 100 : page * 100]}

    def download(repository, selected, destination, names):
        assert repository == REPOSITORY and selected == artifact
        assert names == ("evidence.toml", "selection.json", "notes.md")
        destination.mkdir()
        for name in names:
            (destination / name).write_bytes(name.encode("ascii"))
        if scenario == "attempt-changed-during-download":
            current["run_attempt"] = 2

    prepared = []
    policy = object()

    def prepare(root, repository, tag, commit, **kwargs):
        assert root == tmp_path and repository == REPOSITORY and tag == "v1.2.3" and commit == SHA
        assert kwargs == {
            "policy": policy,
            "expected": b"selection.json",
            "title": "v1.2.3",
            "notes": b"notes.md",
            "receipt": tmp_path / "draft-receipt.json",
            "approve": True,
        }
        prepared.append(tag)
        return ReleaseTarget(repository, tag, 91, commit, tag)

    monkeypatch.setattr(release_validation, "api", api)
    monkeypatch.setattr(release_validation, "download_artifact", download)
    monkeypatch.setattr(release_interfaces, "load_evidence_policy", lambda path: policy)
    monkeypatch.setattr(release_drafts, "prepare_release_draft", prepare)
    if scenario == "first-run":
        exec(compile(source, str(guide), "exec"), {})
        assert prepared == ["v1.2.3"]
        assert (tmp_path / "target.json").is_file()
    else:
        with pytest.raises(ValueError):
            exec(compile(source, str(guide), "exec"), {})
        assert not prepared
        assert not (tmp_path / "draft-receipt.json").exists()
        assert not (tmp_path / "target.json").exists()
    if scenario == "prior-run-next-page":
        assert "actions/workflows/7/runs?per_page=100&page=2" in calls
