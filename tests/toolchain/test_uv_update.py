"""uv ownership, pin reconciliation, and failure behavior without real upgrades."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from research_repo_tools import cli, uv_update


@pytest.fixture
def project(tmp_path, monkeypatch):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname="example"\nversion="0.1.0"\n[tool.uv] # pin\nrequired-version = "==0.12.15" # keep\n', newline="\n")
    monkeypatch.delenv("AXOUPDATER_CONFIG_WORKING_DIR", raising=False)
    monkeypatch.setenv("AXOUPDATER_CONFIG_PATH", str(tmp_path / "receipts"))
    return tmp_path


def standalone_receipt(project, installed):
    path = project / "receipts" / "uv-receipt.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps({"install_layout": "flat", "install_prefix": str(installed.parent), "binaries": [installed.name]}), encoding="utf-8", newline="\n"
    )
    return path


@pytest.mark.parametrize("owner", ["standalone", "homebrew"])
def test_updates_with_owner_and_reconciles_project_pin(project, monkeypatch, owner):
    installed = project / "tools" / ("uv.exe" if os.name == "nt" else "uv")
    installed.parent.mkdir()
    installed.touch()
    if owner == "standalone":
        standalone_receipt(project, installed)
    monkeypatch.setattr(uv_update.shutil, "which", lambda _: str(installed))
    calls = []
    current = "0.12.15"

    def run(command, args, **kwargs):
        nonlocal current
        calls.append((command, args))
        output = ""
        if args == ["--version"]:
            output = f"uv {current}" + (" (Homebrew)" if owner == "homebrew" else "")
        elif args == ["--prefix", "uv"]:
            output = str(installed.parent)
        else:
            assert args == (["upgrade", "uv"] if owner == "homebrew" else ["self", "update", "--no-config"])
            current = "0.12.16"
        return subprocess.CompletedProcess([command, *args], 0, output, "")

    monkeypatch.setattr(uv_update, "run_safe_command", run)
    monkeypatch.setattr(uv_update, "check_uv", lambda *, executable: current)
    uv_update.update(project)
    assert tomllib.loads((project / "pyproject.toml").read_text())["tool"]["uv"]["required-version"] == "==0.12.16"
    assert "# keep" in (project / "pyproject.toml").read_text()
    assert any(args == (["upgrade", "uv"] if owner == "homebrew" else ["self", "update", "--no-config"]) for _, args in calls)


def test_failed_upgrade_preserves_repository_files(project, monkeypatch):
    installed = project / ("uv.exe" if os.name == "nt" else "uv")
    installed.touch()
    standalone_receipt(project, installed)
    originals = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    monkeypatch.setattr(uv_update.shutil, "which", lambda _: str(installed))
    monkeypatch.setattr(uv_update, "check_uv", lambda **_: "0.12.15")

    def run(command, args, **kwargs):
        if args == ["--version"]:
            return subprocess.CompletedProcess([], 0, "uv 0.12.15", "")
        raise subprocess.CalledProcessError(1, [command, *args], stderr="external package manager")

    monkeypatch.setattr(uv_update, "run_safe_command", run)
    with pytest.raises(subprocess.CalledProcessError):
        uv_update.update(project)
    assert {path: path.read_bytes() for path in project.rglob("*") if path.is_file()} == originals


def test_invalid_manifest_fails_before_upgrade(project, monkeypatch):
    manifest = project / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace("==0.12.15", ">=0.12.15"), newline="\n")
    monkeypatch.setattr(uv_update.shutil, "which", lambda _: pytest.fail("must validate pin first"))
    with pytest.raises(ValueError, match="required-version"):
        uv_update.update(project)


def test_pin_edit_preserves_crlf_comments_and_unrelated_settings():
    original = "[tool.uv]\r\nrequired-version = '==0.12.15' # pin\r\n[tool.other]\r\nrequired-version = '==0.12.15'\r\n"
    assert uv_update.updated_manifest(original, "0.12.16") == original.replace("==0.12.15", "==0.12.16", 1)


@pytest.mark.parametrize("text", ["", '[tool.uv]\nrequired-version=">=0.12.15"', 'tool.uv.required-version="==0.12.15"'])
def test_noncanonical_pins_fail_without_changes(text):
    with pytest.raises(ValueError):
        uv_update.updated_manifest(text, "0.12.16")


@pytest.mark.parametrize("value", ['"malformed"', "42", "[]"])
def test_update_uv_rejects_non_table_configuration_without_effects(tmp_path, monkeypatch, capsys, value):
    manifest = tmp_path / "pyproject.toml"
    original = f"[tool]\nuv={value}\n".encode()
    manifest.write_bytes(original)
    monkeypatch.setattr(uv_update.shutil, "which", lambda _: pytest.fail("must reject before probing or upgrading uv"))
    assert cli.main(["--root", str(tmp_path), "deps", "update-uv"]) == 1
    assert "tool.uv" in capsys.readouterr().err
    assert manifest.read_bytes() == original


@pytest.mark.parametrize("receipt", ["missing", "malformed", "other-installation", "unknown-layout", "missing-binary", "relative-prefix", "non-object"])
def test_unverified_owner_never_invokes_self_update_or_changes_pin(project, monkeypatch, receipt):
    installed = project / "tools" / ("uv.exe" if os.name == "nt" else "uv")
    installed.parent.mkdir()
    installed.touch()
    if receipt != "missing":
        path = standalone_receipt(project, installed)
        data = json.loads(path.read_text())
        if receipt == "malformed":
            path.write_text("{", newline="\n")
        else:
            if receipt == "other-installation":
                data["install_prefix"] = str(project / "different uv")
            elif receipt == "unknown-layout":
                data["install_layout"] = "unknown"
            elif receipt == "missing-binary":
                data["binaries"] = ["uvx"]
            elif receipt == "relative-prefix":
                data["install_prefix"] = "tools"
            elif receipt == "non-object":
                data = []
            path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    monkeypatch.setattr(uv_update.shutil, "which", lambda _: str(installed))
    monkeypatch.setattr(uv_update, "check_uv", lambda **_: "0.12.15")

    def run(command, args, **kwargs):
        assert args == ["--version"], "an unverified owner must never reach the self-updater"
        return subprocess.CompletedProcess([], 0, "uv 0.12.15", "")

    monkeypatch.setattr(uv_update, "run_safe_command", run)
    original = (project / "pyproject.toml").read_bytes()
    with pytest.raises(ValueError, match="original package manager"):
        uv_update.update(project)
    assert (project / "pyproject.toml").read_bytes() == original


def test_working_directory_receipt_override_takes_precedence(project, monkeypatch):
    installed = project / ("uv.exe" if os.name == "nt" else "uv")
    installed.touch()
    standalone_receipt(project, installed)
    monkeypatch.chdir(project)
    monkeypatch.setenv("AXOUPDATER_CONFIG_WORKING_DIR", "1")
    assert not uv_update._standalone_installation(installed)
    (project / "uv-receipt.json").write_bytes((project / "receipts" / "uv-receipt.json").read_bytes())
    assert uv_update._standalone_installation(installed)


def test_xdg_receipt_does_not_fall_through_to_another_installation(project, monkeypatch):
    installed = project / ("uv.exe" if os.name == "nt" else "uv")
    installed.touch()
    monkeypatch.delenv("AXOUPDATER_CONFIG_PATH")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(project / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(project / "local"))
    monkeypatch.setattr(Path, "home", lambda: project)
    fallback = project / ("local" if os.name == "nt" else ".config") / "uv"
    fallback.mkdir(parents=True)
    fallback.joinpath("uv-receipt.json").write_bytes(standalone_receipt(project, installed).read_bytes())
    assert uv_update._standalone_installation(installed)
    primary = project / "xdg" / "uv"
    primary.mkdir(parents=True)
    primary.joinpath("uv-receipt.json").write_text("{}", newline="\n")
    assert not uv_update._standalone_installation(installed)
