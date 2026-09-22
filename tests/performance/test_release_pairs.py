"""Release order and prospective/current package selection are distinct policies."""

from datetime import UTC, datetime

import pytest

from research_repo_tools.release_pairs import ReleasePair, resolve_pair
from research_repo_tools.releases import PublishedRelease

RELEASES = (
    PublishedRelease("v2.0.0", datetime(2026, 1, 1, tzinfo=UTC)),
    PublishedRelease("v1.1.0", datetime(2026, 2, 1, tzinfo=UTC)),
    PublishedRelease("v1.0.0", datetime(2025, 1, 1, tzinfo=UTC)),
)


def test_publication_chronology_is_not_numeric_version_order() -> None:
    assert resolve_pair("published-latest", package_tag="v2.1.0", releases=RELEASES) == ReleasePair("v1.1.0", "v2.0.0")
    assert resolve_pair("published-latest", package_tag="v2.1.0", releases=RELEASES, order="version") == ReleasePair("v2.0.0", "v1.1.0")


def test_release_preparation_distinguishes_published_and_prospective() -> None:
    assert resolve_pair("infer-release", package_tag="v1.1.0", releases=RELEASES) == ReleasePair("v1.1.0", "v2.0.0")
    assert resolve_pair("infer-release", package_tag="v3.0.0", releases=RELEASES) == ReleasePair("v3.0.0", "v1.1.0")
    with pytest.raises(ValueError, match="newer"):
        resolve_pair("infer-release", package_tag="v0.9.0", releases=RELEASES)
    with pytest.raises(ValueError, match="previous"):
        resolve_pair("infer-release", package_tag="v1.0.0", releases=RELEASES)


def test_same_label_allowed_only_for_local_comparison() -> None:
    assert resolve_pair("current-vs-latest", package_tag="v1.1.0", releases=RELEASES) == ReleasePair("v1.1.0", "v1.1.0")
    with pytest.raises(ValueError, match="differ"):
        resolve_pair("explicit", package_tag="v1.1.0", current="v1.1.0", baseline="v1.1.0")


def test_explicit_pair_needs_both_tags_and_no_discovery() -> None:
    assert resolve_pair("explicit", package_tag="v3.0.0", current="v2.0.0", baseline="v1.0.0") == ReleasePair("v2.0.0", "v1.0.0")
    with pytest.raises(ValueError, match="both"):
        resolve_pair("explicit", package_tag="v3.0.0", current="v2.0.0")
    with pytest.raises(ValueError, match="two"):
        resolve_pair("published-latest", package_tag="v3.0.0", releases=RELEASES[:1])
