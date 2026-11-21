"""The installed public workflow suite also runs against the checkout."""

import os
from unittest.mock import patch

from tests.fixtures import notebooks
from tests.notebooks.public_workflows_consumer import TestLaunch as TestLaunch
from tests.notebooks.public_workflows_consumer import TestReset as TestReset


def test_git_fixture_isolates_repository_configuration_and_hooks(tmp_path, monkeypatch):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"):
        monkeypatch.setenv(key, "ambient repository selector")
    with patch.object(notebooks, "run_git_bytes") as git:
        notebooks.fixture_git(tmp_path, "init", "--template=")
    args, kwargs = git.call_args
    assert args[0][-2:] == ["init", "--template="]
    assert "core.hooksPath=" + str(tmp_path / "disabled-hooks") in args[0]
    assert "commit.gpgsign=false" in args[0]
    assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"}.intersection(kwargs["env"])
