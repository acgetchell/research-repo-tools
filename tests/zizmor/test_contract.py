"""Credential-free audit policy, failure handling, and scanner selection."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from research_repo_tools import config, zizmor
from research_repo_tools.cli import main
from research_repo_tools.process import ExecutableNotFoundError


@pytest.fixture
def consumer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[dependency-groups]\ndev=["zizmor==1.30.1"]\n[tool.research-repo-tools.zizmor]\npersona="pedantic"\ntimeout=12\n',
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / ".github").mkdir()
    for name in (*zizmor._CREDENTIALS, *zizmor._MODE_VARIABLES, "GH_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(zizmor, "resolve_executable", lambda *args, **kwargs: tmp_path / "zizmor")
    return tmp_path


def invoke(root: Path, *args: str) -> int:
    return main(["--root", str(root), "zizmor", "check", *args])


def scanner_stub(monkeypatch: pytest.MonkeyPatch, *, token: bytes = b"discovered-secret\r\n", auth_status: int = 0, status: int = 0):
    calls = []

    def run(command, args=(), **kwargs):
        calls.append((command, args, kwargs))
        if command == "gh":
            return subprocess.CompletedProcess([], auth_status, token, b"private auth details")
        if args == ["--version"]:
            assert not any(name in kwargs["env"] for name in zizmor._CREDENTIALS)
            return subprocess.CompletedProcess([], 0, b"zizmor 1.30.1\n", b"")
        return subprocess.CompletedProcess([], status, b"report\r\n", b"scanner diagnostic\n")

    monkeypatch.setattr(zizmor, "run_command_bytes", run)
    return calls


@pytest.mark.parametrize("source", ["ZIZMOR_GITHUB_TOKEN", "GH_TOKEN", "gh auth token"])
def test_token_precedence_and_explicit_online_policy(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys, source: str) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ignored-secret")
    monkeypatch.setenv("ZIZMOR_OFFLINE", "true")
    monkeypatch.setenv("ZIZMOR_NO_ONLINE_AUDITS", "true")
    if source == "ZIZMOR_GITHUB_TOKEN":
        monkeypatch.setenv("ZIZMOR_GITHUB_TOKEN", "preferred-secret")
    if source != "gh auth token":
        monkeypatch.setenv("GH_TOKEN", "second-secret")
    before = dict(os.environ)
    calls = scanner_stub(monkeypatch)
    assert invoke(consumer, "--require-online") == 0
    scanner, args, options = calls[-1]
    assert args[:5] == ["--strict-collection", "--persona", "pedantic", "--format", "plain"]
    assert args[-2:] == ["--", str(consumer / ".github")]
    assert "--offline" not in args
    assert options["timeout"] == 12
    env = options["env"]
    assert env["ZIZMOR_GITHUB_TOKEN"] == {"ZIZMOR_GITHUB_TOKEN": "preferred-secret", "GH_TOKEN": "second-secret", "gh auth token": "discovered-secret"}[source]
    assert not any(key in env for key in ("GH_TOKEN", "GITHUB_TOKEN", *zizmor._MODE_VARIABLES))
    assert (calls[0][0] == "gh") == (source == "gh auth token")
    assert dict(os.environ) == before
    output = capsys.readouterr()
    assert f"online audits enabled using {source}" in output.err
    assert "secret" not in output.out + output.err


@pytest.mark.parametrize("auth_status,token", [(1, b"private-token"), (0, b""), (0, b"bad\ntoken"), (0, b"\xff")])
@pytest.mark.parametrize("required", [False, True])
def test_failed_authentication_is_reported_without_diagnostics(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys, auth_status, token, required) -> None:
    calls = scanner_stub(monkeypatch, auth_status=auth_status, token=token)
    assert invoke(consumer, *(["--require-online"] if required else [])) == int(required)
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
    if required:
        assert len(calls) == 1
        assert "online audits required" in output.err
        assert not output.out
    else:
        assert "--offline" in calls[-1][1]
        assert "online audits skipped" in output.err


@pytest.mark.parametrize("failure", [ExecutableNotFoundError("private-token"), OSError("private-token"), subprocess.TimeoutExpired("gh", 10, b"private-token")])
def test_missing_or_broken_gh_never_leaks(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys, failure) -> None:
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(zizmor, "run_command_bytes", fail)
    assert invoke(consumer, "--require-online") == 1
    output = capsys.readouterr()
    assert "authentication is unavailable" in output.err
    assert "private-token" not in output.out + output.err


def test_offline_skips_authentication_and_removes_credentials(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("ZIZMOR_GITHUB_TOKEN", "unused-secret")
    calls = scanner_stub(monkeypatch)
    assert invoke(consumer, "--offline", "--format", "sarif") == 0
    assert len(calls) == 2
    assert "--offline" in calls[-1][1] and "sarif" in calls[-1][1]
    assert not any(key in calls[-1][2]["env"] for key in zizmor._CREDENTIALS)
    assert "explicit --offline" in capsys.readouterr().err


@pytest.mark.parametrize("status,expected", [(1, 1), (14, 14), (-9, 137)])
def test_scanner_exit_status_is_preserved(consumer: Path, monkeypatch: pytest.MonkeyPatch, status, expected) -> None:
    calls = scanner_stub(monkeypatch, status=status)
    assert invoke(consumer) == expected
    assert len(calls) == 3  # No retry or offline downgrade.


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("zizmor", 12, b"selected-secret", b"other-secret"), OSError("selected-secret")])
def test_scanner_exceptions_suppress_captured_secrets(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys, failure) -> None:
    monkeypatch.setenv("GH_TOKEN", "selected-secret")
    original = scanner_stub(monkeypatch)
    runner = zizmor.run_command_bytes

    def fail(command, args, **kwargs):
        if args != ["--version"]:
            raise failure
        return runner(command, args, **kwargs)

    monkeypatch.setattr(zizmor, "run_command_bytes", fail)
    assert invoke(consumer) == 1
    assert len(original) == 1
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err
    assert "suppressed" in output.err


def test_both_output_streams_redact_selected_and_unused_tokens(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    for name, value in (("GH_TOKEN", "short-secret"), ("ZIZMOR_GITHUB_TOKEN", "long-short-secret"), ("GITHUB_TOKEN", "unused-secret")):
        monkeypatch.setenv(name, value)
    scanner_stub(monkeypatch)
    runner = zizmor.run_command_bytes

    def echo(command, args, **kwargs):
        if args == ["--version"]:
            return runner(command, args, **kwargs)
        return subprocess.CompletedProcess([], 9, b'{"message":"long-short-secret unused-secret"}', b"short-secret")

    monkeypatch.setattr(zizmor, "run_command_bytes", echo)
    assert invoke(consumer, "--format", "sarif") == 9
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err
    assert json.loads(output.out) == {"message": "[REDACTED] [REDACTED]"}


def test_json_escaped_credentials_are_redacted(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    token = 'quote"and\\slash'
    monkeypatch.setenv("GH_TOKEN", token)
    scanner_stub(monkeypatch)
    runner = zizmor.run_command_bytes

    def echo(command, args, **kwargs):
        if args == ["--version"]:
            return runner(command, args, **kwargs)
        return subprocess.CompletedProcess([], 0, json.dumps({"message": token}).encode(), token.encode())

    monkeypatch.setattr(zizmor, "run_command_bytes", echo)
    assert invoke(consumer, "--format", "sarif") == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"message": "[REDACTED]"}
    assert token not in output.err


@pytest.mark.parametrize("token", ["bad token", "bad\ntoken", "bad\0token", "nonascii-\u00e9"])
def test_invalid_explicit_token_does_not_downgrade(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys, token: str) -> None:
    # os.environ itself cannot hold NUL. Model that one environment value at
    # the authentication boundary; all other cases exercise the public CLI.
    if "\0" in token:
        with pytest.raises(ValueError, match="ASCII token"):
            zizmor._authentication({"GH_TOKEN": token}, consumer)
        return
    monkeypatch.setenv("GH_TOKEN", token)
    calls = scanner_stub(monkeypatch)
    assert invoke(consumer) == 1
    assert not calls
    output = capsys.readouterr()
    assert token not in output.err
    assert "ASCII token" in output.err


def test_empty_preferred_token_uses_next_source(consumer: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZIZMOR_GITHUB_TOKEN", "")
    monkeypatch.setenv("GH_TOKEN", "next-source")
    calls = scanner_stub(monkeypatch)
    assert invoke(consumer) == 0
    assert len(calls) == 2
    assert calls[-1][2]["env"]["ZIZMOR_GITHUB_TOKEN"] == "next-source"


def test_host_matches_between_discovery_and_scanner(consumer: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_HOST", "github.example.test")
    calls = scanner_stub(monkeypatch)
    assert invoke(consumer) == 0
    assert calls[0][1] == ["auth", "token", "--hostname", "github.example.test"]
    args = calls[-1][1]
    assert args[args.index("--gh-hostname") + 1] == "github.example.test"


@pytest.mark.parametrize("stdout", [b"unexpected selected-secret", b"\xff"])
def test_invalid_version_output_is_suppressed(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys, stdout: bytes) -> None:
    monkeypatch.setenv("GH_TOKEN", "selected-secret")
    monkeypatch.setattr(zizmor, "run_command_bytes", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout, b"selected-secret"))
    assert invoke(consumer) == 1
    output = capsys.readouterr()
    assert "could not verify" in output.err
    assert "selected-secret" not in output.out + output.err


@pytest.mark.parametrize("pin", ['"zizmor>=1"', '"zizmor==1.*"', "\"zizmor==1.30.1; sys_platform != 'win32'\"", '"zizmor==1.30.1", "zizmor==1.30.1"', ""])
def test_ambiguous_or_floating_pins_fail_before_authentication(consumer: Path, monkeypatch: pytest.MonkeyPatch, pin: str) -> None:
    (consumer / "pyproject.toml").write_text(
        f'[dependency-groups]\ndev=[{pin}]\n[tool.research-repo-tools.zizmor]\npersona="regular"\n', encoding="utf-8", newline="\n"
    )
    calls = scanner_stub(monkeypatch)
    assert invoke(consumer) == 1
    assert not calls


def test_included_python_pin_and_version_mismatch(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    (consumer / "pyproject.toml").write_text(
        '[dependency-groups]\ndev=[{include-group="security"}]\nsecurity=["zizmor==1.29.0"]\n[tool.research-repo-tools.zizmor]\npersona="regular"\n',
        encoding="utf-8",
        newline="\n",
    )
    calls = scanner_stub(monkeypatch)
    assert invoke(consumer, "--offline") == 1
    assert len(calls) == 1
    assert "does not match declared 1.29.0" in capsys.readouterr().err


def test_cargo_pin_uses_the_existing_managed_location(consumer: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from research_repo_tools.toolchain import Runtime
    from research_repo_tools.toolchain_config import executable, load

    (consumer / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version="==0.12.18"\n[tool.research-repo-tools.toolchain.cargo]\nzizmor="1.30.1"\n'
        '[tool.research-repo-tools.zizmor]\npersona="regular"\n',
        encoding="utf-8",
        newline="\n",
    )
    (consumer / ".python-version").write_bytes(b"3.14\n")
    (consumer / "rust-toolchain.toml").write_bytes(b'[toolchain]\nchannel="1.94.0"\n')
    monkeypatch.setenv("RESEARCH_REPO_TOOLS_HOME", str(consumer / "managed"))
    calls = scanner_stub(monkeypatch)
    settings = config.load(root=consumer)
    runtime = Runtime(load(settings))
    expected = executable(runtime.cargo_root(runtime.plan.cargo[0]) / "bin", "zizmor")
    assert invoke(consumer, "--offline") == 0
    assert all(call[0] == expected for call in calls)
    assert not expected.exists()  # Checking never installs tools.


@pytest.mark.parametrize("settings", [{"persona": "unknown"}, {"persona": True}, {"timeout": 0}, {"timeout": True}, {"token": "never-configure-secrets"}])
def test_invalid_configuration_is_rejected(settings, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        config.parse({"zizmor": settings}, root=tmp_path)


def test_persona_must_be_explicit(consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    (consumer / "pyproject.toml").write_bytes(b'[dependency-groups]\ndev=["zizmor==1.30.1"]\n')
    calls = scanner_stub(monkeypatch)
    assert invoke(consumer) == 1
    assert not calls
    assert "explicit zizmor.persona" in capsys.readouterr().err
