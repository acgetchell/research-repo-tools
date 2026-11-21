"""Blocking native report/status contracts and full-history/current-file coverage."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from research_repo_tools import config, security
from research_repo_tools.process import run_git_command
from tests.fixture_environment import git_environment


@pytest.fixture
def consumer(tmp_path):
    (tmp_path / "uv.lock").write_bytes(b"version = 1\n")
    (tmp_path / "new file.txt").write_bytes(b"new uncommitted synthetic secret\r\n")
    return config.Config(root=tmp_path)


def sarif(findings=()):
    return {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "scanner"}}, "results": list(findings)}]}


@pytest.mark.parametrize("behavior,expected", [("ok", 0), ("findings", 1), ("error", 23), ("missing", 1), ("malformed", 1), ("incomplete", 1)])
def test_osv_fresh_report_coverage_and_exit_status(consumer, behavior, expected):
    destination = consumer.root / "target/security"
    destination.mkdir(parents=True)
    stale = destination / "osv-0.json"
    stale.write_bytes(b"old success report")
    seen = []

    def run(binary, args, **kwargs):
        seen.append(args)
        assert "--no-call-analysis=go" in args and "--no-call-analysis=rust" in args
        assert args[args.index("--lockfile") + 1] == ":" + str((consumer.root / "uv.lock").resolve())
        path = Path(args[args.index("--output-file") + 1])
        fmt = args[args.index("--format") + 1]
        result = {
            "results": [
                {
                    "source": {"path": str(consumer.root / "uv.lock")},
                    "packages": [{"package": {"name": "fixture"}, "vulnerabilities": [{"id": "FAKE-1"}] if behavior == "findings" else []}],
                }
            ]
        }
        if behavior == "incomplete":
            result["results"] = []
        if behavior != "missing":
            path.write_bytes(b"invalid" if behavior == "malformed" else json.dumps(result if fmt == "json" else sarif()).encode())
        return subprocess.CompletedProcess([], 23 if behavior == "error" else 0, b"", b"")

    with (
        patch.object(security, "security_inventory", return_value=("uv.lock",)),
        patch.object(security, "_binary", return_value=(Path("osv"), {})),
        patch.object(security, "run_command_bytes", side_effect=run),
    ):
        assert security.scan_osv(consumer, ("uv.lock",)) == expected
    assert len(seen) == 2
    if behavior in {"missing", "malformed", "incomplete"}:
        assert not stale.exists()
    else:
        assert stale.read_bytes() != b"old success report"


def test_osv_missing_inputs_dont_execute(consumer):
    with patch.object(security, "security_inventory", return_value=("uv.lock",)), patch.object(security, "_binary") as binary, pytest.raises(ValueError):
        security.scan_osv(consumer, ("Cargo.lock",))
    binary.assert_not_called()


def test_shallow_history_blocks_before_scanner(consumer):
    with (
        patch.object(security, "run_git_bytes", return_value=subprocess.CompletedProcess([], 0, b"true\r\n", b"")),
        patch.object(security, "_binary") as binary,
        pytest.raises(ValueError, match="non-shallow"),
    ):
        security.scan_secrets(consumer)
    binary.assert_not_called()


def test_uncommitted_files_and_redacted_reports(consumer):
    seen = []
    finding = {
        "RuleID": "synthetic",
        "File": "new file.txt",
        "Secret": "REDACTED",
        "Match": "adjacent secret",
        "Message": "commit message secret",
        "Fragment": "surrounding source",
        "StartLine": 1,
        "StartColumn": 1,
        "EndLine": 1,
        "EndColumn": 5,
    }
    sarif_finding = {
        "message": {"text": "synthetic"},
        "locations": [{"physicalLocation": {"region": {"snippet": {"text": "REDACTED"}}}}],
        "partialFingerprints": {"commitMessage": "commit message secret"},
    }
    # Summaries and exception comparisons only read finding identities.
    original = finding.copy()
    with patch.object(security, "_load_report", return_value=[finding]):
        identities = security._secret_findings(Path("report.json"), "working", consumer.root)
    assert [item.rule for item in identities] == ["synthetic"]
    assert finding == original

    def run(binary, args, **kwargs):
        seen.append(args)
        assert {"--redact=100", "--ignore-gitleaks-allow", "--exit-code=3"}.issubset(args)
        assert not any(key.startswith("GITLEAKS_") for key in kwargs["env"])
        if args[0] == "dir":
            assert args[1] == "."
            assert (kwargs["cwd"] / "new file.txt").read_bytes() == b"new uncommitted synthetic secret\r\n"
        else:
            assert "--log-opts=--all --full-history --diff-merges=first-parent --no-renames" in args
        fmt = args[args.index("--report-format") + 1]
        record = finding | {"Commit": "a" * 40} if args[0] == "git" else finding
        Path(args[args.index("--report-path") + 1]).write_bytes(json.dumps([record] if fmt == "json" else sarif([sarif_finding])).encode())
        return subprocess.CompletedProcess([], 3, b"raw secret that must not be logged", b"")

    def git(args, **kwargs):
        if "config" in args:
            return subprocess.CompletedProcess(args, 1, b"", b"")
        return subprocess.CompletedProcess(args, 0, b"a" * 40 + b"\n" if "rev-list" in args else b"false\n", b"")

    with (
        patch.object(security, "run_git_bytes", side_effect=git),
        patch.object(security, "security_inventory", return_value=("new file.txt",)),
        patch.object(security, "_binary", return_value=(Path("gitleaks"), {"GITLEAKS_CONFIG": "ambient"})),
        patch.object(security, "run_command_bytes", side_effect=run),
    ):
        assert security.scan_secrets(consumer) == 1
    assert len(seen) == 4
    reports = list((consumer.root / "target/security").iterdir())
    assert len(reports) == 4
    for scope in ("working", "history"):
        expected = finding | {"Match": "REDACTED", "Message": "REDACTED", "Fragment": None}
        if scope == "history":
            expected["Commit"] = "a" * 40
        assert json.loads((consumer.root / f"target/security/gitleaks-{scope}.json").read_bytes()) == [expected]
        assert json.loads((consumer.root / f"target/security/gitleaks-{scope}.sarif").read_bytes()) == sarif(
            [sarif_finding | {"partialFingerprints": {"commitMessage": "REDACTED"}}]
        )


def test_unredacted_json_rejected():
    with pytest.raises(ValueError, match="unredacted"):
        security._redact_gitleaks_report([{"RuleID": "synthetic", "File": "file", "Secret": "not-redacted"}], "json", scope="working")


def test_scanner_does_not_publish_after_serialization_exceeds_read_limit(tmp_path):
    destination = tmp_path / "report.json"
    destination.write_bytes(b"old report")
    payload = b'{"values":[1,2,3]}'

    def native(_binary, args, **_kwargs):
        Path(args[0]).write_bytes(payload)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with patch.object(security, "REPORT_LIMIT", len(payload)), patch.object(security, "run_command_bytes", side_effect=native):
        assert (
            security._report_run(Path("scanner"), ["{report}"], destination, "json", root=tmp_path, env={}, prepare_report=lambda value, fmt: (value, False))
            == 1
        )
    assert not destination.exists()


def pinned_gitleaks() -> Path:
    executable = shutil.which("gitleaks")
    if executable is None or os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1":
        pytest.skip("requires pinned Gitleaks and a disposable Git checkout")
    binary = Path(executable).resolve()
    version = subprocess.run([binary, "version"], capture_output=True, check=True, timeout=30)
    if version.stdout.strip() != b"8.30.1":
        pytest.skip("requires Gitleaks 8.30.1")
    return binary


def test_policy_rejects_ambiguous_default_without_extension_path(tmp_path):
    policy = tmp_path / ".gitleaks.toml"
    policy.write_bytes(b"[extend]\nuseDefault = true\nusedefault = false\n")
    stage = tmp_path / "snapshot"
    stage.mkdir()
    with pytest.raises(ValueError, match="ambiguous usedefault keys"):
        security._GitleaksPolicySnapshot(tmp_path, stage).retain(policy)
    assert not list(stage.iterdir())


def test_partial_clone_identifies_missing_history_before_scanning(tmp_path):
    env = git_environment()
    source = tmp_path / "source"
    source.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()

    def git(*args, cwd=source):
        return run_git_command(list(args), cwd=cwd, env=env).stdout.strip()

    git("init", "--quiet", "--template=")
    (source / "fixture.txt").write_bytes(b"original blob\n")
    git("add", "fixture.txt")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    oid = git("rev-parse", "HEAD:fixture.txt")
    git("config", "uploadpack.allowFilter", "true")
    partial = tmp_path / "partial"
    git("clone", "--no-checkout", "--filter=blob:none", source.as_uri(), str(partial))
    with pytest.raises(ValueError, match=f"partial clone .* lacks historical Git object {oid}"):
        security._capture_gitleaks_history(partial, stage, env)
    assert not (stage / "history.git").exists()
    assert git("cat-file", "blob", oid, cwd=partial) == "original blob"
    mirror, verify = security._capture_gitleaks_history(partial, stage, env)
    assert git("cat-file", "blob", oid, cwd=mirror) == "original blob"
    verify()


def test_pinned_gitleaks_matches_anchored_working_paths_with_relative_extensions(tmp_path, capsys):
    binary = pinned_gitleaks()

    root = tmp_path.resolve() / "repository"
    root.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(*args: str, cwd: Path = root) -> str:
        return run_git_command(list(args), cwd=cwd, env=env).stdout.strip()

    git("init", "--quiet", "--template=")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "diff.renames", "false")
    (root / "policies").mkdir()
    (root / "policies" / "shared.toml").write_bytes(b"extend.path = 'leaf.toml'\n")
    (root / "leaf.toml").write_bytes(
        rb"""[[rules]]
id = 'fixture-rule'
path = '^src[/\\]'
regex = 'synthetic-scanner-match'
"""
    )
    (root / ".gitleaks.toml").write_bytes(b"[extend]\npath = 'policies/shared.toml'\n")
    (root / "candidate.toml").write_bytes(b"Extend = { Path = 'policies/shared.toml' }\n")
    (root / "src").mkdir()
    (root / "src" / "new.txt").write_bytes(b"synthetic-scanner-match\n")
    (root / "outside.txt").write_bytes(b"synthetic-scanner-match\n")
    git("add", "src/new.txt", "outside.txt")
    git("commit", "-qm", "fixture")
    git("mv", "outside.txt", "src/renamed.txt")
    git("commit", "-qm", "rename fixture")
    renamed_commit = git("rev-parse", "HEAD")
    git("branch", "private-fixture", renamed_commit)
    hidden_ref = "refs/heads/private-fixture"
    git("config", "uploadpack.hideRefs", hidden_ref)
    assert git("ls-remote", str(root), hidden_ref) == ""
    git("rm", "src/renamed.txt")
    git("commit", "-qm", "remove rename fixture from the working tree")
    # A merge-only finding vanishes from the checkout. A local attributes rule
    # makes this file a text diff despite its committed binary declaration.
    primary = git("branch", "--show-current")
    (root / ".gitattributes").write_bytes(b"src/merge.txt -diff\n")
    git("add", ".gitattributes")
    git("commit", "-qm", "declare binary diff")
    git("switch", "-qc", "side-fixture")
    (root / "side.txt").write_bytes(b"side branch\n")
    git("add", "side.txt")
    git("commit", "-qm", "side branch")
    git("switch", "-q", primary)
    git("merge", "--no-ff", "--no-commit", "side-fixture")
    (root / "src" / "merge.txt").write_bytes(b"synthetic-scanner-match\n")
    git("add", "src/merge.txt")
    git("commit", "-qm", "merge fixture")
    merged_commit = git("rev-parse", "HEAD")
    git("rm", "src/merge.txt")
    git("commit", "-qm", "remove merge fixture")
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "attributes").write_bytes(b"src/merge.txt diff\n")
    captured_commit = git("rev-parse", "HEAD")
    (root / "src" / "late.txt").write_bytes(b"synthetic-scanner-match\n")
    git("add", "src/late.txt")
    git("commit", "-qm", "transient fixture")
    transient_commit = git("rev-parse", "HEAD")
    git("reset", "--hard", captured_commit)
    linked = root.parent / "linked worktree"
    git("worktree", "add", "--quiet", "--detach", str(linked), captured_commit)
    (linked / "src" / "linked.txt").write_bytes(b"synthetic-scanner-match\n")
    git("add", "src/linked.txt", cwd=linked)
    git("commit", "-qm", "linked worktree fixture", cwd=linked)
    linked_commit = git("rev-parse", "HEAD", cwd=linked)
    settings = config.Config(root=root)

    native = security.run_command_bytes
    history_scans: list[Path] = []

    def scan(binary, args, **kwargs):
        if args[0] == "git":
            history_scans.append(kwargs["cwd"])
            assert git("rev-parse", "--verify", hidden_ref, cwd=kwargs["cwd"]) == renamed_commit
            if len(history_scans) == 2:
                git("update-ref", "refs/heads/transient", transient_commit)
                try:
                    return native(binary, args, **kwargs)
                finally:
                    git("update-ref", "-d", "refs/heads/transient")
        return native(binary, args, **kwargs)

    hook_environment = env | {"GIT_DIR": str(root / ".git"), "GIT_WORK_TREE": str(root)}
    with patch.dict(os.environ, hook_environment, clear=True), patch.object(security, "_binary", return_value=(binary, hook_environment)):
        with patch.object(security, "run_command_bytes", side_effect=scan):
            security.check_secret_exceptions(settings, "candidate.toml")
        output = capsys.readouterr().out
        assert "Suppressed: 0 finding(s)" in output
        assert "Introduced: 0 finding(s)" in output
        assert "Matching locations: 5 finding(s)" in output
        assert "linked.txt" in output and f"at commit {linked_commit}" in output
        assert 'history "src/renamed.txt"' in output and f"at commit {renamed_commit}" in output
        assert 'working "src/new.txt"' in output
        assert "late.txt" not in output
        assert len(history_scans) == 2 and history_scans[0] == history_scans[1] != root
        assert "synthetic-scanner-match" not in output

        assert security.scan_secrets(settings) == 1
        report = root / "target" / "security" / "gitleaks-working.json"
        assert [finding["RuleID"] for finding in json.loads(report.read_bytes())] == ["fixture-rule"]
        native_history = json.loads(report.with_name("gitleaks-history.json").read_bytes())
        assert any(Path(finding["File"]).name == "renamed.txt" and finding["Commit"] == renamed_commit for finding in native_history)
        assert any(Path(finding["File"]).name == "merge.txt" and finding["Commit"] == merged_commit for finding in native_history)
        (root / ".gitleaksignore").write_bytes(b"src/new.txt:fixture-rule:1\n")
        with pytest.raises(ValueError, match=r"\.gitleaksignore.*absent"):
            security.check_secret_exceptions(settings, "candidate.toml")
        with pytest.raises(ValueError, match=r"\.gitleaksignore.*absent"):
            security.scan_secrets(settings)
        assert not report.exists()


def test_pinned_gitleaks_scopes_the_documented_allowlist(tmp_path, capsys):
    binary = pinned_gitleaks()
    runbook = (Path(__file__).resolve().parents[2] / "docs" / "RUNNING_CHECKS.md").read_text(encoding="utf-8")
    policy = next(block for block in re.findall(r"```toml\n(.*?)```", runbook, re.S) if "Reviewed numeric benchmark statistics" in block)
    match = re.search(r"regexes = \['\^([A-Za-z0-9]+)\$'\]", policy)
    assert match is not None
    root = tmp_path.resolve()
    (root / "proposed.toml").write_bytes(policy.encode("utf-8"))
    intended = "docs/performance-runs/example/run.json"
    nested = f"examples/{intended}"
    for name in (intended, nested):
        path = root / name
        path.parent.mkdir(parents=True)
        path.write_bytes(json.dumps({"api_key": match[1]}).encode("utf-8"))
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    with patch.dict(os.environ, env, clear=True), patch.object(security, "_binary", return_value=(binary, env)):
        run_git_command(["init", "--quiet", "--template="], cwd=root)
        run_git_command(["add", "--", intended, nested], cwd=root)
        run_git_command(
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=root
        )
        security.check_secret_exceptions(config.Config(root=root), "proposed.toml")
    output = capsys.readouterr().out
    suppressed, matching = output.split("Introduced: 0 finding(s)")
    assert "Suppressed: 2 finding(s)" in suppressed
    assert f'history "{intended}"' in suppressed and f'working "{intended}"' in suppressed
    assert nested not in suppressed
    assert "Matching locations: 2 finding(s)" in matching
    assert f'history "{nested}"' in matching and f'working "{nested}"' in matching
    assert match[1] not in output


def test_generated_inventory_exclusions(tmp_path):
    with patch.object(security, "select_files", return_value=()) as select:
        security.security_inventory(tmp_path, exclude=("private/**",))
    assert "**/.venv/**" in select.call_args.kwargs["exclude"]
    assert "private/**" in select.call_args.kwargs["exclude"]


@pytest.mark.parametrize("invocations", ["invalid", {}, [None], [{}], [{"executionSuccessful": "false"}]])
def test_malformed_sarif_invocations_are_rejected(invocations):
    value = sarif()
    value["runs"][0]["invocations"] = invocations
    with pytest.raises(ValueError, match="SARIF invocation"):
        security._sarif(value)
