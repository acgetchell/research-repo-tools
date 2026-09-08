"""Release tag syntax, GitHub URLs, and Markdown anchors."""

import re

_GITHUB_TAG_ANNOTATION_LIMIT = 125000
_GITHUB_REPO_COMPONENT_RE = re.compile("^[A-Za-z0-9_.-]+$")
_ALNUM_ID = "(?:(?=[0-9A-Za-z-]*[A-Za-z-])[0-9A-Za-z-]+)"
_SEMVER_RE = re.compile(
    f"^v(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-(?:(?:0|[1-9]\\d*)|{_ALNUM_ID})(?:\\.(?:(?:0|[1-9]\\d*)|{_ALNUM_ID}))*)?(?:\\+[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?$"
)


def validate_semver(tag_version: str) -> None:
    """
    Validate that tag_version matches SemVer vX.Y.Z format (requires a leading 'v' and allows optional prerelease/metadata).

    Parameters:
        tag_version (str): Tag string to validate; must start with 'v' followed by
            MAJOR.MINOR.PATCH (e.g., v1.2.3) and may include prerelease or build metadata.

    Raises:
        ValueError: If tag_version does not conform to the expected SemVer pattern.
    """
    if not tag_version.isascii() or not _SEMVER_RE.fullmatch(tag_version):
        msg = f"Tag version should follow SemVer format 'vX.Y.Z' (e.g., v0.3.5, v1.2.3-rc.1). Got: {tag_version}"
        raise ValueError(msg)


def _github_repo_url(path: str) -> str:
    """Return a canonical GitHub URL for a validated two-component path."""
    normalized = path.strip("/").removesuffix(".git")
    components = normalized.split("/")
    if (
        len(components) != 2
        or any((component in {"", ".", ".."} for component in components))
        or any((_GITHUB_REPO_COMPONENT_RE.fullmatch(component) is None for component in components))
    ):
        msg = "Origin remote must identify exactly one GitHub owner and repository."
        raise ValueError(msg)
    return f"https://github.com/{components[0]}/{components[1]}"


def _heading_to_anchor(heading_line: str) -> str:
    """Convert a markdown heading line to a GitHub-compatible anchor slug."""
    heading = heading_line.removeprefix("## ").strip()
    heading = re.sub("\\[([^\\]]+)\\]\\([^)]+\\)", "\\1", heading)
    heading = re.sub("\\[([^\\]]+)\\]", "\\1", heading)
    heading = heading.lower()
    heading = re.sub("[^a-z0-9\\s-]", "", heading)
    return re.sub("\\s+", "-", heading)
