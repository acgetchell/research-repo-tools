"""Shared controls for test fixtures that mutate disposable Git repositories."""

import os

import pytest


@pytest.fixture
def git_mutations_allowed() -> None:
    if os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1":
        pytest.skip("Git mutations disabled by RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS=1")
