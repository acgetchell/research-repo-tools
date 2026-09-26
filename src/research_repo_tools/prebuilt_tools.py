"""Checksum-verified upstream binaries in versioned, host-specific installations."""

import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path

from research_repo_tools.archives import ArchiveLimits, download_asset, extract_archive
from research_repo_tools.process import run_safe_command
from research_repo_tools.toolchain_config import BinaryTool

REPOSITORIES = {"gitleaks": "gitleaks/gitleaks", "osv-scanner": "google/osv-scanner"}
HOSTS = {
    "aarch64-apple-darwin": ("darwin", "arm64"),
    "aarch64-pc-windows-msvc": ("windows", "arm64"),
    "aarch64-unknown-linux-gnu": ("linux", "arm64"),
    "x86_64-apple-darwin": ("darwin", "amd64"),
    "x86_64-pc-windows-msvc": ("windows", "amd64"),
    "x86_64-unknown-linux-gnu": ("linux", "amd64"),
}


def asset_name(tool: BinaryTool, host: str) -> str:
    if host not in HOSTS or tool.name not in REPOSITORIES:
        raise ValueError(f"unsupported prebuilt tool/host: {tool.name}, {host}")
    system, arch = HOSTS[host]
    if tool.name == "osv-scanner":
        return f"osv-scanner_{system}_{arch}" + (".exe" if system == "windows" else "")
    arch = "x64" if arch == "amd64" else arch
    extension = "zip" if system == "windows" else "tar.gz"
    return f"gitleaks_{tool.version}_{system}_{arch}.{extension}"


def release_metadata(name: str, tag: str) -> dict:
    if name not in REPOSITORIES or not re.fullmatch(r"latest|v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("unsupported binary release")
    suffix = "latest" if tag == "latest" else f"tags/{tag}"
    request = urllib.request.Request(f"https://api.github.com/repos/{REPOSITORIES[name]}/releases/{suffix}", headers={"User-Agent": "research-repo-tools"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("release metadata exceeds size limit")
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("draft") is not False or value.get("prerelease") is not False:
        raise ValueError("expected a published stable binary release")
    actual = value.get("tag_name")
    if (
        not isinstance(actual, str)
        or not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", actual)
        or tag != "latest"
        and actual != tag
    ):
        raise ValueError("binary release tag mismatch")
    return value


def version_at(path: Path, tool: BinaryTool, *, cwd: Path, env: dict[str, str]) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError("missing managed executable")
    args = ["version"] if tool.name == "gitleaks" else ["--version"]
    output = run_safe_command(str(path), args, cwd=cwd, env=env, timeout=30).stdout.strip()
    pattern = r"(?:gitleaks )?v?([0-9]+\.[0-9]+\.[0-9]+)" if tool.name == "gitleaks" else r"osv-scanner version: ([0-9]+\.[0-9]+\.[0-9]+)(?:\n.*)*"
    match = re.fullmatch(pattern, output)
    if not match:
        raise ValueError(f"unrecognized {tool.name} version output")
    return match[1]


def install(tool: BinaryTool, host: str, destination: Path, *, cwd: Path, env: dict[str, str]) -> None:
    """Download by upstream release SHA-256, verify version, then replace one file.

    GitHub's release-asset SHA-256 is the checksum authority. Missing checksums
    fail closed. Archive members pass the shared portable archive validator.
    """
    name = asset_name(tool, host)
    metadata = release_metadata(tool.name, f"v{tool.version}")
    assets = metadata.get("assets")
    if not isinstance(assets, list):
        raise ValueError("binary release has no asset inventory")
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"release must contain exactly one {name}")
    asset = matches[0]
    digest = asset.get("digest")
    url = f"https://github.com/{REPOSITORIES[tool.name]}/releases/download/v{tool.version}/{name}"
    if asset.get("browser_download_url") != url or not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("release asset URL or SHA-256 checksum is missing/malformed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=destination.parent) as directory:
        stage = Path(directory)
        downloaded = stage / name
        limits = ArchiveLimits(archive_bytes=256 * 1024 * 1024, members=100, content_bytes=512 * 1024 * 1024)
        download_asset(url, downloaded, expected_sha256=digest.removeprefix("sha256:"), limits=limits)
        candidate = downloaded
        if tool.name == "gitleaks":
            extract_archive(downloaded, stage / "unpacked", limits=limits)
            candidate = stage / "unpacked" / ("gitleaks.exe" if HOSTS[host][0] == "windows" else "gitleaks")
        candidate.chmod(0o700)
        if version_at(candidate, tool, cwd=cwd, env=env) != tool.version:
            raise ValueError(f"{tool.name} downloaded executable version mismatch; installation unchanged")
        os.replace(candidate, destination)
