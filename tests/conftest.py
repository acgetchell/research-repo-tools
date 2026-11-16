"""Shared controls for pinned tools and disposable Git test fixtures."""

import os
from collections.abc import Iterator

import pytest

from tests.fixture_environment import git_environment, rust_channel


@pytest.fixture(scope="session", autouse=True)
def pinned_rust_toolchain() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("RUSTUP_TOOLCHAIN", rust_channel())
        yield


@pytest.fixture
def git_mutations_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1":
        pytest.skip("Git mutations disabled by RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS=1")
    environment = git_environment()
    for name in tuple(os.environ):
        if name.upper().startswith("GIT_"):
            monkeypatch.delenv(name)
    for name, value in environment.items():
        if name.upper().startswith("GIT_"):
            monkeypatch.setenv(name, value)
