"""Execute the shared approval workflow against a fake GitHub API."""

import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/dependabot-approve.yml"
SHA = "a" * 40
DEPENDENCY = {
    "dependencyName": "pytest",
    "packageEcosystem": "uv",
    "updateType": "version-update:semver-patch",
    "prevVersion": "9.1.0",
    "newVersion": "9.1.1",
}
POLICY = {
    "uv": {"dependencies": ["pytest", "ruff"], "files": ["pyproject.toml", "uv.lock"]},
    "cargo": {"dependencies": ["serde"], "files": ["Cargo.toml", "Cargo.lock"]},
}
FAKE_GH = r"""
# Keep the Ubuntu workflow's LF transport when Git Bash invokes native jq.exe.
# jq 1.7 on POSIX accepts -b as a no-op but rejects the long --binary option.
jq() { native_jq -b "$@"; }

# Model platform-specific jq behavior without changing OS identity.
native_jq() {
  # Model native-process stdin forwarding consuming a script fed to Bash -s.
  if [[ "$SCENARIO" == stdin-reading-jq && " $* " == *" -rn "* ]]; then
    cat > /dev/null
  fi
  if [[ "$SCENARIO" == posix-jq && "$1" == --binary ]]; then
    echo 'jq: Unknown option --binary' >&2
    return 2
  fi
  if [[ "$SCENARIO" == windows-jq && "$1" != -b ]]; then
    # Emit CRLF with Bash builtins, without a platform-dependent text filter.
    command jq -b "$@" | while IFS= read -r line; do
      printf '%s\r\n' "$line"
    done
  else
    command jq "$@"
  fi
}

gh() {
  case "$*" in
    "api repos/owner/repo/pulls/1") cat "$TEST_DIR/pr.json" ;;
    "api repos/owner/repo/pulls/1 --jq "*)
      if [[ "$SCENARIO" == head-api-error ]]; then return 1; fi
      if [[ "$SCENARIO" == changed ]]; then
        jq -r '.head.sha = "changed" | [.head.sha, .base.sha, .base.ref, .base.repo.full_name, .state, .draft] | @json' "$TEST_DIR/pr.json"
      elif [[ "$SCENARIO" == retargeted ]]; then
        jq -r '.base.ref = "other" | [.head.sha, .base.sha, .base.ref, .base.repo.full_name, .state, .draft] | @json' "$TEST_DIR/pr.json"
      else
        jq -r '[.head.sha, .base.sha, .base.ref, .base.repo.full_name, .state, .draft] | @json' "$TEST_DIR/pr.json"
      fi ;;
    *"/rules/branches/main?per_page=100")
      if [[ "$SCENARIO" == rules-api-error ]]; then return 1; fi
      cat "$TEST_DIR/rules.json" ;;
    *"/commits?per_page=100")
      if [[ "$SCENARIO" == commits-api-error ]]; then return 1; fi
      cat "$TEST_DIR/commits.json" ;;
    *"/files?per_page=100")
      if [[ "$SCENARIO" == files-api-error ]]; then return 1; fi
      cat "$TEST_DIR/files.json" ;;
    *"/reviews?per_page=100")
      if [[ "$SCENARIO" == reviews-api-error ]]; then return 1; fi
      cat "$TEST_DIR/reviews.json" ;;
    "api --method POST "*)
      if [[ "$SCENARIO" == post-api-error ]]; then return 1; fi
      printf '%s\n' "$@" > "$TEST_DIR/review-request" ;;
    "pr merge "*)
      if [[ "$SCENARIO" == merge-api-error ]]; then return 1; fi
      printf '%s\n' "$*" > "$TEST_DIR/merge-request" ;;
    *) echo "Unexpected gh invocation: $*" >&2; return 99 ;;
  esac
}
"""


@pytest.fixture
def payloads():
    return {
        "pr": {
            "state": "open",
            "draft": False,
            "user": {"login": "dependabot[bot]"},
            "head": {"sha": SHA, "repo": {"full_name": "owner/repo"}},
            "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
            "commits": 1,
            "changed_files": 2,
        },
        "commits": [
            [
                {
                    "sha": SHA,
                    "author": {"login": "dependabot[bot]"},
                    "committer": {"login": "web-flow"},
                    "commit": {"verification": {"verified": True}},
                }
            ]
        ],
        "files": [[{"filename": "pyproject.toml", "status": "modified"}], [{"filename": "uv.lock", "status": "modified"}]],
        "rules": [
            [
                {
                    "type": "pull_request",
                    "parameters": {
                        "required_approving_review_count": 1,
                        "dismiss_stale_reviews_on_push": True,
                        "required_review_thread_resolution": True,
                    },
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "CI", "integration_id": 15368}],
                    },
                },
            ]
        ],
        "reviews": [[]],
    }


def run_script(tmp_path, script, scenario="fresh", extra_env=None):
    bash = shutil.which("bash")
    if os.name == "nt":
        # Select the Bash beside the required Git for Windows sh, not a WSL shim.
        sh = shutil.which("sh")
        bash = str(Path(sh).with_name("bash.exe")) if sh else None
    assert bash is not None and Path(bash).is_file(), "Install Bash (Git for Windows on Windows); see CONTRIBUTING.md."
    assert shutil.which("jq") is not None, "Install jq and add it to PATH; see CONTRIBUTING.md."
    script_path = tmp_path / "workflow-step.sh"
    script_path.write_text(FAKE_GH + script, encoding="utf-8", newline="\n")
    return subprocess.run(
        # Keep script bytes out of Windows argv and child-process stdin.
        [bash, "-euo", "pipefail", script_path.as_posix()],
        stdin=subprocess.DEVNULL,
        env=os.environ | {"TEST_DIR": tmp_path.as_posix(), "SCENARIO": scenario} | (extra_env or {}),
        capture_output=True,
        check=False,
        timeout=10,
    )


def run_step(tmp_path, payloads, dependencies=None, scenario="fresh", step=1):
    for name, value in payloads.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(value), encoding="utf-8", newline="\n")
    script = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["approve"]["steps"][step]["run"]
    result = run_script(
        tmp_path,
        script,
        scenario,
        extra_env={
            "GH_TOKEN": "test-token",
            "REPOSITORY": "owner/repo",
            "DEFAULT_BRANCH": "main",
            "PR_NUMBER": "1",
            "PR_HEAD_SHA": SHA,
            "DEPENDENCIES": json.dumps([DEPENDENCY] if dependencies is None else dependencies),
            "POLICY": json.dumps(POLICY),
            "GITHUB_OUTPUT": (tmp_path / "outputs").as_posix(),
        },
    )
    return subprocess.CompletedProcess(result.args, result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8"))


@pytest.mark.parametrize(
    "binary,substitute,expected",
    [
        (False, False, b"first\r\nsecond\r\n"),
        (True, False, b"first\nsecond\n"),
        (False, True, b"first\r\nsecond\r"),
        (True, True, b"first\nsecond"),
    ],
    ids=["crlf-output", "lf-output", "crlf-substitution", "lf-substitution"],
)
@pytest.mark.parametrize("text_filter_normalizes_crlf", [False, True], ids=["default-tools", "normalizing-filter"])
def test_jq_model_preserves_exact_bytes(tmp_path, monkeypatch, binary, substitute, expected, text_filter_normalizes_crlf):
    if text_filter_normalizes_crlf:
        # Model a platform text filter removing CR; the fixture must not rely on it.
        normalizing_filter = r"""sed() { command sed "$@" | tr -d '\r'; }""" + "\n"
        monkeypatch.setitem(globals(), "FAKE_GH", normalizing_filter + FAKE_GH)
    command = "native_jq " + ("-b " if binary else "") + '-nr \'"first", "second"\''
    script = f'value="$({command})"\nprintf "%s" "$value"\n' if substitute else command + "\n"
    result = run_script(tmp_path, script, scenario="windows-jq")
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    assert result.stdout == expected


@pytest.mark.parametrize("ecosystem", ["uv", "cargo"])
def test_patch_approval_is_bound_to_verified_head(tmp_path, payloads, ecosystem):
    dependency = dict(DEPENDENCY, packageEcosystem=ecosystem)
    if ecosystem == "cargo":
        dependency["dependencyName"] = "serde"
        payloads["files"] = [[{"filename": file, "status": "modified"} for file in ["Cargo.toml", "Cargo.lock"]]]
    result = run_step(tmp_path, payloads, [dependency])
    assert result.returncode == 0, result.stderr
    request = (tmp_path / "review-request").read_text(encoding="utf-8").splitlines()
    assert f"commit_id={SHA}" in request
    assert "event=APPROVE" in request
    assert (tmp_path / "outputs").read_text(encoding="utf-8") == "eligible=true\n"


def test_native_jq_cannot_consume_the_workflow_script(tmp_path, payloads):
    result = run_step(tmp_path, payloads, scenario="stdin-reading-jq")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "review-request").exists()
    assert (tmp_path / "outputs").read_bytes() == b"eligible=true\n"


@pytest.mark.parametrize(
    "scenario,broken_call,error",
    [
        ("posix-jq", 'native_jq --binary "$@"', "jq: Unknown option --binary"),
        ("windows-jq", 'native_jq "$@"', "Unexpected gh invocation:"),
    ],
)
def test_native_jq_models_preserve_workflow_decisions(tmp_path, payloads, monkeypatch, scenario, broken_call, error):
    # Prove each model reproduces its failure before using the portable adapter.
    script = FAKE_GH
    monkeypatch.setitem(globals(), "FAKE_GH", script.replace('native_jq -b "$@"', broken_call))
    failure = run_step(tmp_path, payloads, scenario=scenario)
    assert failure.returncode != 0
    assert error in failure.stderr
    assert not (tmp_path / "review-request").exists()
    monkeypatch.setitem(globals(), "FAKE_GH", script)
    result = run_step(tmp_path, payloads, scenario=scenario)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "review-request").exists()
    assert (tmp_path / "outputs").read_bytes() == b"eligible=true\n"


@pytest.mark.parametrize(
    "change",
    [
        {"updateType": "version-update:semver-minor"},
        {"updateType": "version-update:semver-major"},
        {"updateType": ""},
        {"dependencyName": "unlisted"},
        {"packageEcosystem": "github_actions"},
        {"prevVersion": ""},
        {"prevVersion": None},
        {"newVersion": ""},
        {"newVersion": "9.1.1rc1"},
        {"newVersion": "9.1.0"},
        {"prevVersion": "9.1.2"},
        {"newVersion": "10.1.1"},
        {"newVersion": "9.2.1"},
    ],
)
def test_one_ineligible_group_member_prevents_approval(tmp_path, payloads, change):
    result = run_step(tmp_path, payloads, [DEPENDENCY, dict(DEPENDENCY, **change)])
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "review-request").exists()
    assert not (tmp_path / "outputs").exists()


def test_empty_metadata_cannot_approve(tmp_path, payloads):
    result = run_step(tmp_path, payloads, [])
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "review-request").exists()


@pytest.mark.parametrize(
    "field,value", [("filename", ".github/workflows/ci.yml"), ("filename", "src/policy.py"), ("status", "renamed"), ("status", "added"), ("status", "removed")]
)
def test_unexpected_file_on_later_page_prevents_approval(tmp_path, payloads, field, value):
    payloads["files"][1][0][field] = value
    result = run_step(tmp_path, payloads)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "review-request").exists()


def test_truncated_file_list_cannot_approve(tmp_path, payloads):
    payloads["pr"]["changed_files"] = 3
    result = run_step(tmp_path, payloads)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "review-request").exists()


@pytest.mark.parametrize("case", ["human", "fork", "base", "closed", "draft", "stale", "unsigned", "human-commit", "wrong-commit"])
def test_untrusted_or_stale_pr_cannot_approve(tmp_path, payloads, case):
    pr = payloads["pr"]
    commit = payloads["commits"][0][0]
    if case == "human":
        pr["user"]["login"] = "owner"
    elif case == "fork":
        pr["head"]["repo"]["full_name"] = "intruder/repo"
    elif case == "base":
        pr["base"]["ref"] = "other"
    elif case == "closed":
        pr["state"] = "closed"
    elif case == "draft":
        pr["draft"] = True
    elif case == "stale":
        pr["head"]["sha"] = "stale"
    elif case == "unsigned":
        commit["commit"]["verification"]["verified"] = False
    elif case == "human-commit":
        commit["author"]["login"] = "owner"
    else:
        commit["sha"] = "stale"
    result = run_step(tmp_path, payloads)
    assert result.returncode != 0
    assert not (tmp_path / "review-request").exists()


def test_additional_commit_cannot_inherit_first_commit_metadata(tmp_path, payloads):
    payloads["pr"]["commits"] = 2
    payloads["commits"][0].append(deepcopy(payloads["commits"][0][0]))
    result = run_step(tmp_path, payloads)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "review-request").exists()


@pytest.mark.parametrize("committer", [{"login": "collaborator"}, {"login": "dependabot[bot]"}, None])
def test_verified_signature_requires_github_committer(tmp_path, payloads, committer):
    commit = payloads["commits"][0][0]
    assert commit["author"]["login"] == "dependabot[bot]"
    assert commit["commit"]["verification"]["verified"] is True
    commit["committer"] = committer
    result = run_step(tmp_path, payloads)
    assert result.returncode != 0
    assert not (tmp_path / "review-request").exists()
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize(
    "scenario", ["changed", "retargeted", "head-api-error", "rules-api-error", "commits-api-error", "files-api-error", "reviews-api-error", "post-api-error"]
)
def test_api_failure_or_changed_head_fails_closed(tmp_path, payloads, scenario):
    result = run_step(tmp_path, payloads, scenario=scenario)
    assert result.returncode != 0
    assert not (tmp_path / "review-request").exists()
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize(
    "state,sha,login,posts",
    [
        ("APPROVED", SHA, "github-actions[bot]", False),
        ("DISMISSED", SHA, "github-actions[bot]", True),
        ("APPROVED", "old", "github-actions[bot]", True),
        ("APPROVED", SHA, "intruder", True),
    ],
)
def test_only_current_bot_approval_suppresses_duplicate(tmp_path, payloads, state, sha, login, posts):
    payloads["reviews"] = [[{"id": 10, "commit_id": sha, "user": {"login": login}, "state": state}]]
    result = run_step(tmp_path, payloads)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "review-request").exists() is posts
    assert (tmp_path / "outputs").read_text(encoding="utf-8") == "eligible=true\n"


def test_auto_merge_uses_native_rules_and_head_guard(tmp_path, payloads):
    result = run_step(tmp_path, payloads, step=2)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "merge-request").read_text(encoding="utf-8") == f"pr merge 1 --repo owner/repo --auto --squash --match-head-commit {SHA}\n"


def test_auto_merge_api_errors_propagate(tmp_path, payloads):
    result = run_step(tmp_path, payloads, step=2, scenario="merge-api-error")
    assert result.returncode != 0
    assert not (tmp_path / "merge-request").exists()


def test_privileged_workflow_cannot_run_pr_code_or_merge_ineligible_changes():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["approve"]
    assert "github.event_name == 'pull_request_target'" in job["if"]
    actions = [step["uses"] for step in job["steps"] if "uses" in step]
    assert len(actions) == 1
    assert actions[0].startswith("dependabot/fetch-metadata@")
    assert len(actions[0].rsplit("@", 1)[-1]) == 40
    assert job["steps"][0]["with"]["skip-verification"] is False
    assert job["steps"][0]["with"]["skip-commit-verification"] is False
    assert job["steps"][2]["if"] == "steps.approve.outputs.eligible == 'true'"


@pytest.mark.parametrize(
    "rule,key,value",
    [
        (0, "required_approving_review_count", 0),
        (0, "dismiss_stale_reviews_on_push", False),
        (0, "required_review_thread_resolution", False),
        (1, "strict_required_status_checks_policy", False),
        (1, "required_status_checks", []),
    ],
)
def test_weakened_rules_prevent_approval(tmp_path, payloads, rule, key, value):
    payloads["rules"][0][rule]["parameters"][key] = value
    result = run_step(tmp_path, payloads)
    assert result.returncode != 0
    assert not (tmp_path / "review-request").exists()


def test_absent_active_rules_prevent_approval(tmp_path, payloads):
    payloads["rules"] = [[]]
    result = run_step(tmp_path, payloads)
    assert result.returncode != 0
    assert not (tmp_path / "review-request").exists()


def test_all_patch_group_can_be_approved(tmp_path, payloads):
    second = dict(DEPENDENCY, dependencyName="ruff", prevVersion="0.16.8", newVersion="0.16.9")
    result = run_step(tmp_path, payloads, [DEPENDENCY, second])
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "review-request").exists()
