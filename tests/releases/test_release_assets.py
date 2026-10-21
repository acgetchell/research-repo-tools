"""Provenance must join one immutable producer, complete attempt and promotion."""

import copy
import hashlib
import io
import json
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from research_repo_tools import release_validation as release

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
TAG = "v0.2.3"
NAMES = release.distribution_names(TAG)
WORKFLOW = {"id": 12, "path": ".github/workflows/ci.yml", "name": "Package checks", "state": "active"}


def run():
    return {
        "id": 123,
        "run_attempt": 1,
        "workflow_id": 12,
        "path": WORKFLOW["path"],
        "name": WORKFLOW["name"],
        "event": "push",
        "head_branch": "main",
        "head_sha": COMMIT,
        "repository": {"id": 99, "full_name": release.REPOSITORY},
        "head_repository": {"id": 99, "full_name": release.REPOSITORY},
        "pull_requests": [],
        "status": "completed",
        "conclusion": "success",
    }


def jobs():
    return [
        {
            "id": index,
            "run_id": 123,
            "run_attempt": 1,
            "head_sha": COMMIT,
            "head_branch": "main",
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in steps],
        }
        for index, (name, steps) in enumerate(release.POLICY.required_steps, 10)
    ]


def distributions(*, version="0.2.3", package="research-repo-tools"):
    metadata = f"Metadata-Version: 2.3\nName: {package}\nVersion: {version}\n".encode()
    wheel, sdist = io.BytesIO(), io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("research_repo_tools-0.2.3.dist-info/METADATA", metadata)
    with tarfile.open(fileobj=sdist, mode="w:gz") as archive:
        member = tarfile.TarInfo("research_repo_tools-0.2.3/PKG-INFO")
        member.size = len(metadata)
        archive.addfile(member, io.BytesIO(metadata))
    return dict(zip(NAMES, (wheel.getvalue(), sdist.getvalue()), strict=True))


def zipped(contents):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name, payload in contents.items():
            archive.writestr(name, payload)
    return result.getvalue()


def artifact(identifier, name, payload):
    return {
        "id": identifier,
        "name": name,
        "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
        "size_in_bytes": len(payload),
        "expired": False,
        "workflow_run": {"id": 123, "repository_id": 99, "head_repository_id": 99, "head_branch": "main", "head_sha": COMMIT},
    }


@pytest.fixture
def remote(monkeypatch, tmp_path):
    """Fake authenticated transport, not selection, extraction or verification policy."""
    contents = distributions()
    distribution_zip = zipped(contents)
    producer = artifact(31, "distributions-1", distribution_zip)
    manifest = {
        "schema": release.SCHEMA,
        "repository": release.REPOSITORY,
        "repository_id": 99,
        "run": release._identity(run()),
        "jobs": {job["name"]: job["id"] for job in jobs()},
        "artifact": release._artifact(producer, run(), "distributions-1"),
        "version": "0.2.3",
        "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in contents.items()},
    }
    manifest_bytes = release._bytes(manifest)
    evidence_zip = zipped({release.MANIFEST: manifest_bytes, release.VALIDATION_BUNDLE: b"original signed main bundle"})
    evidence = artifact(32, "validated-evidence-1", evidence_zip)
    state: dict[str, Any] = {
        "run": run(),
        "runs": [run()],
        "jobs": [*jobs(), {"id": 19, "name": "release-evidence", "run_attempt": 1, "status": "completed", "conclusion": "success"}],
        "artifacts": [producer, evidence],
        "zip": {31: distribution_zip, 32: evidence_zip},
        "contents": contents,
        "manifest": manifest_bytes,
        "calls": [],
        "release": None,
        "tag_commit": COMMIT,
        "signature_defect": None,
        "downloads": {},
        "listing": [],
        "uploaded": None,
    }

    def api(endpoint, *, repository=release.REPOSITORY):
        state["calls"].append(endpoint)
        state["calls"].append(("api-repository", repository))
        if endpoint == "actions/workflows/ci.yml":
            return copy.deepcopy(WORKFLOW)
        if endpoint.startswith("actions/workflows/12/runs?"):
            return {"workflow_runs": copy.deepcopy(state["runs"])}
        if endpoint == "actions/runs/123":
            return copy.deepcopy(state["run"])
        if "/attempts/" in endpoint and "/jobs?" in endpoint:
            return {"jobs": copy.deepcopy(state["jobs"])}
        if endpoint.startswith("actions/runs/123/artifacts?"):
            return {"artifacts": copy.deepcopy(state["artifacts"])}
        if endpoint.startswith("actions/artifacts/"):
            return copy.deepcopy(next(entry for entry in state["artifacts"] if entry["id"] == int(endpoint.rsplit("/", 1)[1])))
        if endpoint == f"git/ref/tags/{TAG}":
            return {"ref": f"refs/tags/{TAG}", "object": {"type": "tag", "sha": "b" * 40}}
        if endpoint == f"git/tags/{'b' * 40}":
            return {"sha": "b" * 40, "object": {"type": "commit", "sha": state["tag_commit"]}}
        if endpoint == "releases/17":
            return copy.deepcopy(state["release"])
        pytest.fail(f"unexpected API endpoint {endpoint}")

    def gh(*args):
        state["calls"].append(args)
        if args[:2] == ("attestation", "verify"):
            # Preserve verification as an explicit trust boundary: verify CLI
            # constraints and emulate only its already-authenticated JSON output.
            expected = {"--repo": release.REPOSITORY, "--signer-digest": COMMIT, "--source-digest": COMMIT, "--format": "json"}
            assert "--deny-self-hosted-runners" in args
            for flag, value in expected.items():
                assert args[args.index(flag) + 1] == value
            origin = Path(args[2]).name == release.MANIFEST
            assert args[args.index("--source-ref") + 1] == ("refs/heads/main" if origin else f"refs/tags/{TAG}")
            assert args[args.index("--signer-workflow") + 1] == f"{release.REPOSITORY}/.github/workflows/{'ci' if origin else 'prepare-release'}.yml"
            bundle = Path(args[args.index("--bundle") + 1]).read_bytes()
            assert bundle == (b"original signed main bundle" if origin else b"signed promotion bundle")
            if state["signature_defect"] == "signature":
                raise subprocess.CalledProcessError(1, ["gh"], stderr="invalid signed digest")
            if origin:
                assert Path(args[2]).read_bytes() == state["manifest"]
                predicate = {}
            else:
                predicate = json.loads((tmp_path / "promotion.json").read_bytes())
            run_id, attempt = (123, 1) if origin else (456, 1)
            certificate = {"runInvocationURI": f"https://github.com/{release.REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}", "buildTrigger": "push"}
            if state["signature_defect"] == "attempt":
                certificate["runInvocationURI"] += "9"
            elif state["signature_defect"] == "event":
                certificate["buildTrigger"] = "pull_request"
            elif state["signature_defect"] == "predicate":
                predicate = {"wrong": "predicate"}
            return json.dumps([{"verificationResult": {"signature": {"certificate": certificate}, "statement": {"predicate": predicate}}}])
        if args[:3] == ("api", "--paginate", "--slurp"):
            return json.dumps([state["listing"]])
        if args[:2] == ("release", "create"):
            assert "--draft" in args and "--verify-tag" in args and "--repo" not in args
            return ""
        if args[:2] == ("release", "upload"):
            assert "--clobber" not in args
            state["uploaded"] = {Path(name).name: Path(name).read_bytes() for name in args[-3:]}
            return ""
        pytest.fail(f"unexpected gh args {args}")

    def execute(args, **kwargs):
        state["calls"].append(tuple(args))
        assert args[:2] == ["gh", "api"] and kwargs["timeout"] == 120 and kwargs["check"]
        identifier = int(args[2].split("/")[-2] if args[2].endswith("/zip") else args[2].split("/")[-1])
        data = state["zip"][identifier] if "/actions/artifacts/" in args[2] else state["downloads"][identifier]
        kwargs["stdout"].write(data)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(release, "api", api)
    monkeypatch.setattr(release, "gh", gh)
    monkeypatch.setattr(release.subprocess, "run", execute)
    monkeypatch.setenv("GITHUB_RUN_ID", "456")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", release.REPOSITORY)
    return state


def prepare(tmp_path):
    release.promote(TAG, COMMIT, tmp_path / "dist", tmp_path / "promotion.json", attempts=1, interval=0)
    bundle = tmp_path / "promotion.sigstore.json"
    bundle.write_bytes(b"signed promotion bundle")
    return bundle


def test_end_to_end_promotes_exact_original_bytes_and_verifies_both_signatures(remote, tmp_path):
    bundle = prepare(tmp_path)
    release.stage(TAG, tmp_path / "dist", bundle, tmp_path / "promotion.json")
    uploaded = remote["uploaded"]
    assert set(uploaded) == {*NAMES, release.BUNDLE}
    assert {name: uploaded[name] for name in NAMES} == remote["contents"]
    envelope = json.loads(uploaded[release.BUNDLE])
    assert envelope["evidence"]["manifest"].encode() == remote["manifest"]
    assert envelope["evidence"]["validation_bundle"] == "original signed main bundle"
    assets = [
        {"id": index, "name": name, "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}", "size": len(payload), "state": "uploaded"}
        for index, (name, payload) in enumerate(uploaded.items(), 1)
    ]
    remote["release"] = {"id": 17, "tag_name": TAG, "draft": False, "prerelease": False, "assets": assets}
    remote["downloads"] = {asset["id"]: uploaded[asset["name"]] for asset in assets}
    output = tmp_path / "published" / "verified-dist"
    release.verify({"action": "published", "repository": {"full_name": release.REPOSITORY}, "release": remote["release"]}, COMMIT, output)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == remote["contents"]
    assert any(isinstance(call, tuple) and "/actions/artifacts/31/zip" in call[2] for call in remote["calls"] if len(call) > 2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event", "pull_request"),
        ("event", "workflow_dispatch"),
        ("event", "workflow_call"),
        ("head_branch", "feature"),
        ("head_sha", "c" * 40),
        ("workflow_id", 13),
        ("path", ".github/workflows/other.yml"),
        ("name", "Untrusted"),
        ("run_attempt", 0),
        ("id", True),
        ("pull_requests", [{"id": 1}]),
    ],
)
def test_wrong_run_identity_never_reaches_artifact_retrieval(remote, tmp_path, field, value):
    remote["runs"][0][field] = value
    with pytest.raises(ValueError):
        prepare(tmp_path)
    assert not (tmp_path / "dist").exists()


@pytest.mark.parametrize("field", ["repository", "head_repository"])
def test_wrong_repository_or_fork_is_not_trusted(remote, tmp_path, field):
    remote["runs"][0][field] = {"id": 100, "full_name": "fork/research-repo-tools"}
    with pytest.raises(ValueError, match="repository"):
        prepare(tmp_path)


@pytest.mark.parametrize("state", ["missing", "failed", "cancelled", "ambiguous", "pending"])
def test_missing_failed_ambiguous_and_bounded_pending_validation(remote, tmp_path, state):
    if state == "missing":
        remote["runs"] = []
    elif state in {"failed", "cancelled"}:
        remote["runs"][0]["conclusion"] = state
    elif state == "ambiguous":
        remote["runs"].append({**run(), "id": 124})
    else:
        remote["runs"][0].update(status="in_progress", conclusion=None)
    with pytest.raises(ValueError, match="ambiguous|missing or failed|bounded wait"):
        prepare(tmp_path)
    assert not (tmp_path / "dist").exists()


def test_pending_wait_is_bounded_and_can_observe_exact_success(remote):
    remote["runs"][0].update(status="in_progress", conclusion=None)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        remote["runs"] = [run()]

    selected = release.select_main_run(release.POLICY, COMMIT, release.api, attempts=2, interval=3, sleep=sleep)
    assert selected["id"] == 123 and waits == [3]


@pytest.mark.parametrize(
    "defect", ["producer-missing", "duplicate", "job-failed", "job-skipped", "step-skipped", "step-missing", "wrong-run", "wrong-attempt", "wrong-commit"]
)
def test_complete_attempt_and_installed_validation_are_required(remote, tmp_path, defect):
    selected = remote["jobs"]
    if defect == "producer-missing":
        selected.pop(0)
    elif defect == "duplicate":
        selected.append(copy.deepcopy(selected[1]))
    elif defect.startswith("job-"):
        selected[1]["conclusion"] = defect[4:]
    elif defect == "step-skipped":
        selected[1]["steps"][-1]["conclusion"] = "skipped"
    elif defect == "step-missing":
        selected[1]["steps"].pop()
    else:
        selected[1][{"wrong-run": "run_id", "wrong-attempt": "run_attempt", "wrong-commit": "head_sha"}[defect]] = 999
    with pytest.raises(ValueError):
        prepare(tmp_path)
    assert not (tmp_path / "dist").exists()


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "expired",
        "duplicate",
        "wrong-run",
        "wrong-repo",
        "wrong-commit",
        "missing-digest",
        "wrong-digest",
        "wrong-size",
        "wrong-inventory",
        "symlink",
    ],
)
def test_artifact_identity_integrity_and_inventory_fail_closed(remote, tmp_path, defect):
    original = remote["artifacts"][0]
    if defect == "missing":
        remote["artifacts"].pop(0)
    elif defect == "expired":
        original["expired"] = True
    elif defect == "duplicate":
        remote["artifacts"].append(copy.deepcopy(original))
    elif defect in {"wrong-run", "wrong-repo", "wrong-commit"}:
        original["workflow_run"][{"wrong-run": "id", "wrong-repo": "head_repository_id", "wrong-commit": "head_sha"}[defect]] = 999
    elif defect == "missing-digest":
        original.pop("digest")
    elif defect == "wrong-digest":
        remote["zip"][31] += b"changed bytes"
    elif defect == "wrong-size":
        original["size_in_bytes"] += 1
    else:
        contents = {"../escape": b"unsafe"} if defect == "wrong-inventory" else remote["contents"]
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name, payload in contents.items():
                entry = zipfile.ZipInfo(name)
                if defect == "symlink":
                    entry.create_system = 3
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, payload)
        remote["zip"][31] = data.getvalue()
        original.update(digest=f"sha256:{hashlib.sha256(data.getvalue()).hexdigest()}", size_in_bytes=len(data.getvalue()))
        # Keep signed evidence consistent so extraction itself must reject it.
        manifest = json.loads(remote["manifest"])
        manifest["artifact"] = release._artifact(original, run(), "distributions-1")
        remote["manifest"] = release._bytes(manifest)
        proof = zipped({release.MANIFEST: remote["manifest"], release.VALIDATION_BUNDLE: b"original signed main bundle"})
        remote["zip"][32] = proof
        remote["artifacts"][1].update(digest=f"sha256:{hashlib.sha256(proof).hexdigest()}", size_in_bytes=len(proof))
    with pytest.raises(ValueError):
        prepare(tmp_path)
    assert remote["uploaded"] is None and not (tmp_path / "escape").exists()


@pytest.mark.parametrize("defect", ["signature", "attempt", "event", "predicate"])
def test_origin_and_promotion_signatures_are_both_required(remote, tmp_path, defect):
    if defect == "predicate":
        bundle = prepare(tmp_path)
        remote["signature_defect"] = defect
        with pytest.raises(ValueError, match="predicate"):
            release.stage(TAG, tmp_path / "dist", bundle, tmp_path / "promotion.json")
    else:
        remote["signature_defect"] = defect
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            prepare(tmp_path)
    assert remote["uploaded"] is None


@pytest.mark.parametrize("defect", ["moved-tag", "rerun", "changed-job", "changed-artifact", "changed-bytes", "published", "prerelease", "partial-assets"])
def test_state_is_rechecked_before_any_release_write(remote, tmp_path, defect):
    bundle = prepare(tmp_path)
    if defect == "moved-tag":
        remote["tag_commit"] = "c" * 40
    elif defect == "rerun":
        remote["run"]["run_attempt"] = 2
    elif defect == "changed-job":
        remote["jobs"][0]["id"] = 999
    elif defect == "changed-artifact":
        remote["artifacts"][0]["digest"] = f"sha256:{'0' * 64}"
    elif defect == "changed-bytes":
        (tmp_path / "dist" / NAMES[0]).write_bytes(distributions(version="0.2.2")[NAMES[0]])
    else:
        remote["listing"] = [
            {
                "tag_name": TAG,
                "draft": defect != "published",
                "prerelease": defect == "prerelease",
                "assets": [{"name": NAMES[0]}] if defect == "partial-assets" else [],
            }
        ]
    with pytest.raises(ValueError):
        release.stage(TAG, tmp_path / "dist", bundle, tmp_path / "promotion.json")
    assert remote["uploaded"] is None
    assert not any(isinstance(call, tuple) and call[:2] == ("release", "create") for call in remote["calls"])


@pytest.mark.parametrize(("version", "package"), [("0.2.2", "research-repo-tools"), ("0.2.3", "other-package")])
def test_both_distribution_metadata_contracts(tmp_path, version, package):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name, payload in distributions(version=version, package=package).items():
        (dist / name).write_bytes(payload)
    with pytest.raises(ValueError, match="name or version"):
        release.verify_distributions(dist, TAG)


def test_standard_library_entry_point_loads_without_installed_package(tmp_path, monkeypatch):
    (tmp_path / "sitecustomize.py").write_bytes(b'print("UNTRUSTED-SITE-STARTUP")\n')
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(ROOT / "scripts/release_assets.py"), "--help"], check=True, capture_output=True, text=True, timeout=10
    )
    assert "promote" in result.stdout and "seal" in result.stdout
    assert "UNTRUSTED-SITE-STARTUP" not in result.stdout


def test_workflows_preserve_native_gates_and_promote_without_rebuilding():
    ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    prepare = yaml.load((ROOT / ".github/workflows/prepare-release.yml").read_text(), Loader=yaml.BaseLoader)
    publish = yaml.load((ROOT / ".github/workflows/publish.yml").read_text(), Loader=yaml.BaseLoader)
    assert "pull_request" in ci["on"] and ci["on"]["push"]["branches"] == ["main"]
    evidence = ci["jobs"]["release-evidence"]
    assert set(evidence["needs"]) == {"build", "check", "changelog-integration"}
    assert "github.event_name == 'push'" in evidence["if"] and "refs/heads/main" in evidence["if"]
    assert prepare["on"] == {"push": {"tags": ["v*"]}}
    assert set(prepare["jobs"]) == {"preflight", "stage"}
    stage = prepare["jobs"]["stage"]
    assert stage["needs"] == "preflight" and "environment" not in stage and "if" not in stage
    stage_text = json.dumps(stage)
    assert "release_assets.py promote" in stage_text and "release_assets.py stage" in stage_text
    assert "ci.yml" not in stage_text and "just build" not in stage_text and "just ci" not in stage_text
    assert "predicate-path" in stage_text and release.PROMOTION in stage_text
    assert "just audit" in json.dumps(prepare["jobs"]["preflight"])
    for job in [*prepare["jobs"].values(), *publish["jobs"].values()]:
        assert job.get("continue-on-error", "false") == "false"
        for step in job.get("steps", []):
            assert "if" not in step and step.get("continue-on-error", "false") == "false"
    final = publish["jobs"]["publish"]
    assert final["needs"] == "verify" and final["environment"]["name"] == "pypi"
    assert final["permissions"] == {"id-token": "write"}
    assert len(final["steps"]) == 2 and final["steps"][1]["uses"].startswith("pypa/gh-action-pypi-publish@")


@pytest.mark.parametrize("defect", ["run-changed", "tag-moved"])
def test_changes_during_promotion_fail_before_emitting_signable_evidence(remote, tmp_path, monkeypatch, defect):
    read = release.api
    reads = 0

    def changed(endpoint):
        nonlocal reads
        watched = "actions/runs/123" if defect == "run-changed" else f"git/ref/tags/{TAG}"
        if endpoint == watched:
            reads += 1
            if reads == 2:
                if defect == "run-changed":
                    remote["run"]["run_attempt"] = 2
                else:
                    remote["tag_commit"] = "d" * 40
        return read(endpoint)

    monkeypatch.setattr(release, "api", changed)
    with pytest.raises(ValueError, match="changed|moved"):
        prepare(tmp_path)
    assert not (tmp_path / "promotion.json").exists()


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ([], "event"),
        ({"repository": None}, "event.repository"),
        ({"action": "published", "repository": {"full_name": release.REPOSITORY}, "release": []}, "event.release"),
    ],
)
def test_malformed_event_cli_diagnostics_have_no_side_effects(tmp_path, monkeypatch, capsys, payload, field):
    source = tmp_path / "event.json"
    source.write_bytes(json.dumps(payload).encode())
    monkeypatch.setattr(release.sys, "argv", ["release_assets.py", "verify", "--event", str(source), "--commit", COMMIT])
    monkeypatch.setattr(release, "api", lambda _: pytest.fail("unexpected remote access"))
    assert release.main() == 1
    assert f"{field} must be a JSON object" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == [source]


def test_cli_retains_transport_diagnostics_but_redacts_signed_urls(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["gh"], stderr=b'Get "https://storage.example/blob?sig=secret&token=private": Forbidden')

    monkeypatch.setattr(release, "promote", fail)
    monkeypatch.setattr(release.sys, "argv", ["release_assets.py", "promote", TAG, "--commit", COMMIT])
    assert release.main() == 1
    detail = capsys.readouterr().err
    assert "Forbidden" in detail and "storage.example/blob?<redacted>" in detail
    assert "secret" not in detail and "private" not in detail


def test_seal_records_original_producer_and_every_required_validator(remote, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", COMMIT)
    (tmp_path / "pyproject.toml").write_bytes(b'[project]\nversion = "0.2.3"\n')
    output = tmp_path / "validation.json"
    release.seal(123, 1, 31, output)
    manifest = json.loads(output.read_bytes())
    assert manifest["run"]["head_sha"] == COMMIT and manifest["run"]["run_attempt"] == 1
    assert manifest["artifact"]["id"] == 31
    assert manifest["jobs"] == {job["name"]: job["id"] for job in jobs()}
    assert manifest["files"] == {name: hashlib.sha256(payload).hexdigest() for name, payload in remote["contents"].items()}


@pytest.mark.parametrize("repository", [release.REPOSITORY, "fork-owner/research-repo-tools"])
def test_validator_retrieves_same_immutable_artifact_without_release_side_effects(remote, tmp_path, monkeypatch, repository):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_REPOSITORY", repository)
    remote["run"]["repository"]["full_name"] = repository
    (tmp_path / "pyproject.toml").write_bytes(b'[project]\nversion = "0.2.3"\n')
    release.consume(31, tmp_path / "dist")
    assert {path.name: path.read_bytes() for path in (tmp_path / "dist").iterdir()} == remote["contents"]
    assert not any(isinstance(call, tuple) and call[0] == "release" for call in remote["calls"])
    assert ("api-repository", repository) in remote["calls"]
    assert ("gh", "api", f"repos/{repository}/actions/artifacts/31/zip") in remote["calls"]
