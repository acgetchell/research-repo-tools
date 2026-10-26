"""Generic YAML selection, policy rejection, tag peeling and publication failures."""

import json
from pathlib import Path

import pytest

from research_repo_tools import action_updates, cli, config, workflow_allowlist
from research_repo_tools.workflow_uses import references, workflow_paths

OLD, NEW = "a" * 40, "b" * 40


@pytest.mark.parametrize("scalar", [f"approved/action@{OLD}", f"'approved/action@{OLD}'", f'"approved/action@{OLD}"', f">-\n          approved/action@{OLD}"])
@pytest.mark.parametrize("name", ["", "      - name: named\n        "])
def test_step_styles(scalar, name):
    prefix = name or "      - "
    text = f"jobs:\n  check:\n    steps:\n{prefix}uses: {scalar}\n"
    assert references(Path("example.yml"), text)[0].action == "approved/action"


def test_aliases_retain_anchor_location():
    text = f"jobs:\n  check:\n    steps:\n      - uses: &action approved/action@{OLD}\n      - uses: *action\n"
    uses = references(Path("example.yml"), text)
    assert len(uses) == 2 and uses[0].node is uses[1].node
    assert "example.yml:4" in uses[1].location


@pytest.mark.parametrize("scalar", [f"approved/action@{OLD}", f"'approved/action@{OLD}'", f'"approved/action@{OLD}"', f">-\n        approved/action@{OLD}"])
@pytest.mark.parametrize("named", [False, True])
def test_composite_step_styles_and_locations(scalar, named):
    prefix = "    - name: named\n      " if named else "    - "
    text = f"name: example\nruns:\n  using: composite\n  steps:\n{prefix}uses: {scalar}\n"
    (use,) = references(Path("action.yml"), text)
    assert (use.action, use.ref, use.context) == ("approved/action", OLD, "runs.steps[0].uses")
    assert use.line == (6 if named else 5)
    assert text[use.start : use.end].strip() == scalar


def test_composite_aliases_and_non_reference_metadata():
    text = (
        f"description: uses other/action@{OLD}\n"
        f"example: {{uses: unapproved/example@{OLD}}}\n"
        "runs:\n  using: composite\n  steps:\n"
        f"    - &step {{uses: &action approved/action@{OLD}}}\n"
        "    - *step\n    - uses: *action\n"
        "    - uses: ./local\n    - uses: '$/.github/actions/setup'\n    - uses: docker://image:latest\n"
    )
    uses = references(Path("action.yml"), text)
    assert [use.action for use in uses] == ["approved/action"] * 3 + [None] * 3
    assert [use.context for use in uses] == [f"runs.steps[{index}].uses" for index in range(6)]
    assert all(use.node is uses[0].node and use.line == 6 for use in uses[:3])


def test_same_repository_steps_and_workflow_calls_are_non_external():
    text = "jobs:\n  call:\n    uses: $/.github/workflows/check.yml\n  check:\n    steps:\n      - uses: '$/.github/actions/setup'\n"
    uses = references(Path("example.yml"), text)
    assert [(use.context, use.action, use.ref) for use in uses] == [("jobs.call.uses", None, None), ("jobs.check.steps[0].uses", None, None)]


@pytest.mark.parametrize(
    "fragment",
    [
        "jobs: {}\njobs: {}",
        "jobs: {check: {<<: {uses: action/repo@v1}}}",
        "jobs: {check: {steps: [{uses: 42}]}}",
        "jobs: &cycle {check: *cycle}",
        "jobs: {check: {uses: action/repo}}",
        "jobs: {check: {uses: 'a/b@${{ x }}'}}",
        "jobs: [check]",
        "jobs: {check: {steps: {uses: x}}}",
        "jobs: {}\nruns: {using: composite, steps: []}",
        "runs: []",
        "runs: {using: composite}",
        "runs: {using: composite, steps: {uses: a/b@v1}}",
        "runs: {using: composite, steps: [42]}",
        "runs: {using: composite, steps: [{uses: 42}]}",
        "runs: {using: composite, steps: [{uses: a/b@v1, uses: other/action@v2}]}",
        "runs: {using: composite, steps: [{<<: {uses: a/b@v1}}]}",
        "runs: &cycle {using: composite, steps: [*cycle]}",
        "runs: {using: [composite], steps: []}",
        "runs: {using: node24, main: index.js}",
        "runs: {steps: []}",
        "name: no workflow or action",
    ],
)
def test_ambiguous_or_invalid_yaml_fails(fragment):
    with pytest.raises(ValueError):
        references(Path("bad.yml"), fragment)


@pytest.mark.parametrize(
    "change",
    [
        {"verified_allowed": True},
        {"github_owned_allowed": True},
        {"patterns_allowed": ["actions/*@*"]},
        {"patterns_allowed": ["a/b@v1"]},
        {"patterns_allowed": ["a/b@main"]},
        {"patterns_allowed": [f"a/b@{OLD[:-1]}"]},
        {"patterns_allowed": [f"a/b@{OLD}0"]},
        {"patterns_allowed": [f"a/b@{'g' * 40}"]},
        {"patterns_allowed": [f"a/b@{OLD}@*"]},
        {"patterns_allowed": [f"a/b@{OLD} "]},
        {"patterns_allowed": ["a/b@v*"]},
        {"patterns_allowed": [f"a/*@{OLD}"]},
        {"patterns_allowed": [f"a/b/*@{OLD}"]},
        {"patterns_allowed": [f"a/b/../c@{OLD}"]},
        {"patterns_allowed": [f"a/b//c@{OLD}"]},
        {"patterns_allowed": [f"./local@{OLD}"]},
        {"patterns_allowed": [False]},
        {"patterns_allowed": [None]},
        {"patterns_allowed": ["a/b/../c@*"]},
        {"extra": False},
        {"patterns_allowed": "a/b@*"},
    ],
)
def test_unsupported_policy_fails(tmp_path, change):
    value = {"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": ["a/b@*"]} | change
    path = tmp_path / "policy.json"
    path.write_bytes(json.dumps(value).encode())
    with pytest.raises(ValueError):
        workflow_allowlist.load_policy(path)


def test_policy_authority_exact_nested_paths_and_local_scope(tmp_path):
    policy = tmp_path / "policy.json"
    workflow = tmp_path / "workflow.yml"
    value = {"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": ["APPROVED/Repo/.github/workflows/test.yml@*"]}
    policy.write_bytes(json.dumps(value).encode())
    workflow.write_bytes(
        (
            f"jobs:\n  reusable:\n    uses: approved/repo/.github/workflows/test.yml@{OLD}\n"
            "  same_repository:\n    uses: $/.github/workflows/check.yml\n"
            "  local:\n    steps:\n      - uses: ./local\n      - uses: '$/.github/actions/setup'\n      - uses: docker://image:latest\n"
        ).encode()
    )
    assert workflow_allowlist.check(tmp_path, policy, [str(workflow)]) == 0
    value["patterns_allowed"] = ["approved/repo@*"]
    policy.write_bytes(json.dumps(value).encode())
    with pytest.raises(ValueError, match="unapproved"):
        workflow_allowlist.check(tmp_path, policy, [str(workflow)])


@pytest.mark.parametrize("allowed", [[OLD], [NEW, OLD], ["*", OLD]])
@pytest.mark.parametrize("ref", [OLD, OLD.upper()])
def test_policy_exact_commit_normalizes_hex_and_accepts_any_matching_rule(tmp_path, allowed, ref):
    policy = tmp_path / "policy.json"
    policy.write_bytes(
        json.dumps(
            {"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": [f"APPROVED/Repo/Path@{sha.upper()}" for sha in allowed]}
        ).encode()
    )
    workflow = tmp_path / "workflow.yml"
    workflow.write_bytes(f"jobs: {{call: {{uses: approved/repo/Path@{ref}}}}}\n".encode())
    assert workflow_allowlist.check(tmp_path, policy, [str(workflow)]) == 0


@pytest.mark.parametrize("ref", [OLD, OLD[:7], "main", "v1", "release/v1"])
def test_policy_wildcard_keeps_supported_refs_for_the_exact_identity(tmp_path, ref):
    policy = tmp_path / "policy.json"
    policy.write_bytes(b'{"github_owned_allowed":false,"verified_allowed":false,"patterns_allowed":["approved/action@*"]}')
    workflow = tmp_path / "workflow.yml"
    workflow.write_bytes(f"jobs: {{call: {{uses: approved/action@{ref}}}}}\n".encode())
    assert workflow_allowlist.check(tmp_path, policy, [str(workflow)]) == 0


@pytest.mark.parametrize(
    "action,ref",
    [
        ("approved/repo/Path", NEW),
        ("approved/repo/Path", OLD[:-1]),
        ("approved/repo/Path", "main"),
        ("approved/repo/Path", "v1"),
        ("approved/repo/path", OLD),
        ("approved/repo/Other", OLD),
    ],
)
def test_policy_exact_commit_rejects_other_refs_and_paths_without_weakening_advice(tmp_path, action, ref):
    policy = tmp_path / "policy.json"
    policy.write_bytes(
        json.dumps({"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": [f"approved/repo/Path@{OLD}", "approved/repo@*"]}).encode()
    )
    workflow = tmp_path / "workflow.yml"
    workflow.write_bytes(f"jobs: {{call: {{uses: {action}@{ref}}}}}\n".encode())
    with pytest.raises(ValueError) as caught:
        workflow_allowlist.check(tmp_path, policy, [str(workflow)])
    diagnostic = str(caught.value)
    assert "workflow.yml:1" in diagnostic and "jobs.call.uses" in diagnostic
    assert f"{action}@{ref}" in diagnostic and str(policy) in diagnostic
    assert "@*" not in diagnostic
    if action == "approved/repo/Path":
        assert f"permitted references: approved/repo/Path@{OLD}" in diagnostic
    else:
        assert "no approved identity" in diagnostic


def test_annotated_tag_peeling_and_exact_tag_guard(tmp_path, monkeypatch):
    responses = iter([{"ref": "refs/tags/v2", "object": {"type": "tag", "sha": OLD}}, {"object": {"type": "commit", "sha": NEW}}])
    calls = []

    def api(endpoint, root):
        calls.append(endpoint)
        return next(responses)

    monkeypatch.setattr(action_updates, "_api", api)
    assert action_updates.resolve("a/b/subpath", "v2", tmp_path) == (NEW, "v2")
    assert calls == ["repos/a/b/git/ref/tags/v2", f"repos/a/b/git/tags/{OLD}"]
    monkeypatch.setattr(action_updates, "_api", lambda *_: {"ref": "refs/tags/v3", "object": {"type": "commit", "sha": NEW}})
    with pytest.raises(ValueError, match="exact requested tag"):
        action_updates.resolve("a/b", "v2", tmp_path)


@pytest.mark.parametrize(
    "response",
    [
        {"draft": True, "prerelease": False, "tag_name": "v2"},
        {"draft": False, "prerelease": True, "tag_name": "v2"},
        {"draft": False, "prerelease": False, "tag_name": "v2-rc1"},
    ],
)
def test_unstable_release_resolution_fails(tmp_path, monkeypatch, response):
    monkeypatch.setattr(action_updates, "_api", lambda *_: response)
    with pytest.raises(ValueError):
        action_updates.resolve("a/b", "latest", tmp_path)


def test_invalid_later_workflow_preserves_every_file(tmp_path, monkeypatch):
    policy = tmp_path / "update.toml"
    policy.write_bytes(b'[actions]\n"a/b/path"="v2"\n')
    first, second = tmp_path / "a.yml", tmp_path / "b.yml"
    first.write_bytes(f"jobs:\n  call:\n    uses: a/b/path@{OLD} # v1\n".encode())
    second.write_bytes(b"jobs: {call: {uses: a/b/path@v1}}\n")
    before = {path: path.read_bytes() for path in (first, second)}
    monkeypatch.setattr(action_updates, "resolve", lambda *_: (NEW, "v2"))
    with pytest.raises(ValueError, match="full commit SHAs"):
        action_updates.update(config.parse({}, root=tmp_path), policy, [str(first), str(second)])
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_scope_selection_subpaths_and_unrelated_comment_bytes(tmp_path, monkeypatch, newline):
    policy = tmp_path / "update.toml"
    policy.write_bytes(b'[actions]\n"a/b/path"="v2.1.0"\n')
    workflow = tmp_path / "workflow.yml"
    before = (
        (
            f"# keep\njobs:\n  call:\n    uses: a/b/path@{OLD} # v1.0.0 reason\n"
            f"  same_repository:\n    uses: $/.github/workflows/check.yml # v1.0.0 {OLD}\n"
            f"  step:\n    steps:\n      - uses: a/b@{OLD} # v1\n"
            f"      - uses: '$/.github/actions/setup' # v1.0.0 {OLD}\n"
        )
        .replace("\n", newline)
        .encode()
    )
    workflow.write_bytes(before)
    monkeypatch.setattr(action_updates, "resolve", lambda *_: (NEW, "v2.1.0"))
    assert action_updates.update(config.parse({}, root=tmp_path), policy, [str(workflow)]) == 0
    expected = before.replace(f"a/b/path@{OLD} # v1.0.0".encode(), f"a/b/path@{NEW} # v2.1.0".encode())
    assert workflow.read_bytes() == expected


def test_duplicate_json_policy_field(tmp_path):
    path = tmp_path / "policy.json"
    path.write_bytes(b'{"verified_allowed":false,"verified_allowed":false}')
    with pytest.raises(ValueError, match="duplicate"):
        workflow_allowlist.load_policy(path)


def test_pin_replacement_does_not_replace_identical_sha_in_subpath(tmp_path, monkeypatch):
    action = f"a/b/{OLD}"
    policy = tmp_path / "update.toml"
    policy.write_bytes(f'[actions]\n"{action}"="v2"\n'.encode())
    workflow = tmp_path / "workflow.yml"
    workflow.write_bytes(f"jobs:\n  call:\n    uses: {action}@{OLD} # v1\n".encode())
    monkeypatch.setattr(action_updates, "resolve", lambda *_: (NEW, "v2"))
    action_updates.update(config.parse({}, root=tmp_path), policy, [str(workflow)])
    assert workflow.read_bytes() == f"jobs:\n  call:\n    uses: {action}@{NEW} # v2\n".encode()


@pytest.mark.parametrize("support", [b"# 1.2.3 unsupported\n1.2.2 sha\n", b"1.2.3 sha\n1.2.3 other\n", b"unsupported: 1.2.3\n", b"11.2.3 sha\n"])
def test_support_inventory_does_not_accept_comments_substrings_or_ambiguous_rows(tmp_path, monkeypatch, support):
    import base64

    monkeypatch.setattr(action_updates, "_api", lambda *_: {"encoding": "base64", "content": base64.b64encode(support).decode()})
    with pytest.raises(ValueError):
        action_updates._compatible("a/b", OLD, "support/versions", "1.2.3", tmp_path)


@pytest.mark.parametrize("scalar", [f"a/b@{OLD}", f"&use a/b@{OLD} # v1", f">-\n      a/b@{OLD} # v1"])
def test_unsupported_update_scalar_or_missing_comment_preserves_source(tmp_path, monkeypatch, scalar):
    policy = tmp_path / "policy.toml"
    policy.write_bytes(b'[actions]\n"a/b"="v2"\n')
    workflow = tmp_path / "workflow.yml"
    original = f"jobs:\n  call:\n    uses: {scalar}\n".encode()
    workflow.write_bytes(original)
    monkeypatch.setattr(action_updates, "resolve", lambda *_: (NEW, "v2"))
    with pytest.raises(ValueError):
        action_updates.update(config.parse({}, root=tmp_path), policy, [str(workflow)])
    assert workflow.read_bytes() == original


def test_workflow_inventory_deduplicates_spellings_and_ignores_yaml_directories(tmp_path):
    workflow = tmp_path / "ci.yml"
    workflow.write_bytes(b"jobs: {}\n")
    (tmp_path / "directory.yaml").mkdir()
    (tmp_path / "subdir").mkdir()
    assert workflow_paths(tmp_path, [".", "ci.yml", "subdir/../ci.yml"]) == (workflow,)


@pytest.mark.parametrize("groups", ['dependency-groups="invalid"', '[dependency-groups]\ndev="scanner==1.2.3"', "[dependency-groups]\ndev=[42]"])
def test_wrapper_malformed_groups_have_cli_diagnostics(tmp_path, monkeypatch, capsys, groups):
    (tmp_path / "pyproject.toml").write_bytes((groups + "\n").encode())
    policy = tmp_path / "updates.toml"
    policy.write_bytes(b'[actions]\n[compatibility."a/b"]\npath="versions"\ntool="scanner"\n')
    workflow = tmp_path / "ci.yml"
    before = f"jobs:\n  call:\n    uses: a/b@{OLD} # v1\n".encode()
    workflow.write_bytes(before)
    monkeypatch.setattr(action_updates, "_api", lambda *_: pytest.fail("invalid tool pins must fail before support lookup"))
    assert cli.main(["--root", str(tmp_path), "actions", "update", "--policy", str(policy), str(workflow)]) == 1
    assert "dependency-groups" in capsys.readouterr().err
    assert workflow.read_bytes() == before


def test_wrapper_python_names_use_package_normalization(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(b'[dependency-groups]\ntooling=["scan_ner==1.2.3"]\ndev=[{include-group="tooling"}]\n')
    assert action_updates._tool_version(config.parse({}, root=tmp_path), "scan--ner") == "1.2.3"


def test_deep_workflow_reports_invalid_yaml(tmp_path, capsys):
    policy = tmp_path / "selected.json"
    policy.write_bytes(b'{"github_owned_allowed":false,"verified_allowed":false,"patterns_allowed":[]}')
    workflow = tmp_path / "deep.yml"
    workflow.write_bytes(("extra: " + "[" * 1500 + "0" + "]" * 1500 + "\njobs: {}\n").encode())
    assert cli.main(["--root", str(tmp_path), "actions", "allowlist", "--policy", str(policy), str(workflow)]) == 1
    assert "deep.yml" in capsys.readouterr().err


@pytest.mark.parametrize("second", ["jobs:\n  call:\n    uses:", "runs:\n  using: composite\n  steps:\n    - uses:"])
def test_failed_second_publication_rolls_back_and_has_no_completion_report(tmp_path, monkeypatch, capsys, second):
    from research_repo_tools import files

    policy = tmp_path / "updates.toml"
    policy.write_bytes(b'[actions]\n"a/b"="v2"\n')
    workflows = [tmp_path / "a.yml", tmp_path / "b.yml"]
    workflows[0].write_bytes(f"jobs:\n  call:\n    uses: a/b@{OLD} # v1\n".encode())
    workflows[1].write_bytes(f"{second} a/b@{OLD} # v1\n".encode())
    before = {workflow: workflow.read_bytes() for workflow in workflows}
    replace = files._replace_path
    publications = []

    def fail_second(source, target):
        if source.suffix == ".tmp":
            publications.append(target)
            if len(publications) == 2:
                raise OSError("simulated second publication failure")
        replace(source, target)

    monkeypatch.setattr(action_updates, "resolve", lambda *_: (NEW, "v2"))
    monkeypatch.setattr(files, "_replace_path", fail_second)
    assert cli.main(["--root", str(tmp_path), "actions", "update", "--policy", str(policy), *map(str, workflows)]) == 1
    output = capsys.readouterr()
    assert "simulated second publication failure" in output.err
    assert "Applied" not in output.out and "Update " not in output.out
    assert {workflow: workflow.read_bytes() for workflow in workflows} == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.yml", "b.yml", "updates.toml"]


def test_concurrent_edit_during_resolution_is_preserved(tmp_path, monkeypatch):
    policy = tmp_path / "updates.toml"
    policy.write_bytes(b'[actions]\n"a/b"="v2"\n')
    workflow = tmp_path / "ci.yml"
    original = f"jobs:\n  call:\n    uses: a/b@{OLD} # v1\n".encode()
    workflow.write_bytes(original)
    edited = original + b"# concurrent edit\n"

    def resolve(*_):
        workflow.write_bytes(edited)
        return NEW, "v2"

    monkeypatch.setattr(action_updates, "resolve", resolve)
    with pytest.raises(ValueError, match="workflow changed"):
        action_updates.update(config.parse({}, root=tmp_path), policy, [str(workflow)])
    assert workflow.read_bytes() == edited
