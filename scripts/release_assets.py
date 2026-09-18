"""Stage validated distributions on a draft release and verify them for PyPI.

Runs with the runner's Python and GitHub CLI; never installs or imports the
package being published. Signed provenance is checked by gh, not parsed as trust
by this script. Only the preparation job may mint that provenance after CI.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY = "acgetchell/research-repo-tools"
SIGNER = f"{REPOSITORY}/.github/workflows/prepare-release.yml"
BUNDLE = "release-attestation.json"


def distribution_names(tag: str) -> tuple[str, str]:
    """Require a canonical stable tag before deriving paths or API arguments."""
    if re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag) is None:
        raise ValueError("release assets require a canonical stable vX.Y.Z tag")
    version = tag[1:]
    return f"research_repo_tools-{version}-py3-none-any.whl", f"research_repo_tools-{version}.tar.gz"


def gh(*args: str) -> str:
    """Fail closed on GitHub errors, with a bounded request duration."""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8", timeout=120).stdout


def api(endpoint: str) -> Any:
    return json.loads(gh("api", f"repos/{REPOSITORY}/{endpoint}"))


def require_release(release: dict[str, Any], tag: str, *, draft: bool) -> None:
    """Require the intended lifecycle state, rejecting prereleases and retargets."""
    if release.get("tag_name") != tag or release.get("draft") is not draft or release.get("prerelease") is not False:
        raise ValueError(f"expected {'draft' if draft else 'published'} stable release for {tag}")


def inventory(directory: Path, names: tuple[str, str]) -> None:
    """Accept only the two expected distributions, never links or extra files."""
    if {path.name for path in directory.iterdir()} != set(names):
        raise ValueError(f"expected exactly these distributions: {', '.join(names)}")
    if any((directory / name).is_symlink() or not (directory / name).is_file() or (directory / name).stat().st_size == 0 for name in names):
        raise ValueError("distributions must be nonempty regular files")


def stage(tag: str, dist: Path, bundle: Path) -> None:
    """Create or resume an empty draft; never replace existing release assets."""
    names = distribution_names(tag)
    inventory(dist, names)
    if not bundle.is_file() or bundle.is_symlink() or bundle.stat().st_size == 0:
        raise ValueError("missing signed release attestation")
    # Listing includes drafts for the authenticated repository writer. Unlike a
    # failed lookup, a successful empty listing is evidence that creation is safe.
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100"))
    matches = [release for page in pages for release in page if release["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("multiple releases name the target tag")
    if matches:
        release = matches[0]
        require_release(release, tag, draft=True)
        if release.get("assets"):
            raise ValueError("draft already has assets; inspect and remove the incomplete draft before retrying; assets are never overwritten")
    else:
        gh("release", "create", tag, "--repo", REPOSITORY, "--draft", "--verify-tag", "--title", tag, "--notes-from-tag")
    # Use a stable asset name so publication can require an exact inventory.
    attachment = dist.parent / BUNDLE
    with bundle.open("rb") as source, attachment.open("xb") as target:
        shutil.copyfileobj(source, target)
    gh("release", "upload", tag, "--repo", REPOSITORY, *(str(dist / name) for name in names), str(attachment))
    print(f"Draft {tag} contains the validated wheel, sdist, and signed provenance. Review before publishing.")


def verify(event: dict[str, Any], commit: str, output: Path) -> None:
    """Download a published release by asset IDs and verify its signed subjects."""
    if event.get("action") != "published" or event.get("repository", {}).get("full_name") != REPOSITORY:
        raise ValueError("expected a published release event from the owning repository")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("expected the release event's full commit SHA")
    released = event["release"]
    tag = released["tag_name"]
    names = distribution_names(tag)
    require_release(released, tag, draft=False)
    release_id = released["id"]
    if type(release_id) is not int or release_id <= 0:
        raise ValueError("invalid release ID")
    current = api(f"releases/{release_id}")
    require_release(current, tag, draft=False)
    assets = current["assets"]
    expected = {*names, BUNDLE}
    if len(assets) != len(expected) or {asset["name"] for asset in assets} != expected:
        raise ValueError("release must contain exactly the wheel, sdist, and signed provenance")
    output.mkdir(parents=True, exist_ok=False)
    # Keep the bundle out of the directory passed to the PyPI action.
    bundle = output.parent / BUNDLE
    if bundle.exists():
        raise ValueError("attestation destination already exists")
    for asset in assets:
        asset_id = asset["id"]
        if type(asset_id) is not int or asset_id <= 0:
            raise ValueError("invalid release asset ID")
        target = bundle if asset["name"] == BUNDLE else output / asset["name"]
        with target.open("xb") as stream:
            subprocess.run(
                ["gh", "api", f"repos/{REPOSITORY}/releases/assets/{asset_id}", "-H", "Accept: application/octet-stream"],
                stdout=stream,
                stderr=subprocess.PIPE,
                check=True,
                timeout=120,
            )
    inventory(output, names)
    for name in names:
        gh(
            "attestation",
            "verify",
            str(output / name),
            "--bundle",
            str(bundle),
            "--repo",
            REPOSITORY,
            "--signer-workflow",
            SIGNER,
            "--signer-digest",
            commit,
            "--source-digest",
            commit,
            "--source-ref",
            f"refs/tags/{tag}",
            "--deny-self-hosted-runners",
        )
    print(f"Verified both release assets for {tag} at {commit}; ready for the approval-gated upload.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    command = subcommands.add_parser("stage")
    command.add_argument("tag")
    command.add_argument("--dist", type=Path, default=Path("dist"))
    command.add_argument("--bundle", type=Path, required=True)
    command = subcommands.add_parser("verify")
    command.add_argument("--event", type=Path, default=Path(os.environ.get("GITHUB_EVENT_PATH", "event.json")))
    command.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    command.add_argument("--output", type=Path, default=Path("verified-dist"))
    args = parser.parse_args()
    try:
        if args.command == "stage":
            stage(args.tag, args.dist, args.bundle)
        else:
            verify(json.loads(args.event.read_text(encoding="utf-8")), args.commit, args.output)
    except subprocess.CalledProcessError as error:
        detail = error.stderr or error.stdout or str(error)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        print(f"Release assets failed: {detail.strip()}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Release assets failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
