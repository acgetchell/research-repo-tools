# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Run one exact shared release or verified GitHub commit through isolated uv.

This module is also exported verbatim as python-bootstrap.py. It stays usable
before installing research-repo-tools; Git metadata parsing requires Python 3.11.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

PACKAGE = "research-repo-tools"
_LIMIT = 1024 * 1024
_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
_REPOSITORY = r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*"

__all__ = ["GitHubPin", "ReleasePin", "parse_source", "run_tooling"]


@dataclass(frozen=True)
class ReleasePin:
    """An exact version selector; availability is checked immediately before run."""

    version: str

    def __post_init__(self):
        if not isinstance(self.version, str) or re.fullmatch(_VERSION, self.version) is None:
            raise ValueError("release requires an exact canonical X.Y.Z package version, optionally with a pre/post/dev suffix")


@dataclass(frozen=True)
class GitHubPin:
    """A public GitHub repository and full commit selector, never a branch/tag."""

    repository: str
    commit: str

    def __post_init__(self):
        if not isinstance(self.repository, str) or re.fullmatch(_REPOSITORY, self.repository) is None or self.repository.endswith(".git"):
            raise ValueError("GitHub repository must be owner/repository without a URL, credentials, query or .git suffix")
        if not isinstance(self.commit, str) or re.fullmatch(r"[0-9a-f]{40}", self.commit) is None:
            raise ValueError("GitHub commit must be a full lowercase 40-character SHA")


Source = Union[ReleasePin, GitHubPin]


def parse_source(*, release: str | None = None, github: str | None = None, commit: str | None = None) -> Source:
    """Parse exactly one source form; reject incomplete and competing selections."""
    if release is not None and github is None and commit is None:
        return ReleasePin(release)
    if release is None and github is not None and commit is not None:
        return GitHubPin(github, commit)
    raise ValueError("select either --version VERSION or both --github OWNER/REPOSITORY and --commit SHA")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # ty: ignore[missing-override-decorator]  Standalone Python 3.8 has no typing.override.
        return None


def _read(url: str) -> bytes:
    headers = {"User-Agent": "research-repo-tools-bootstrap"}
    token = os.environ.get("RRT_GITHUB_LOOKUP_TOKEN") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        # http.client includes invalid header values in its exceptions.
        if re.fullmatch(r"[\x21-\x7e]+", token) is None:
            raise ValueError("GitHub lookup credential must contain only visible ASCII without whitespace")
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200 or response.url != url:
                raise ValueError("source lookup returned an unexpected status or redirect")
            data = response.read(_LIMIT + 1)
            if not isinstance(data, bytes):
                raise ValueError("source response must contain bytes")
            if len(data) > _LIMIT:
                raise ValueError("source metadata exceeds the byte limit")
            return data
    except urllib.error.HTTPError as error:
        error.close()
        raise ValueError(f"requested source does not exist or is unavailable (HTTP {error.code})") from error
    except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
        raise ValueError("source lookup failed; no tooling was started") from error


def _json(data: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("source metadata has duplicate keys")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("source metadata contains a non-finite number")

    value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("source metadata must be an object")
    return value


def _python(value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\s*(?:~=|==|!=|<=|>=|<|>)\s*[0-9]+(?:\.[0-9]+)*(?:\.\*)?(?:\s*,\s*(?:~=|==|!=|<=|>=|<|>)\s*[0-9]+(?:\.[0-9]+)*(?:\.\*)?)*\s*", value)
        is None
    ):
        raise ValueError("source metadata must declare a numeric Requires-Python constraint")
    return value


@dataclass(frozen=True)
class _ResolvedSource:
    requirement: str
    python: str


def _resolve(source: Source, *, metadata_file: Path | None = None) -> _ResolvedSource:
    if isinstance(source, ReleasePin):
        data = _json(metadata_file.read_bytes() if metadata_file else _read(f"https://pypi.org/pypi/{PACKAGE}/{source.version}/json"))
        info = data.get("info")
        if not isinstance(info, dict) or info.get("name") != PACKAGE or info.get("version") != source.version:
            raise ValueError("release metadata must identify the exact requested research-repo-tools version")
        if metadata_file is None:
            uploads = data.get("urls")
            if not isinstance(uploads, list) or not uploads or any(not isinstance(item, dict) for item in uploads):
                raise ValueError("requested release has no valid published artifacts")
            if not any(
                item.get("yanked") is False
                and type(item.get("size")) is int
                and item["size"] > 0
                and item.get("packagetype") in ("sdist", "bdist_wheel")
                and isinstance(item.get("url"), str)
                and item["url"].startswith("https://files.pythonhosted.org/")
                for item in uploads
            ):
                raise ValueError("requested release has no available non-yanked artifact")
        return _ResolvedSource(f"{PACKAGE}=={source.version}", _python(info.get("requires_python")))
    if not isinstance(source, GitHubPin):
        raise TypeError("source must be a ReleasePin or GitHubPin")
    if metadata_file is not None:
        raise ValueError("saved release metadata cannot verify a GitHub commit")
    data = _json(_read(f"https://api.github.com/repos/{source.repository}/git/commits/{source.commit}"))
    if data.get("sha") != source.commit:
        raise ValueError("GitHub did not return the exact requested commit")
    if sys.version_info >= (3, 11):
        import tomllib

        document = tomllib.loads(_read(f"https://raw.githubusercontent.com/{source.repository}/{source.commit}/pyproject.toml").decode("utf-8"))
    else:
        raise ValueError("GitHub bootstrap requires Python 3.11 or newer; run this script through uv with --python 3.15")
    project = document.get("project")
    if not isinstance(project, dict) or project.get("name") != PACKAGE:
        raise ValueError("GitHub commit must contain the research-repo-tools project at its root")
    ReleasePin(project.get("version"))
    return _ResolvedSource(f"{PACKAGE} @ git+https://github.com/{source.repository}.git@{source.commit}", _python(project.get("requires-python")))


def _strings(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{context} must be a tuple of strings without NUL")
    result = []
    for item in value:
        if not isinstance(item, str) or "\x00" in item:
            raise ValueError(f"{context} must be a tuple of strings without NUL")
        result.append(item)
    return tuple(result)


def run_tooling(source: Source, arguments: tuple[str, ...], *, root: Path, release_intent: bool = False, extras: tuple[str, ...] = ()) -> int:
    """Verify source existence, then run isolated tooling without rewriting pins.

    Release intent rejects Git before any lookup or process. The invoked release
    command still owns checkout/lock/installed-byte policy and evidence checks.
    """
    if type(release_intent) is not bool:
        raise ValueError("release_intent must be a boolean")
    arguments = _strings(arguments, "tooling arguments")
    if not arguments:
        raise ValueError("tooling arguments must be nonempty")
    extras = _strings(extras, "tooling extras")
    if any(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", extra) is None for extra in extras) or len(set(extras)) != len(extras):
        raise ValueError("tooling extras must be unique canonical extra names")
    if release_intent and not isinstance(source, ReleasePin):
        raise ValueError("release intent requires an exact published version; GitHub commits are development-only")
    resolved = _resolve(source)
    return _execute(resolved, arguments, root=root, extras=extras)


def _execute(resolved: _ResolvedSource, arguments: tuple[str, ...], *, root: Path, local_artifact: bool = False, extras: tuple[str, ...] = ()) -> int:
    # uv configuration must not redirect a verified PyPI release to a local or
    # private source with the same version. Legacy offline adoption is explicit.
    environment = (
        dict(os.environ)
        if local_artifact
        else {key: value for key, value in os.environ.items() if not key.upper().startswith("UV_") or key.upper() in {"UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"}}
    )
    # The Action's lookup credential is not an authorization grant to the
    # selected tooling. Explicit caller-supplied tooling credentials remain.
    environment = {key: value for key, value in environment.items() if key.upper() != "RRT_GITHUB_LOOKUP_TOKEN"}
    requirement = resolved.requirement
    if extras:
        requirement = PACKAGE + "[" + ",".join(sorted(extras)) + "]" + requirement[len(PACKAGE) :]
    return subprocess.run(
        [
            "uvx",
            "--no-config",
            "--isolated",
            "--managed-python",
            "--python",
            resolved.python,
            "--default-index",
            "https://pypi.org/simple",
            "--from",
            requirement,
            PACKAGE,
            "--root",
            str(root.resolve()),
            *arguments,
        ],
        env=environment,
        check=False,
        timeout=7200,
    ).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="exact release for legacy Python adoption")
    parser.add_argument("--version", dest="release", help="exact published research-repo-tools version")
    parser.add_argument("--github", help="public GitHub owner/repository")
    parser.add_argument("--commit", help="full lowercase 40-character Git commit SHA")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--extras", default="", help="comma-separated optional package extras, such as python-tools")
    parser.add_argument("--release-intent", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="preview toolchain adoption")
    mode.add_argument("--apply", action="store_true", help="apply toolchain adoption")
    mode.add_argument("--arguments-json", help="nonempty JSON array of tooling command arguments")
    parser.add_argument("--metadata-file", type=Path, help="saved exact metadata for legacy offline/local adoption only")
    args = parser.parse_args(argv)
    try:
        if args.version is not None and args.release is not None:
            raise ValueError("supply the release once")
        source = parse_source(release=args.version if args.version is not None else args.release, github=args.github, commit=args.commit)
        if args.arguments_json is not None:
            arguments = json.loads(args.arguments_json)
            if not isinstance(arguments, list):
                raise ValueError("--arguments-json must be an array")
            if args.metadata_file is not None:
                raise ValueError("saved metadata is only supported for legacy offline adoption")
            return run_tooling(
                source, tuple(arguments), root=args.root, release_intent=args.release_intent, extras=tuple(args.extras.split(",")) if args.extras else ()
            )
        arguments = ("toolchain", "adopt", "--dry-run" if args.dry_run else "--apply")
        if args.metadata_file is not None:
            if args.extras:
                raise ValueError("legacy offline adoption does not accept extras")
            if args.release_intent:
                raise ValueError("saved metadata does not prove publication for release intent")
            return _execute(_resolve(source, metadata_file=args.metadata_file), arguments, root=args.root, local_artifact=True)
        return run_tooling(source, arguments, root=args.root, release_intent=args.release_intent, extras=tuple(args.extras.split(",")) if args.extras else ())
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
        print("Python adoption bootstrap: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
