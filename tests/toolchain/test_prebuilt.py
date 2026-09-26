"""Prebuilt-tool provenance, portable asset mapping and atomic publication."""

import hashlib
import io
import os
import tarfile
from unittest.mock import patch

import pytest

from research_repo_tools import config, toolchain_config
from research_repo_tools import prebuilt_tools as prebuilt
from research_repo_tools.toolchain_config import BinaryTool


@pytest.mark.parametrize("host", prebuilt.HOSTS)
@pytest.mark.parametrize("name,version", [("gitleaks", "8.30.1"), ("osv-scanner", "2.6.0")])
def test_assets(host, name, version):
    asset = prebuilt.asset_name(BinaryTool(name, version), host)
    assert ("windows" in asset) == ("windows" in host)
    assert ("arm64" in asset) == host.startswith("aarch64")
    assert asset.endswith(".exe" if name == "osv-scanner" else ".zip") if "windows" in host else not asset.endswith((".exe", ".zip"))


def test_unsupported_host():
    with pytest.raises(ValueError, match="unsupported"):
        prebuilt.asset_name(BinaryTool("gitleaks", "8.30.1"), "x86_64-unknown-linux-musl")


def test_exact_config(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(b'[project]\nrequires-python=">=3.14"\n[tool.uv]\nrequired-version="==0.12.19"\n')
    (tmp_path / ".python-version").write_bytes(b"3.14\n")
    for value in ("latest", "^8.30.1", "08.30.1", "8.30.1-rc.1"):
        with pytest.raises(ValueError, match="exact stable"):
            toolchain_config.load(config.parse({"toolchain": {"binaries": {"gitleaks": value}}}, root=tmp_path))
    result = toolchain_config.load(config.parse({"toolchain": {"binaries": {"gitleaks": "8.30.1"}}}, root=tmp_path))
    assert result.binaries == (BinaryTool("gitleaks", "8.30.1"),)


def release(tool, host, payload):
    name = prebuilt.asset_name(tool, host)
    return {
        "assets": [
            {
                "name": name,
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "browser_download_url": f"https://github.com/{prebuilt.REPOSITORIES[tool.name]}/releases/download/v{tool.version}/{name}",
            }
        ]
    }


@pytest.mark.parametrize("failure", ["checksum", "version", "missing-digest", "duplicate", "url"])
def test_failed_install_preserves_previous(tmp_path, failure):
    tool = BinaryTool("osv-scanner", "2.6.0")
    host = "x86_64-pc-windows-msvc"
    payload = b"model executable bytes"
    metadata = release(tool, host, payload)
    if failure == "missing-digest":
        metadata["assets"][0].pop("digest")
    elif failure == "duplicate":
        metadata["assets"] *= 2
    elif failure == "url":
        metadata["assets"][0]["browser_download_url"] = "https://example.org/other"
    target = tmp_path / "bin/osv-scanner.exe"
    target.parent.mkdir()
    target.write_bytes(b"old executable")

    def download(_url, destination, **kwargs):
        assert kwargs["expected_sha256"] == hashlib.sha256(payload).hexdigest()
        if failure == "checksum":
            raise ValueError("checksum mismatch")
        destination.write_bytes(payload)

    with (
        patch.object(prebuilt, "release_metadata", return_value=metadata),
        patch.object(prebuilt, "download_asset", side_effect=download),
        patch.object(prebuilt, "version_at", return_value="0.0.0") as probe,
        pytest.raises(ValueError),
    ):
        prebuilt.install(tool, host, target, cwd=tmp_path, env=dict(os.environ))
    assert target.read_bytes() == b"old executable"
    if failure != "version":
        probe.assert_not_called()


def test_archive_safety_precedes_execution(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("../escape")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    payload = stream.getvalue()
    with tarfile.open(fileobj=io.BytesIO(payload)) as stored:
        assert stored.getnames() == ["../escape"]
    tool = BinaryTool("gitleaks", "8.30.1")
    host = "aarch64-apple-darwin"
    with (
        patch.object(prebuilt, "release_metadata", return_value=release(tool, host, payload)),
        patch.object(prebuilt, "download_asset", side_effect=lambda _url, destination, **_: destination.write_bytes(payload)),
        patch.object(prebuilt, "version_at") as probe,
        pytest.raises(ValueError, match="unsafe"),
    ):
        prebuilt.install(tool, host, tmp_path / "bin/gitleaks", cwd=tmp_path, env={})
    probe.assert_not_called()
    assert not (tmp_path / "bin/gitleaks").exists()


def test_verified_binary_published(tmp_path):
    tool = BinaryTool("osv-scanner", "2.6.0")
    host = "aarch64-apple-darwin"
    payload = b"verified"
    target = tmp_path / "bin/osv-scanner"
    with (
        patch.object(prebuilt, "release_metadata", return_value=release(tool, host, payload)),
        patch.object(prebuilt, "download_asset", side_effect=lambda _url, destination, **_: destination.write_bytes(payload)),
        patch.object(prebuilt, "version_at", return_value=tool.version),
    ):
        prebuilt.install(tool, host, target, cwd=tmp_path, env={})
    assert target.read_bytes() == payload
