"""Common stable GitHub release discovery and transactional text publication."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from research_repo_tools.files import replace_many
from research_repo_tools.process import run_safe_command

TAG = re.compile(r"v?(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)")


@dataclass(frozen=True)
class PublishedRelease:
    tag: str
    published_at: datetime


def normalize_tag(tag: str) -> str:
    match = TAG.fullmatch(tag)
    if match is None:
        raise ValueError(f"release tag must use stable vX.Y.Z form: {tag!r}")
    return f"v{match['major']}.{match['minor']}.{match['patch']}"


def _tag_version(tag: str) -> tuple[int, ...]:
    return tuple(map(int, normalize_tag(tag)[1:].split(".")))


def stable_published_releases(document: object) -> tuple[PublishedRelease, ...]:
    if not isinstance(document, list):
        raise ValueError("GitHub releases must be an array")
    releases: list[PublishedRelease] = []
    for entry in document:
        if not isinstance(entry, dict):
            raise ValueError("GitHub release must be an object")
        if type(entry.get("isDraft")) is not bool or type(entry.get("isPrerelease")) is not bool:
            raise ValueError("GitHub release draft/prerelease flags must be booleans")
        if entry["isDraft"] or entry["isPrerelease"]:
            continue
        tag = entry.get("tagName")
        if not isinstance(tag, str):
            raise ValueError("GitHub release tagName must be a string")
        if TAG.fullmatch(tag) is None:
            continue
        published = entry.get("publishedAt")
        if not isinstance(published, str):
            raise ValueError("published stable release requires publishedAt")
        timestamp = datetime.fromisoformat(published)
        if timestamp.tzinfo is None:
            raise ValueError("release publication time must include a timezone")
        normalized = normalize_tag(tag)
        if any(item.tag == normalized for item in releases):
            raise ValueError(f"duplicate published release: {tag}")
        releases.append(PublishedRelease(normalized, timestamp))
    return tuple(sorted(releases, key=lambda item: _tag_version(item.tag), reverse=True))


def _published_releases(root: Path) -> tuple[PublishedRelease, ...]:
    result = run_safe_command("gh", ["release", "list", "--limit", "1000", "--json", "tagName,isDraft,isPrerelease,publishedAt"], cwd=root)
    return stable_published_releases(json.loads(result.stdout))


def _publish_texts(updates: tuple[tuple[Path, str], ...]) -> None:
    replace_many({path: text.encode("utf-8") for path, text in updates})
