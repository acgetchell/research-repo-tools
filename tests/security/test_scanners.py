"""Blocking native report/status contracts and full-history/current-file coverage."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from research_repo_tools import config, security


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
    finding = {"RuleID": "synthetic", "File": "new file.txt", "Secret": "REDACTED", "Match": "adjacent secret", "Message": "commit message secret"}
    sarif_finding = {
        "message": {"text": "synthetic"},
        "locations": [{"physicalLocation": {"region": {"snippet": {"text": "REDACTED"}}}}],
        "partialFingerprints": {"commitMessage": "commit message secret"},
    }

    def run(binary, args, **kwargs):
        seen.append(args)
        assert "--redact=100" in args and "--ignore-gitleaks-allow" in args
        assert not any(key.startswith("GITLEAKS_") for key in kwargs["env"])
        if args[0] == "dir":
            assert (Path(args[1]) / "new file.txt").read_bytes() == b"new uncommitted synthetic secret\r\n"
        else:
            assert "--log-opts=--all --full-history" in args
        fmt = args[args.index("--report-format") + 1]
        Path(args[args.index("--report-path") + 1]).write_bytes(json.dumps([finding] if fmt == "json" else sarif([sarif_finding])).encode())
        return subprocess.CompletedProcess([], 1, b"raw secret that must not be logged", b"")

    with (
        patch.object(security, "run_git_bytes", return_value=subprocess.CompletedProcess([], 0, b"false\n", b"")),
        patch.object(security, "security_inventory", return_value=("new file.txt",)),
        patch.object(security, "_binary", return_value=(Path("gitleaks"), {"GITLEAKS_CONFIG": "ambient"})),
        patch.object(security, "run_command_bytes", side_effect=run),
    ):
        assert security.scan_secrets(consumer) == 1
    assert len(seen) == 4
    for path in (consumer.root / "target/security").iterdir():
        assert b"adjacent secret" not in path.read_bytes()
        assert b"commit message secret" not in path.read_bytes()


def test_unredacted_json_rejected():
    with pytest.raises(ValueError, match="unredacted"):
        security._gitleaks_report([{"RuleID": "synthetic", "File": "file", "Secret": "not-redacted"}], "json")


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
