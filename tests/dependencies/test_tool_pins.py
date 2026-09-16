"""Canonical Cargo SemVer and stable uv pins, independent of consumer identity."""

import os
import subprocess
from pathlib import Path

import pytest

from research_repo_tools import cli, tool_pins
from research_repo_tools.process import ExecutableNotFoundError


@pytest.fixture
def policy():
    return {"tools": {"rumdl_version": "rumdl", "uv_version": "uv", "just_version": "just"}}


def just_text(mapping):
    return "".join((f'{pin} := "1.2.3"  # retained\r\n' for pin in mapping))


def installed(mapping):
    return dict.fromkeys(mapping.values(), "1.2.3")


def test_atomic_reconciliation_preserves_unmanaged_text_and_crlf(tmp_path, policy):
    path = tmp_path / "justfile"
    original = ("# café\r\n" + just_text(policy["tools"]) + "\r\nbuild:\r\n    cargo build\r\n").encode()
    path.write_bytes(original)
    versions = installed(policy["tools"]) | {"rumdl": "2.0.0", "uv": "3.0.0"}
    changes = tool_pins.reconcile(path, versions, policy["tools"])
    assert changes == {"rumdl_version": ("1.2.3", "2.0.0"), "uv_version": ("1.2.3", "3.0.0")}
    expected = original.replace(b'rumdl_version := "1.2.3"', b'rumdl_version := "2.0.0"').replace(b'uv_version := "1.2.3"', b'uv_version := "3.0.0"')
    assert path.read_bytes() == expected
    assert sorted((p.name for p in tmp_path.iterdir())) == ["justfile"]
    assert tool_pins.reconcile(path, versions, policy["tools"]) == {}


@pytest.mark.parametrize("problem", ["missing-tool", "duplicate-pin", "missing-pin", "malformed-uv"])
def test_any_invalid_pin_leaves_entire_file_unchanged(tmp_path, policy, problem):
    text = just_text(policy["tools"])
    versions = installed(policy["tools"]) | {"rumdl": "2.0.0"}
    if problem == "missing-tool":
        del versions["uv"]
    elif problem == "duplicate-pin":
        text += 'uv_version := "1.2.3"\r\n'
    elif problem == "missing-pin":
        text = text.replace('uv_version := "1.2.3"  # retained\r\n', "")
    else:
        versions["uv"] = "3.0.0-rc.1"
    path = tmp_path / "justfile"
    path.write_bytes(text.encode())
    with pytest.raises(ValueError):
        tool_pins.reconcile(path, versions, policy["tools"])
    assert path.read_bytes() == text.encode()
    assert sorted((p.name for p in tmp_path.iterdir())) == ["justfile"]


@pytest.mark.parametrize("version", ["2.0.0-rc.1", "2.0.0+build.5", "2.0.0-rc.1+build.5"])
def test_every_consumer_accepts_canonical_cargo_prereleases_and_build_metadata(tmp_path, policy, version):
    path = tmp_path / "justfile"
    original = just_text(policy["tools"]).encode()
    path.write_bytes(original)
    versions = installed(policy["tools"]) | {"rumdl": version}
    assert tool_pins.reconcile(path, versions, policy["tools"]) == {"rumdl_version": ("1.2.3", version)}
    assert path.read_bytes() == original.replace(b'rumdl_version := "1.2.3"', f'rumdl_version := "{version}"'.encode())


@pytest.mark.parametrize("version", ["01.2.3", "1.02.3", "1.2.03", "1.2.3-01", "1.2.3-alpha..beta", "1.2.3+", "1.2.3+build..1"])
def test_noncanonical_cargo_versions_rejected(version):
    with pytest.raises(ValueError, match="invalid installed version for rumdl"):
        tool_pins.parse_installed_packages(f"rumdl v{version}:\n    rumdl\n")


def test_unmanaged_prerelease_and_source_location_are_preserved():
    assert tool_pins.parse_installed_packages("rumdl v1.2.3:\r\n    rumdl\r\nunmanaged-tool v2.0.0-rc.1+build.01 (/some/source):\n    unmanaged\n") == {
        "rumdl": "1.2.3",
        "unmanaged-tool": "2.0.0-rc.1+build.01",
    }


@pytest.mark.parametrize("output", ["rumdl v1.2.3:\nrumdl v2.0.0:", "rumdl unknown:", "rumdl v:", "unexpected stdout"])
def test_duplicate_or_malformed_headers_rejected(output):
    with pytest.raises(ValueError):
        tool_pins.parse_installed_packages(output)


@pytest.mark.parametrize(
    "output",
    [
        "uv 1.2.3 using runtime 3.14.0",
        "uv unknown",
        "uv 1.2.3-rc.1",
        "uv 1.2.3+meta",
        "uv 1.2.3.4",
        "uv release-1.2.3",
        "uv 1.2.3 (embedded 1.2.3)",
        "uv 01.2.3",
    ],
)
def test_invalid_uv_output_rejected_before_cargo(tmp_path, monkeypatch, output):
    calls = []

    def fake(command, args, **kwargs):
        calls.append((command, args, kwargs["timeout"]))
        return subprocess.CompletedProcess([], 0, output, "")

    monkeypatch.setattr(tool_pins, "run_safe_command", fake)
    with pytest.raises(ValueError):
        tool_pins.update(tmp_path / "nonexistent", {"rumdl_version": "rumdl", "uv_version": "uv"}, uv="selected-uv")
    assert calls == [("selected-uv", ["--version"], 30)]
    assert list(tmp_path.iterdir()) == []


def test_selected_uv_does_not_get_overwritten_by_cargo_package(tmp_path, monkeypatch):
    path = tmp_path / "justfile"
    original = b'uv_version := "1.2.3"\nrumdl_version := "1.2.3"\n'
    path.write_bytes(original)
    calls = []

    def fake(command, args, **kwargs):
        calls.append((command, args))
        return subprocess.CompletedProcess([], 0, "uv 3.0.0" if command == "chosen-uv" else "uv v2.0.0:\n    uv\nrumdl v4.0.0:\n    rumdl\n", "")

    monkeypatch.setattr(tool_pins, "run_safe_command", fake)
    changes = tool_pins.update(path, {"uv_version": "uv", "rumdl_version": "rumdl"}, uv="chosen-uv", dry_run=True)
    assert changes == {"uv_version": ("1.2.3", "3.0.0"), "rumdl_version": ("1.2.3", "4.0.0")}
    assert calls == [("chosen-uv", ["--version"]), ("cargo", ["install", "--list"])]
    assert path.read_bytes() == original


def test_symlink_target_and_permissions_preserved(tmp_path):
    target = tmp_path / "pins.just"
    target.write_bytes(b'uv_version := "1.2.3"\r\n')
    target.chmod(416)
    link = tmp_path / "justfile"
    try:
        link.symlink_to(target.name)
    except OSError:
        if os.name != "nt":
            raise
        pytest.skip("symlink creation unavailable on this native Windows runner")
    mode = target.stat().st_mode
    tool_pins.reconcile(link, {"uv": "3.0.0"}, {"uv_version": "uv"})
    assert link.is_symlink()
    assert target.read_bytes() == b'uv_version := "3.0.0"\r\n'
    assert target.stat().st_mode == mode
    assert sorted((p.name for p in tmp_path.iterdir())) == ["justfile", "pins.just"]


def test_captured_uv_preflight_never_resolves_an_executable(tmp_path, monkeypatch, capsys):

    def unexpected(*args, **kwargs):
        pytest.fail("captured uv output must not invoke an executable")

    monkeypatch.setattr(tool_pins, "run_safe_command", unexpected)
    assert cli.main(["--root", str(tmp_path), "deps", "check-uv", "--output", "uv 9.8.7"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "uv 9.8.7 satisfies the stable X.Y.Z contract\n"
    assert captured.err == ""


def test_cli_adopts_cargo_prerelease_without_a_policy_setting(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text('[tool.research-repo-tools.deps.tools]\nrumdl_version = "rumdl"\nuv_version = "uv"\n')
    path = tmp_path / "justfile"
    path.write_bytes(b'rumdl_version := "1.0.0"\r\nuv_version := "1.0.0"\r\n')

    def run(command, args, **kwargs):
        return subprocess.CompletedProcess([], 0, "uv 3.0.0" if command == "uv" else "rumdl v2.0.0-rc.1+build.5:\n    rumdl\n", "")

    monkeypatch.setattr(tool_pins, "run_safe_command", run)
    assert cli.main(["--root", str(tmp_path), "deps", "update-tools"]) == 0
    assert path.read_bytes() == b'rumdl_version := "2.0.0-rc.1+build.5"\r\nuv_version := "3.0.0"\r\n'
    captured = capsys.readouterr()
    assert "rumdl_version: 1.0.0 -> 2.0.0-rc.1+build.5" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("action", ["check-uv", "update-tools"])
@pytest.mark.parametrize("executable", ["uv", "./tools/uv"])
@pytest.mark.parametrize("explicit_root", [False, True])
def test_configured_uv_uses_consumer_root_from_any_working_directory(tmp_path, monkeypatch, capsys, action, executable, explicit_root):
    root = tmp_path / "consumer"
    root.mkdir()
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    settings = (unrelated if explicit_root else root) / "config.toml"
    settings.write_text(f'[deps]\nuv="{executable}"\n[deps.tools]\nuv_version="uv"\n')
    pins = root / "justfile"
    pins.write_bytes(b'uv_version := "1.0.0"\r\n')
    selected = []

    def run(command, args, **kwargs):
        selected.append(command)
        version = "2.0.0" if command == "uv" or Path(command) == root / "tools/uv" else "9.9.9"
        return subprocess.CompletedProcess([], 0, f"uv {version}", "")

    monkeypatch.setattr(tool_pins, "run_safe_command", run)
    arguments = ["--config", str(settings)] + (["--root", str(root)] if explicit_root else []) + ["deps", action]
    if action == "update-tools":
        arguments.append("--dry-run")
    for cwd in (root, unrelated):
        monkeypatch.chdir(cwd)
        assert cli.main(arguments) == 0
        assert "2.0.0" in capsys.readouterr().out
        assert pins.read_bytes() == b'uv_version := "1.0.0"\r\n'
    assert selected == [executable if executable == "uv" else str(root / "tools/uv")] * 2


@pytest.mark.parametrize(
    "error,detail",
    [
        (ExecutableNotFoundError("Required executable 'uv' not found in PATH"), "Required executable 'uv' not found in PATH"),
        (subprocess.CalledProcessError(23, ["uv"], stderr="\x1b[31mregistry unavailable\x1b[0m\nretry later"), "exit status 23"),
        (subprocess.TimeoutExpired(["uv"], 30, stderr=b"request\ntimed out"), "timed out after 30 seconds"),
        (ValueError("x" * 800), "…"),
    ],
)
def test_cli_diagnostics_preserve_failures_without_controls_or_tracebacks(tmp_path, monkeypatch, capsys, error, detail):

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(tool_pins, "run_safe_command", fail)
    assert cli.main(["--root", str(tmp_path), "deps", "check-uv"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert detail in captured.err
    assert captured.err.count("\n") == 1
    assert "\x1b" not in captured.err and "Traceback" not in captured.err
    assert len(captured.err) <= 425
