"""Publication evidence rejects incomplete/ambiguous state without live uploads."""

import copy
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from research_repo_tools import changelog, cli, config
from research_repo_tools import release_assets as assets
from research_repo_tools import release_publishing as publishing
from research_repo_tools.release_assets import GitHubRelease

SHA = "a" * 40
CHECKS = (publishing.RequiredCheck("native package", 15368),)


def event():
    return {
        "action": "published",
        "repository": {"full_name": "example/project"},
        "release": {"id": 9, "tag_name": "v1.2.3", "draft": False, "prerelease": False},
    }


def pages():
    return [
        {
            "total_count": 1,
            "check_runs": [{"id": 7, "name": "native package", "app": {"id": 15368}, "head_sha": SHA, "status": "completed", "conclusion": "success"}],
        }
    ]


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="sample"\nversion="1.2.3"\n', encoding="utf-8", newline="\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Authored notes.\n", encoding="utf-8", newline="\n")
    settings = config.parse(
        {
            "publishing": {
                "registry": "pypi",
                "package": "sample",
                "repository": "example/project",
                "required-checks": [{"name": "native package", "app-id": 15368}],
                "required-assets": ["sample-{version}.tgz"],
            }
        },
        root=tmp_path,
    )
    monkeypatch.setattr(publishing, "run_git_command", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "" if "status" in args else SHA + "\n", ""))
    evidence = {
        "": {"full_name": "example/project", "default_branch": "main"},
        "git/ref/tags/v1.2.3": {"ref": "refs/tags/v1.2.3", "object": {"type": "tag", "sha": "b" * 40}},
        "git/tags/" + "b" * 40: {"sha": "b" * 40, "object": {"type": "commit", "sha": SHA}},
        "branches/main": {"name": "main", "protected": True},
        f"compare/{SHA}...main": {"status": "ahead"},
        f"commits/{SHA}/check-runs?filter=latest&per_page=100": pages(),
    }
    monkeypatch.setattr(publishing, "_api", lambda root, repo, endpoint, **kwargs: evidence[endpoint])
    monkeypatch.setattr(assets, "_gh", lambda root, args: json.dumps(evidence[args[-1].removeprefix("repos/example/project/")]).encode())
    release = GitHubRelease("example/project", "v1.2.3", 9, False, False, False, (("sample-1.2.3.tgz", 3, 42, None),))
    monkeypatch.setattr(publishing, "lookup_release", lambda *args: release)
    return settings, evidence, release


def test_gate_reuses_metadata_and_binds_annotated_tag_to_event(consumer):
    settings, _, release = consumer
    before = {path.name: path.read_bytes() for path in settings.root.iterdir()}
    reviewed = publishing.validate_event(event(), "example/project", SHA, "refs/tags/v1.2.3")
    assert publishing.check_reviewed_release(settings, "v1.2.3", event=reviewed) == release
    assert {path.name: path.read_bytes() for path in settings.root.iterdir()} == before


@pytest.mark.parametrize("field,value", [("action", "created"), ("repository", {}), ("release", {})])
def test_event_rejects_wrong_trigger_or_identity(field, value):
    data = event()
    data[field] = value
    with pytest.raises(ValueError):
        publishing.validate_event(data, "example/project", SHA, "refs/tags/v1.2.3")


@pytest.mark.parametrize(
    "field,value", [("draft", True), ("prerelease", True), ("id", True), ("tag_name", "v1.2.3-rc.1"), ("tag_name", "1.2.3"), ("tag_name", "v1.2.3+build")]
)
def test_event_rejects_unpublishable_release(field, value):
    data = event()
    data["release"][field] = value
    with pytest.raises(ValueError):
        publishing.validate_event(data, "example/project", SHA, "refs/tags/v1.2.3")


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("conclusion", "skipped"),
        ("conclusion", None),
        ("head_sha", "b" * 40),
        ("app", {"id": 8}),
        ("name", "different"),
    ],
)
def test_required_checks_fail_closed(field, value):
    data = pages()
    data[0]["check_runs"][0][field] = value
    with pytest.raises(ValueError):
        publishing.require_checks(data, SHA, CHECKS)


@pytest.mark.parametrize("provider", [15368.0, True, 0, None])
def test_checks_require_integer_provider_identity(provider):
    data = pages()
    data[0]["check_runs"][0]["app"]["id"] = provider
    checks = (publishing.RequiredCheck("native package", 1 if provider is True else 15368),)
    with pytest.raises(ValueError):
        publishing.require_checks(data, SHA, checks)


@pytest.mark.parametrize(
    "payload",
    [
        '{"status":"diverged","status":"ahead"}',
        '{"status":"ahead","extra":Infinity}',
        "[" * (sys.getrecursionlimit() + 100) + "]" * (sys.getrecursionlimit() + 100),
    ],
    ids=["duplicate", "nonfinite", "deep"],
)
def test_github_evidence_rejects_ambiguous_nonfinite_or_deep_json(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(publishing, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, payload, ""))
    with pytest.raises(ValueError):
        publishing._api(tmp_path, "example/project", "compare")


@pytest.mark.parametrize("damage", ["missing", "truncated", "duplicate-id", "duplicate-name", "changing-count", "malformed"])
def test_checks_reject_absent_ambiguous_or_incomplete_pages(damage):
    data = pages()
    if damage == "missing":
        data = [{"total_count": 0, "check_runs": []}]
    elif damage == "truncated":
        data[0]["total_count"] = 2
    elif damage in {"duplicate-id", "duplicate-name"}:
        other = copy.deepcopy(data[0]["check_runs"][0])
        if damage == "duplicate-name":
            other["id"] += 1
        data[0]["total_count"] = 2
        data[0]["check_runs"].append(other)
    elif damage == "changing-count":
        data.append({"total_count": 2, "check_runs": []})
    else:
        data = [{}]
    with pytest.raises(ValueError):
        publishing.require_checks(data, SHA, CHECKS)


def test_complete_paginated_checks_match_each_required_provider():
    first = pages()[0]["check_runs"][0]
    second = {**first, "id": 8, "name": "package install", "app": {"id": 27}}
    checks = (*CHECKS, publishing.RequiredCheck("package install", 27))
    publishing.require_checks([{"total_count": 2, "check_runs": [first]}, {"total_count": 2, "check_runs": [second]}], SHA, checks)
    second["app"]["id"] = 15368
    with pytest.raises(ValueError, match="package install"):
        publishing.require_checks([{"total_count": 2, "check_runs": [first]}, {"total_count": 2, "check_runs": [second]}], SHA, checks)


@pytest.mark.parametrize("damage", ["tag", "unprotected", "unrelated", "wrong-repository", "missing-checks"])
def test_remote_evidence_rejects_wrong_identity_and_policy(consumer, damage):
    settings, evidence, _ = consumer
    if damage == "tag":
        evidence["git/tags/" + "b" * 40]["object"]["sha"] = "c" * 40
    elif damage == "unprotected":
        evidence["branches/main"]["protected"] = False
    elif damage == "unrelated":
        evidence[f"compare/{SHA}...main"]["status"] = "diverged"
    elif damage == "wrong-repository":
        evidence[""]["full_name"] = "other/project"
    else:
        evidence[f"commits/{SHA}/check-runs?filter=latest&per_page=100"] = []
    with pytest.raises(ValueError):
        publishing.check_reviewed_release(settings, "v1.2.3")


@pytest.mark.parametrize("damage", ["dirty", "event-commit", "event-tag", "release-id", "draft", "prerelease", "missing-asset", "version", "package"])
def test_gate_rejects_checkout_event_lifecycle_and_package_mismatches(consumer, monkeypatch, damage):
    settings, _, release = consumer
    reviewed = publishing.ReviewedRelease("v1.2.3", SHA, 9)
    if damage == "dirty":
        monkeypatch.setattr(
            publishing, "run_git_command", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "?? unexpected\n" if "status" in args else SHA, "")
        )
    elif damage in {"event-commit", "event-tag"}:
        reviewed = replace(reviewed, **({"commit": "b" * 40} if damage == "event-commit" else {"tag": "v1.2.4"}))
    elif damage in {"version", "package"}:
        path = settings.root / "pyproject.toml"
        path.write_bytes(path.read_bytes().replace(b"1.2.3", b"1.2.4") if damage == "version" else path.read_bytes().replace(b"sample", b"other"))
    else:
        fields = {"release-id": {"identifier": 10}, "draft": {"draft": True}, "prerelease": {"prerelease": True}, "missing-asset": {"assets": ()}}
        monkeypatch.setattr(publishing, "lookup_release", lambda *args: replace(release, **fields[damage]))
    with pytest.raises(ValueError):
        publishing.check_reviewed_release(settings, "v1.2.3", event=reviewed)


def test_explicit_draft_publication_rechecks_before_one_lifecycle_mutation(consumer, monkeypatch):
    settings, _, release = consumer
    monkeypatch.setattr(publishing, "lookup_release", lambda *args: replace(release, draft=True))
    commands = []
    monkeypatch.setattr(assets, "_release_by_id", lambda *args: replace(release, draft=True))
    gh = assets._gh

    def response(root, args):
        if "PATCH" not in args:
            return gh(root, args)
        commands.append(("gh", args))
        return json.dumps(
            {
                "url": "https://api.github.com/repos/example/project/releases/9",
                "id": 9,
                "tag_name": "v1.2.3",
                "name": None,
                "draft": False,
                "prerelease": False,
                "immutable": False,
                "assets": [{"name": "sample-1.2.3.tgz", "id": 3, "size": 42, "digest": None, "state": "uploaded"}],
            }
        ).encode()

    monkeypatch.setattr(assets, "_gh", response)
    publishing.publish_reviewed_release(settings, "v1.2.3")
    assert commands == [("gh", ["api", "--method", "PATCH", "repos/example/project/releases/9", "--field", "draft=false"])]
    monkeypatch.setattr(publishing, "lookup_release", lambda *args: release)
    with pytest.raises(ValueError):
        publishing.publish_reviewed_release(settings, "v1.2.3")
    assert len(commands) == 1


@pytest.mark.parametrize("damage", ["identity", "immutable"])
def test_changed_draft_blocks_publication_without_external_mutation(consumer, monkeypatch, damage):
    settings, _, release = consumer
    changed = replace(release, draft=True, **({"identifier": 10} if damage == "identity" else {"immutable": True}))
    values = iter([replace(release, draft=True), changed])
    monkeypatch.setattr(publishing, "lookup_release", lambda *args: next(values))
    commands = []
    monkeypatch.setattr(publishing, "run_safe_command", lambda *args, **kwargs: commands.append(args))
    with pytest.raises(ValueError, match="identity changed" if damage == "identity" else "mutable stable draft"):
        publishing.publish_reviewed_release(settings, "v1.2.3")
    assert commands == []


def test_registry_cli_blocks_existing_versions_and_unavailable_evidence(consumer, monkeypatch):
    from research_repo_tools import registry

    settings, _, _ = consumer
    args = cli.parser().parse_args(["release", "registry", "v1.2.3", "--expect", "absent"])
    visible = registry.RegistryVersion("pypi", "sample", "1.2.3", True)
    monkeypatch.setattr(registry, "lookup_version", lambda *args: visible)
    with pytest.raises(ValueError, match="already present"):
        cli.run(args, settings)
    monkeypatch.setattr(registry, "lookup_version", lambda *args: replace(visible, present=False))
    assert cli.run(args, settings) == 0

    def unavailable(*args):
        raise registry.RegistryLookupError("rate limited")

    monkeypatch.setattr(registry, "lookup_version", unavailable)
    with pytest.raises(registry.RegistryLookupError):
        cli.run(args, settings)


def test_workflow_template_keeps_authentication_and_native_upload_in_approved_job():
    import yaml

    data = yaml.safe_load(changelog.template("publish-crates.yml", owner="example", repository="project"))
    assert data[True] == {"release": {"types": ["published"]}}
    assert data["concurrency"]["cancel-in-progress"] is False
    verify, publish = data["jobs"]["verify"], data["jobs"]["publish"]
    assert "id-token" not in verify["permissions"]
    assert publish["environment"] == "crates-io" and publish["permissions"]["id-token"] == "write"
    steps = publish["steps"]
    auth = next(index for index, step in enumerate(steps) if step.get("id") == "auth")
    assert "--expect absent" in steps[auth - 1]["run"]
    assert steps[auth + 1]["env"]["CARGO_REGISTRY_TOKEN"] == "${{ steps.auth.outputs.token }}"
    assert "cargo publish --locked --registry crates-io" in steps[auth + 1]["run"]
    assert "!cancelled()" in steps[auth + 2]["if"]
    assert "continue-on-error" not in steps[auth + 1]
    for job in (verify, publish):
        for step in job["steps"]:
            if "uses" in step:
                assert len(step["uses"].split("@")[1]) == 40
            assert "--allow-dirty" not in step.get("run", "")
            assert "--no-verify" not in step.get("run", "")


def test_common_release_recipes_and_guides_share_argument_contracts(tmp_path):
    root = Path(__file__).resolve().parents[2]
    justfile = tmp_path / "justfile"
    justfile.write_text(changelog.template("justfile"), encoding="utf-8", newline="\n")
    from research_repo_tools.just_inspect import inspect_justfile

    maintained = inspect_justfile(root)
    packaged = inspect_justfile(tmp_path)
    for recipe, arguments, expected in [
        ("release-check", ["v1.2.3"], "release check"),
        ("release-first", ["v1.2.3", "2026-09-01"], "--first-release"),
        ("release-notes", ["v1.2.3"], "changelog notes"),
        ("release-publish", ["v1.2.3"], "--approve"),
        ("release-tag", ["v1.2.3"], "changelog tag"),
        ("release-tag-preview", ["v1.2.3"], "--dry-run"),
        ("release-update", ["v1.2.3", "v1.2.2", "2026-09-01"], "--previous-release"),
        ("release-verify", ["v1.2.3", "--attempts", "7"], "release verify"),
    ]:
        for path in [root / "justfile", justfile]:
            result = publishing.run_safe_command("just", ["--justfile", str(path), "--dry-run", recipe, *arguments], cwd=tmp_path)
            assert expected in result.stderr
        assert maintained.recipes[recipe]["parameters"] == packaged.recipes[recipe]["parameters"]

    def commands(text):
        return [line for line in text.splitlines() if line.startswith("just ")]

    common = commands(changelog.template("RELEASING.md"))
    shared_recipes = {command.split()[1] for command in common}
    documented = commands((root / "docs/RELEASING.md").read_text(encoding="utf-8"))
    shared = [command for command in documented if command.split()[1] in shared_recipes]
    # Package-specific setup and checks may extend the guide, but shared recipe
    # arguments and workflow order must agree. Read-only notes may be repeated.
    assert set(shared) == set(common)
    assert [command for command in shared if not command.startswith("just release-notes ")] == [
        command for command in common if not command.startswith("just release-notes ")
    ]


def test_cli_release_gate_reads_current_event_and_rejects_wrong_tag(consumer, monkeypatch):
    settings, _, _ = consumer
    path = settings.root / "event.json"
    path.write_text(json.dumps(event()), encoding="utf-8", newline="\n")
    for name, value in {
        "GITHUB_EVENT_NAME": "release",
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_SHA": SHA,
        "GITHUB_REF": "refs/tags/v1.2.3",
        "GITHUB_EVENT_PATH": str(path),
    }.items():
        monkeypatch.setenv(name, value)
    args = cli.parser().parse_args(["release", "gate", "v1.2.3"])
    assert cli.run(args, settings) == 0
    args.tag = "v1.2.4"
    with pytest.raises(ValueError):
        cli.run(args, settings)


def test_cli_gate_rejects_duplicate_event_fields_before_remote_checks(tmp_path, monkeypatch, capsys):
    (tmp_path / "research.toml").write_text(
        '[publishing]\nregistry="pypi"\npackage="sample"\nrepository="example/project"\nrequired-checks=[{name="native",app-id=15368}]\n',
        encoding="utf-8",
        newline="\n",
    )
    path = tmp_path / "event.json"
    path.write_bytes(json.dumps(event()).encode().replace(b'"draft": false', b'"draft": true, "draft": false'))
    for name, value in {
        "GITHUB_EVENT_NAME": "release",
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_EVENT_PATH": str(path),
        "GITHUB_SHA": SHA,
        "GITHUB_REF": "refs/tags/v1.2.3",
    }.items():
        monkeypatch.setenv(name, value)
    remote = []
    monkeypatch.setattr(publishing, "check_reviewed_release", lambda *args, **kwargs: remote.append(args))
    assert cli.main(["--config", str(tmp_path / "research.toml"), "release", "gate", "v1.2.3"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "duplicate JSON field" in output.err
    assert remote == []
