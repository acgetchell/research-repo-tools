"""Just metadata boundary errors supplement the installed native consumer suite."""

import json
import subprocess
from importlib.metadata import version

import pytest

from research_repo_tools import just_inspect
from tests.utilities.public_just_consumer import TestJustInspection as TestJustInspection


@pytest.mark.parametrize(
    "output",
    [
        "{",
        "[]",
        '{"recipes":{},"recipes":{},"aliases":{}}',
        '{"recipes":{},"aliases":{},"other":NaN}',
        '{"recipes":{},"aliases":{},"other":1e999}',
        '{"recipes":[] ,"aliases":{}}',
        '{"recipes":{},"aliases":{"x":{"name":"x","target":3}}}',
        *[
            json.dumps({"recipes": {"check": recipe}, "aliases": {}})
            for recipe in (
                None,
                {"name": "wrong"},
                {"name": "check", "parameters": "bad"},
                {"name": "check", "parameters": [{"name": 1}]},
                {"name": "check", "parameters": [], "dependencies": [{"recipe": False}]},
                {"name": "check", "parameters": [], "dependencies": [], "body": ["bad"]},
            )
        ],
    ],
)
def test_malformed_native_metadata_is_rejected_through_public_api(tmp_path, monkeypatch, output):
    (tmp_path / "justfile").write_bytes(b"check:\n")

    def run(command, args, **kwargs):
        return subprocess.CompletedProcess([str(command), *args], 0, f"just {version('rust-just')}" if args == ["--version"] else output, "")

    monkeypatch.setattr(just_inspect, "run_command", run)
    with pytest.raises(ValueError, match="invalid Just JSON metadata"):
        just_inspect.inspect_justfile(tmp_path)


def test_version_drift_is_rejected_before_native_evaluation(tmp_path, monkeypatch):
    (tmp_path / "justfile").write_bytes(b"check:\n")

    def run(command, args, **kwargs):
        assert args == ["--version"]
        return subprocess.CompletedProcess([str(command), *args], 0, "just 0.0.1\n", "")

    monkeypatch.setattr(just_inspect, "run_command", run)
    with pytest.raises(ValueError, match="installed rust-just dependency"):
        just_inspect.dry_run(tmp_path, "check")
