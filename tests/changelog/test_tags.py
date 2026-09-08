"""Tag syntax has one canonical SemVer contract."""

import pytest

from research_repo_tools.release_tags import _github_repo_url, _heading_to_anchor, validate_semver


@pytest.mark.parametrize("tag", ["v0.0.0", "v1.2.3", "v1.2.3-alpha.1", "v1.2.3-rc.10+build.01"])
def test_canonical_semver_tags(tag):
    validate_semver(tag)


@pytest.mark.parametrize("tag", ["1.2.3", "v01.2.3", "v1.02.3", "v1.2.03", "v1.2.3-01", "v1.2.3-alpha..1", "v1.2.3+", "v1.2.3\n", "v1.2.٣", " v1.2.3"])
def test_noncanonical_tags_fail(tag):
    with pytest.raises(ValueError, match="SemVer"):
        validate_semver(tag)


@pytest.mark.parametrize("path", ["owner/../repo", "owner/repo/extra", "owner/", "owner/repo?query", "owner/repo\n"])
def test_invalid_repository_components_fail(path):
    with pytest.raises(ValueError):
        _github_repo_url(path)


def test_repository_url_and_release_anchor():
    assert _github_repo_url("owner/repo.git") == "https://github.com/owner/repo"
    assert _heading_to_anchor("## [1.2.3](https://example.invalid/release) - 2026-09-07") == "123---2026-09-07"
