"""Explicit benchmark release selection, independent of measurement and I/O."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from research_repo_tools.release_discovery import PublishedRelease, _tag_version, normalize_tag

type PairMode = Literal["explicit", "published-latest", "infer-release", "current-vs-latest"]
type ReleaseOrder = Literal["published", "version"]

__all__ = ["PairMode", "ReleaseOrder", "ReleasePair", "resolve_pair"]


@dataclass(frozen=True, slots=True)
class ReleasePair:
    current: str
    baseline: str

    def __post_init__(self) -> None:
        for name in ("current", "baseline"):
            object.__setattr__(self, name, normalize_tag(getattr(self, name)))

    @property
    def stem(self) -> str:
        return f"{self.current}-vs-{self.baseline}"


def resolve_pair(
    mode: PairMode,
    *,
    package_tag: str,
    releases: Sequence[PublishedRelease] = (),
    order: ReleaseOrder = "published",
    current: str | None = None,
    baseline: str | None = None,
) -> ReleasePair:
    """Select explicit, latest-published, local, or prospective release pairs.

    Publication chronology and numeric version order are distinct, explicit
    policies. A published package compares with its predecessor in that order.
    A prospective package must be newer than the selected latest release.
    Only current-vs-latest permits the same release label on both sources.
    """
    package_tag = normalize_tag(package_tag)
    if mode not in {"explicit", "published-latest", "infer-release", "current-vs-latest"}:
        raise ValueError(f"unknown release pair mode: {mode}")
    if order not in {"published", "version"}:
        raise ValueError("release order must be published or version")
    if mode == "explicit":
        if current is None or baseline is None:
            raise ValueError("explicit comparison requires both current and baseline tags")
        pair = ReleasePair(current, baseline)
    else:
        if current is not None or baseline is not None:
            raise ValueError(f"explicit tags cannot be combined with {mode}")
        if any(item.published_at.tzinfo is None for item in releases):
            raise ValueError("release timestamps must have a timezone")
        ordered = sorted(
            releases, key=lambda item: (item.published_at, _tag_version(item.tag)) if order == "published" else _tag_version(item.tag), reverse=True
        )
        tags = [normalize_tag(item.tag) for item in ordered]
        if len(tags) != len(set(tags)):
            raise ValueError("duplicate release tags")
        if not tags:
            raise ValueError("a published stable release is required")
        if mode == "published-latest":
            if len(tags) < 2:
                raise ValueError("at least two published stable releases are required")
            pair = ReleasePair(tags[0], tags[1])
        elif mode == "current-vs-latest":
            pair = ReleasePair(package_tag, tags[0])
        elif package_tag in tags:
            index = tags.index(package_tag)
            if index + 1 == len(tags):
                raise ValueError(f"published release {package_tag} has no previous stable release")
            pair = ReleasePair(package_tag, tags[index + 1])
        else:
            if _tag_version(package_tag) <= _tag_version(tags[0]):
                raise ValueError(f"unpublished package {package_tag} must be newer than {tags[0]}")
            pair = ReleasePair(package_tag, tags[0])
    if pair.current == pair.baseline and mode != "current-vs-latest":
        raise ValueError("current and baseline releases must differ")
    return pair
