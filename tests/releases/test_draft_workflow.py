"""Execute the documented writer against inert handoff/provider fixtures."""

import contextlib
import copy
import io
import os
import shlex
from pathlib import Path

import pytest
import yaml

from research_repo_tools import cli, release_interfaces, release_validation
from research_repo_tools.release_assets import ReleaseTarget, parse_release_target
from research_repo_tools.release_receipts import TagSource, retrieve_draft_receipt
from tests.releases import public_evidence_consumer, public_receipts_consumer
from tests.releases.public_evidence_consumer import TestDraftWorkflowShell as TestDraftWorkflowShell

SHA = "a" * 40


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
def test_documented_writer_requires_original_handoff_and_first_tag_intent(request, monkeypatch, scenario):
    guide = Path(__file__).resolve().parents[2] / "docs/PREPARING_DRAFTS.md"
    source = guide.read_text(encoding="utf-8").split("```yaml\n", 1)[1].split("\n```", 1)[0]
    workflow = yaml.safe_load(source)
    command = workflow["jobs"]["prepare"]["steps"][1]["run"]
    case = public_evidence_consumer.TestAuthenticatedDraftWorkflow()
    request.addfinalizer(case.doCleanups)
    case.setUp()
    provider = case.provider
    monkeypatch.chdir(case.writer)
    policy_file = case.writer / "handoff-policy.toml"
    policy_file.write_text(command.split("<<'TOML'\n", 1)[1].split("\nTOML", 1)[0].replace("OWNER/REPO", "example/package"), encoding="utf-8", newline="\n")
    policy = release_interfaces.load_receipt_policy(policy_file)
    provider.tag_workflow.update(path=policy.workflow, name=policy.workflow_name)
    provider.tag_run.update(path=policy.workflow, name=policy.workflow_name)
    provider.tag_jobs[0].update(
        name=policy.required_steps[0][0],
        steps=[{"name": step, "status": "completed", "conclusion": "success"} for step in policy.required_steps[0][1]],
        labels=["ubuntu-24.04"],
    )
    provider.workflow_text = source.encode("utf-8")
    for name, value in {
        "GITHUB_REPOSITORY": policy.repository,
        "GITHUB_RUN_ID": "101",
        "GITHUB_RUN_ATTEMPT": "1",
        "RELEASE_TAG": "v1.2.3",
        "RELEASE_COMMIT": public_evidence_consumer.COMMIT,
        "HANDOFF_ID": "301",
        "HANDOFF_SHA256": case.selected.sha256,
        "RUNNER_TEMP": str(case.writer),
    }.items():
        monkeypatch.setenv(name, value)
    current = provider.tag_run
    history = [copy.deepcopy(current)]
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
        provider.handoff_artifact["digest"] = "sha256:" + "c" * 64
    elif scenario == "attempt-changed-during-download":
        provider.after_handoff = lambda: current.update(run_attempt=2)
    provider.history = history
    original_read = provider.read

    def read(endpoint):
        result = original_read(endpoint)
        if scenario == "incomplete-history" and endpoint.startswith("actions/workflows/7/runs?"):
            result["total_count"] += 1
        return result

    monkeypatch.setattr(provider, "read", read)
    arguments = command.split("python -I -m research_repo_tools", 1)[1].replace("\\\n", "")
    args = [os.path.expandvars(value) for value in shlex.split(arguments)]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
        status = cli.main(["--root", str(case.writer), *args])
    state = case.writer / "draft-state"
    if scenario == "first-run":
        assert status == 0, errors.getvalue()
        assert len(provider.posts) == 1
        assert parse_release_target((state / "target.json").read_bytes()).release_id == 91
    else:
        assert status == 1
        assert provider.posts == []
        assert not (state / "draft-receipt.json").exists()
        assert not (state / "target.json").exists()
    if scenario == "prior-run-next-page":
        assert "actions/workflows/7/runs?per_page=100&page=2" in provider.requests
