"""uv ownership, pin reconciliation, and failure behavior without real upgrades."""

import subprocess
import tomllib

import pytest

from research_repo_tools import cli, uv_update


@pytest.fixture
def project(tmp_path):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname="example"\nversion="0.1.0"\n[tool.uv] # pin\nrequired-version = "==0.12.15" # keep\n')
    return tmp_path


@pytest.mark.parametrize("owner", ["standalone", "homebrew"])
def test_updates_with_owner_and_reconciles_project_pin(project, monkeypatch, owner):
    installed = project / "tools" / "uv"
    installed.parent.mkdir()
    installed.touch()
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
    originals = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    monkeypatch.setattr(uv_update.shutil, "which", lambda _: "/tools/uv")
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
    manifest.write_text(manifest.read_text().replace("==0.12.15", ">=0.12.15"))
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
