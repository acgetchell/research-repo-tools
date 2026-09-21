"""The standalone consumer suite honors the same Git policy as pytest fixtures."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("setting", [None, "1"], ids=["default", "skip-git"])
def test_standalone_git_fixture_selection(setting: str | None, tmp_path: Path) -> None:
    # Import the standalone suite in isolation, as the installation checker does.
    # Never execute its Git fixtures when the opt-out is unset. With the opt-out
    # enabled, fail closed if any test body reaches a subprocess instead of skipping.
    script = """
import os, runpy, sys, unittest
from unittest.mock import patch
consumer = runpy.run_path(sys.argv[1])["TestGitPublicationConsumer"]
skip = sys.argv[2] == "skip-git"
assert os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == ("1" if skip else None)
assert bool(getattr(consumer, "__unittest_skip__", False)) == skip
suite = unittest.defaultTestLoader.loadTestsFromTestCase(consumer)
assert suite.countTestCases() == 2
if skip:
    with patch("subprocess.run", side_effect=AssertionError("Git fixture executed")):
        result = unittest.TestResult()
        suite.run(result)
    assert result.wasSuccessful(), (result.failures, result.errors)
    assert result.testsRun == len(result.skipped) == 2
"""
    suite = Path(__file__).with_name("public_publication_consumer.py")
    environment = os.environ.copy()
    environment.pop("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS", None)
    if setting is not None:
        environment["RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS"] = setting
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(suite), "skip-git" if setting == "1" else "default"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
