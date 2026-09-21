"""Shared subprocess utils behavior and regression cases."""

import io
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest

from research_repo_tools import process as subprocess_utils
from research_repo_tools.process import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ExecutableNotFoundError,
    _build_run_kwargs,
    check_git_history,
    check_git_repo,
    cpu_description,
    find_project_root,
    format_exception_diagnostics,
    get_git_commit_hash,
    get_git_remote_url,
    get_safe_executable,
    run_cargo_command,
    run_git_command,
    run_git_command_with_input,
    run_safe_command,
)


@pytest.fixture(autouse=True)
def invocation_directory(tmp_path, monkeypatch):
    """Keep generic subprocess tests independent of Git and the checkout."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def invocation_repository(tmp_path, monkeypatch, git_mutations_allowed):
    """Exercise Git helpers in a minimal disposable repository."""
    root = tmp_path / "repository"
    root.mkdir()
    for args in (
        ["init", "--quiet"],
        [
            "-c",
            "user.name=Test Author",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            "Initial fixture",
        ],
        ["remote", "add", "origin", "https://example.invalid/consumer.git"],
    ):
        subprocess.run(["git", "--no-pager", *args], cwd=root, check=True, capture_output=True, timeout=30)
    monkeypatch.chdir(root)
    return root


def test_repository_identity_and_history_are_read_from_the_consumer(invocation_repository):
    assert check_git_repo() is True
    assert check_git_history() is True
    expected = run_git_command(["rev-parse", "HEAD"], cwd=invocation_repository).stdout.strip()
    assert get_git_commit_hash() == expected
    assert get_git_remote_url() == "https://example.invalid/consumer.git"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
if TYPE_CHECKING:
    pass
if TYPE_CHECKING:
    from pathlib import Path


class TestGetSafeExecutable:
    @pytest.mark.parametrize("command", ["echo", "git", "ls"])
    def test_finds_existing_executables(self, command) -> None:
        """Test that it finds common executables."""
        if sys.platform.startswith("win") and command in {"ls", "echo"}:
            pytest.skip(f"{command} may not be an external executable on Windows")
        result = get_safe_executable(command)
        assert isinstance(result, str)
        assert len(result) > 0
        assert Path(result).name.startswith(command)
        assert Path(result).is_absolute()

    @pytest.mark.parametrize("fake_command", ["definitely-nonexistent-command-xyz", "fake-command-for-testing", "nonexistent123"])
    def test_raises_on_nonexistent_executables(self, fake_command) -> None:
        """Test that it raises ExecutableNotFoundError for nonexistent commands."""
        with pytest.raises(ExecutableNotFoundError, match="not found in PATH") as exc_info:
            get_safe_executable(fake_command)
        assert fake_command in str(exc_info.value)

    def test_finds_git(self) -> None:
        path = get_safe_executable("git")
        assert "git" in path

    def test_raises_for_nonexistent_command(self) -> None:
        with pytest.raises(ExecutableNotFoundError, match="not found in PATH"):
            get_safe_executable("definitely_not_a_real_command_12345")

    def test_returns_full_executable_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        selected = str(tmp_path / "bin" / "git")
        monkeypatch.setattr(subprocess_utils.shutil, "which", lambda _command: selected)
        assert get_safe_executable("git") == selected

    def test_raises_when_executable_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess_utils.shutil, "which", lambda _command: None)
        with pytest.raises(ExecutableNotFoundError, match="Required executable 'git' not found in PATH"):
            get_safe_executable("git")


class TestRunGitCommand:
    def test_git_version(self) -> None:
        """Test basic git command execution."""
        result = run_git_command(["--version"])
        assert result.returncode == 0
        assert "git version" in result.stdout.lower()
        assert isinstance(result.stdout, str)

    def test_git_command_with_custom_params(self) -> None:
        """Test git command with custom parameters."""
        result = run_git_command(["status", "--porcelain"], check=False)
        assert isinstance(result.returncode, int)
        assert isinstance(result.stdout, str)

    def test_git_command_failure_handling(self) -> None:
        """Test that failed git commands raise CalledProcessError when check=True."""
        with pytest.raises(subprocess.CalledProcessError):
            run_git_command(["invalid-git-subcommand-xyz"], check=True)

    def test_git_command_no_failure_with_check_false(self) -> None:
        """Test that failed git commands don't raise when check=False."""
        result = run_git_command(["invalid-git-subcommand-xyz"], check=False)
        assert result.returncode != 0
        assert isinstance(result.stdout, str)

    def test_runs_simple_git_command(self, invocation_repository) -> None:
        result = run_git_command(["rev-parse", "--git-dir"])
        assert result.returncode == 0
        assert result.stdout.strip()

    def test_raises_on_bad_command(self) -> None:
        with pytest.raises(subprocess_utils.subprocess.CalledProcessError):
            run_git_command(["not-a-real-git-subcommand"])

    def test_runs_git_with_full_path_and_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        calls: dict[str, Any] = {}
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda command: f"/usr/bin/{command}")

        def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls["args"] = args
            calls["kwargs"] = kwargs
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

        monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)
        result = run_git_command(["status", "--short"], cwd=tmp_path)
        assert result.stdout == "ok\n"
        assert calls["args"] == ["/usr/bin/git", "status", "--short"]
        assert calls["kwargs"]["cwd"] == tmp_path
        assert calls["kwargs"]["capture_output"] is True
        assert calls["kwargs"]["text"] is True
        assert calls["kwargs"]["check"] is True
        assert calls["kwargs"]["encoding"] == "utf-8"

    def test_passes_safe_overrides_to_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict[str, Any] = {}
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda _command: "/usr/bin/git")

        def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls["args"] = args
            calls["kwargs"] = kwargs
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="bad")

        monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)
        result = run_git_command(["bad-subcommand"], check=False, timeout=10)
        assert result.returncode == 1
        assert calls["kwargs"]["check"] is False
        assert calls["kwargs"]["timeout"] == 10

    def test_rejects_insecure_kwargs_before_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = False
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda _command: "/usr/bin/git")

        def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal called
            called = True
            return subprocess.CompletedProcess([], 0)

        monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)
        kwargs: dict[str, Any] = {"shell": True}
        with pytest.raises(ValueError, match="shell=True is not allowed"):
            run_git_command(["status"], **kwargs)
        assert called is False


class TestRunCargoCommand:
    @pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed in PATH")
    def test_cargo_version(self) -> None:
        """Test basic cargo command execution."""
        result = run_cargo_command(["--version"])
        assert result.returncode == 0
        assert "cargo" in result.stdout.lower()
        assert isinstance(result.stdout, str)

    @pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed in PATH")
    def test_cargo_command_with_custom_params(self) -> None:
        """Test cargo command with custom parameters."""
        result = run_cargo_command(["check", "--dry-run"], check=False)
        assert isinstance(result.returncode, int)
        assert isinstance(result.stdout, str)


class TestRunSafeCommand:
    def test_basic_command_execution(self) -> None:
        """Test basic command execution with default parameters."""
        result = run_safe_command(sys.executable, ["-c", "import sys; print(sys.argv[1])", "hello world"])
        assert result.returncode == 0
        assert result.stdout.strip() == "hello world"
        assert isinstance(result.stdout, str)

    def test_secure_defaults_are_applied(self) -> None:
        """Test that secure defaults are applied."""
        result = run_safe_command(sys.executable, ["-c", "import sys; print(sys.argv[1])", "test"])
        assert isinstance(result.stdout, str)
        assert result.stdout.strip() == "test"

    def test_text_parameter_enforced(self) -> None:
        """Test that text parameter is enforced for security/stability."""
        result = run_safe_command(sys.executable, ["-c", "import sys; print(sys.argv[1])", "test output"], text=False)
        assert isinstance(result.stdout, str)
        assert "test output" in result.stdout

    def test_custom_check_parameter(self) -> None:
        """Test overriding check parameter."""
        result = run_safe_command("git", ["invalid-git-subcommand-xyz"], check=False)
        assert result.returncode != 0

    def test_custom_capture_output_parameter(self) -> None:
        """Test overriding capture_output parameter."""
        result = run_safe_command(sys.executable, ["-c", "import sys; print(sys.argv[1])", "no capture"], capture_output=False)
        assert result.stdout is None

    def test_multiple_custom_parameters(self) -> None:
        """Test multiple custom parameters at once (text is enforced)."""
        result = run_safe_command(sys.executable, ["-c", "import sys; print(sys.argv[1])", "multi param test"], text=False, check=False, capture_output=True)
        assert isinstance(result.stdout, str)
        assert result.returncode == 0
        assert "multi param test" in result.stdout

    def test_nonexistent_command_raises_error(self) -> None:
        """Test that nonexistent commands raise ExecutableNotFoundError."""
        with pytest.raises(ExecutableNotFoundError):
            run_safe_command("definitely-nonexistent-command", ["arg"])

    def test_additional_kwargs_passed_through(self) -> None:
        """Test that additional kwargs are passed through to subprocess.run."""
        result = run_safe_command(sys.executable, ["-c", "import sys; print(sys.argv[1])", "timeout test"], timeout=10)
        assert result.returncode == 0
        assert "timeout test" in result.stdout

    def test_resolves_arbitrary_command_and_preserves_hardening(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict[str, Any] = {}
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda command: f"/tools/{command}")

        def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls["args"] = args
            calls["kwargs"] = kwargs
            return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

        monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)
        result = run_safe_command("ruff", ["check", "-"], input="value = 1\n", timeout=30)
        assert result.stdout == "ok"
        assert calls["args"] == ["/tools/ruff", "check", "-"]
        assert calls["kwargs"]["input"] == "value = 1\n"
        assert calls["kwargs"]["timeout"] == 30
        assert calls["kwargs"]["check"] is True


class TestErrorHandling:
    def test_executable_not_found_error_attributes(self) -> None:
        """Test ExecutableNotFoundError has proper attributes."""
        error = ExecutableNotFoundError("test message")
        assert str(error) == "test message"
        assert isinstance(error, Exception)

    def test_git_functions_handle_missing_git(self, monkeypatch) -> None:
        """Test git functions handle missing git executable gracefully."""

        def mock_get_safe_executable(command) -> str:
            if command == "git":
                raise ExecutableNotFoundError(f"Required executable '{command}' not found in PATH")
            return "/bin/echo"

        monkeypatch.setattr("research_repo_tools.process.get_safe_executable", mock_get_safe_executable)
        assert check_git_repo() is False
        assert check_git_history() is False
        with pytest.raises(ExecutableNotFoundError):
            get_git_commit_hash()
        with pytest.raises(ExecutableNotFoundError):
            get_git_remote_url()

    def test_git_discovery_checks_handle_failed_git_commands(self, monkeypatch) -> None:
        """Git discovery helpers return False for nonzero git probes."""

        def mock_run_git_command(_args) -> None:
            raise subprocess.CalledProcessError(128, "git")

        monkeypatch.setattr("research_repo_tools.process.run_git_command", mock_run_git_command)
        assert check_git_repo() is False
        assert check_git_history() is False


class TestSecurityFeatures:
    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed in PATH")
    def test_uses_full_executable_paths(self) -> None:
        """Test that commands use full executable paths."""
        git_path = get_safe_executable("git")
        assert Path(git_path).is_absolute()
        assert "git" in git_path

    def test_no_shell_execution(self) -> None:
        """Test that commands don't use shell=True."""
        result = run_safe_command(sys.executable, ["-c", "import sys; print(sys.argv[1])", "$HOME"])
        assert result.stdout.strip() == "$HOME"

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed in PATH")
    def test_check_parameter_security_default(self) -> None:
        """Test that check=True is the default for security."""
        with pytest.raises(subprocess.CalledProcessError):
            run_safe_command("git", ["invalid-git-subcommand-xyz"])

    @pytest.mark.parametrize(
        ("function", "args", "kwargs"),
        [
            (run_git_command, (["status"],), {"executable": "/malicious/fake/git"}),
            (run_cargo_command, (["--version"],), {"executable": "/malicious/fake/cargo"}),
            (run_safe_command, ("echo", ["test"]), {"executable": "/malicious/fake/command"}),
        ],
    )
    def test_rejects_executable_override(self, function, args, kwargs, monkeypatch) -> None:
        """Test that functions reject executable override for security."""
        called = {"run": False}

        def fake_run(*_a: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
            called["run"] = True
            msg = "subprocess.run should not be called on override"
            raise AssertionError(msg)

        monkeypatch.setattr("subprocess.run", fake_run)
        with pytest.raises(ValueError, match="Overriding 'executable' is not allowed"):
            function(*args, **kwargs)
        assert called["run"] is False

    def test_run_git_command_with_input_rejects_executable_override(self) -> None:
        """Test that run_git_command_with_input raises ValueError when executable is overridden."""
        with pytest.raises(ValueError, match="Overriding 'executable' is not allowed"):
            run_git_command_with_input(["hash-object", "--stdin"], "test content", executable="/malicious/fake/git")

    def test_run_git_command_with_input_rejects_input_override(self) -> None:
        """Test that callers cannot replace the wrapper-managed input payload."""
        with pytest.raises(ValueError, match="Overriding 'input' is not allowed"):
            run_git_command_with_input(["hash-object", "--stdin"], "test content", input="replacement")

    def test_run_git_command_with_input_rejects_stdin_override(self) -> None:
        """Test that callers cannot replace the wrapper-managed stdin stream."""
        with pytest.raises(ValueError, match="Overriding 'stdin' is not allowed"):
            run_git_command_with_input(["hash-object", "--stdin"], "test content", stdin=subprocess.PIPE)

    @pytest.mark.parametrize(
        ("run_options", "expected_encoding", "expected_errors"),
        [
            ({}, "utf-8", "strict"),
            ({"encoding": "", "errors": ""}, "utf-8", "strict"),
            ({"encoding": "ascii", "errors": "backslashreplace"}, "ascii", "backslashreplace"),
        ],
    )
    def test_run_git_command_with_input_forwards_resolved_codec_options(
        self, monkeypatch: pytest.MonkeyPatch, run_options: dict[str, Any], expected_encoding: str, expected_errors: str
    ) -> None:
        """Resolved codec options govern both input encoding and subprocess output."""
        observed: dict[str, object] = {}

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            observed["payload"] = kwargs["input"]
            assert kwargs["text"] is False
            assert "encoding" not in kwargs
            return subprocess.CompletedProcess(args, 0, "café".encode(expected_encoding, expected_errors), b"")

        monkeypatch.setattr("research_repo_tools.process.get_safe_executable", lambda _command: "git")
        monkeypatch.setattr("subprocess.run", fake_run)
        result = run_git_command_with_input(["hash-object", "--stdin"], "café", **run_options)
        assert result.stdout == "café".encode(expected_encoding, expected_errors).decode(expected_encoding, expected_errors)
        assert observed == {"payload": "café".encode(expected_encoding, expected_errors)}

    def test_run_git_command_with_input_accepts_bytes(self) -> None:
        """Test that git stdin helpers accept bytes payloads."""
        result = run_git_command_with_input(["hash-object", "--stdin"], b"test content")
        digest = result.stdout.strip()
        assert len(digest) == 40
        assert all((character in "0123456789abcdef" for character in digest))


class TestBuildRunKwargs:
    def test_uses_finite_default_timeout(self) -> None:
        kwargs = _build_run_kwargs("test_func")
        assert math.isfinite(kwargs["timeout"])
        assert kwargs["timeout"] == DEFAULT_COMMAND_TIMEOUT_SECONDS

    def test_respects_explicit_longer_timeout(self) -> None:
        kwargs = _build_run_kwargs("test_func", timeout=1800)
        assert kwargs["timeout"] == 1800

    def test_defaults(self) -> None:
        kwargs = _build_run_kwargs("test_func")
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["timeout"] == DEFAULT_COMMAND_TIMEOUT_SECONDS

    def test_rejects_shell_true(self) -> None:
        kwargs: dict[str, Any] = {"shell": True}
        with pytest.raises(ValueError, match="shell=True is not allowed in test_function"):
            _build_run_kwargs("test_function", **kwargs)

    def test_rejects_executable_override(self) -> None:
        kwargs: dict[str, Any] = {"executable": "/malicious/fake-git"}
        with pytest.raises(ValueError, match="Overriding 'executable' is not allowed in test_function"):
            _build_run_kwargs("test_function", **kwargs)

    def test_strips_text_kwarg(self) -> None:
        """User-provided text=False is ignored; we always enforce text=True."""
        kwargs = _build_run_kwargs("test_func", text=False)
        assert kwargs["text"] is True

    def test_allows_check_false(self) -> None:
        kwargs = _build_run_kwargs("test_func", check=False)
        assert kwargs["check"] is False

    def test_respects_custom_encoding(self) -> None:
        kwargs = _build_run_kwargs("test_func", encoding="latin-1")
        assert kwargs["encoding"] == "latin-1"

    def test_respects_custom_timeout(self) -> None:
        kwargs = _build_run_kwargs("test_func", timeout=12.5)
        assert kwargs["timeout"] == 12.5

    def test_applies_secure_defaults(self) -> None:
        kwargs = _build_run_kwargs("test_function")
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is True
        assert kwargs["encoding"] == "utf-8"

    def test_allows_safe_overrides_and_extra_kwargs(self) -> None:
        kwargs = _build_run_kwargs("test_function", capture_output=False, check=False, timeout=30)
        assert kwargs["capture_output"] is False
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 30
        assert kwargs["text"] is True

    def test_ignores_text_override_to_keep_string_output(self) -> None:
        kwargs = _build_run_kwargs("test_function", text=False)
        assert kwargs["text"] is True


class TestFindProjectRoot:
    def test_accepts_nested_directory_start(self, tmp_path: Path) -> None:
        root = tmp_path / "checkout"
        nested = root / "target" / "wheel"
        nested.mkdir(parents=True)
        (root / "Cargo.toml").write_text("[package]\n", encoding="utf-8", newline="\n")
        assert find_project_root(nested) == root

    def test_accepts_file_start(self, tmp_path: Path) -> None:
        root = tmp_path / "checkout"
        source = root / "scripts" / "tool.py"
        source.parent.mkdir(parents=True)
        source.write_text("", encoding="utf-8", newline="\n")
        (root / "Cargo.toml").write_text("[package]\n", encoding="utf-8", newline="\n")
        assert find_project_root(source) == root


class TestFormatExceptionDiagnostics:
    def test_preserves_nested_subprocess_output(self) -> None:
        failure = subprocess_utils.subprocess.CalledProcessError(128, ["git", "tag", "v1.2.3"], output="tag stdout", stderr="tag rejected by hook")
        error = ExceptionGroup("publication and rollback failed", [failure, OSError("rollback target unavailable")])
        rendered = format_exception_diagnostics(error)
        assert "publication and rollback failed (2 sub-exceptions)" in rendered
        assert "git tag v1.2.3" in rendered
        assert "tag stdout" in rendered
        assert "tag rejected by hook" in rendered
        assert "rollback target unavailable" in rendered


class TestRunGitCommandWithInput:
    def test_passes_stdin_data(self) -> None:
        """Use git hash-object --stdin to verify input piping works."""
        result = run_git_command_with_input(["hash-object", "--stdin"], input_data="hello\n")
        assert result.returncode == 0
        assert result.stdout.strip() == "ce013625030ba8dba906f756967f9e9ca394464a"

    def test_input_data_forwarded_as_raw_utf8(self) -> None:
        """Verify stdin bytes preserve LF even when subprocess output is text."""
        observed_input = b""

        def capture_run(*_args: object, **kwargs: object) -> subprocess_utils.subprocess.CompletedProcess[bytes]:
            nonlocal observed_input
            observed_input = cast(bytes, kwargs["input"])
            return subprocess_utils.subprocess.CompletedProcess(args=["git"], returncode=0, stdout=b"", stderr=b"")

        with (
            patch("research_repo_tools.process.get_safe_executable", return_value="/usr/bin/git") as mock_executable,
            patch("research_repo_tools.process.subprocess.run", side_effect=capture_run) as mock_run,
        ):
            run_git_command_with_input(["tag", "-a", "v1.0.0", "-F", "-"], input_data="tag body\n")
        mock_executable.assert_called_once_with("git")
        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert observed_input == b"tag body\n"
        assert "stdin" not in kwargs
        assert kwargs["text"] is False

    def test_binary_input_forwarded_without_newline_or_encoding_changes(self) -> None:
        observed_input = b""

        def capture_run(*_args: object, **kwargs: object) -> subprocess_utils.subprocess.CompletedProcess[bytes]:
            nonlocal observed_input
            observed_input = cast(bytes, kwargs["input"])
            return subprocess_utils.subprocess.CompletedProcess(args=["git"], returncode=0, stdout=b"", stderr=b"")

        payload = b"line\r\n\x00\xff"
        with (
            patch("research_repo_tools.process.get_safe_executable", return_value="/usr/bin/git"),
            patch("research_repo_tools.process.subprocess.run", side_effect=capture_run),
        ):
            run_git_command_with_input(["apply", "--binary"], input_data=payload)
        assert observed_input == payload

    @pytest.mark.parametrize(
        "contents",
        ["", "line\n", "line\r\n", "line\r", "line é\nsecond\r\nlast\r\x00", b"line\r\n", b"byte\xe9\r\n\x00"],
        ids=["empty", "lf", "crlf", "cr", "mixed-unicode", "bytes-crlf", "raw-bytes"],
    )
    def test_preserves_stdin_bytes_with_windows_text_pipes(self, monkeypatch: pytest.MonkeyPatch, contents: str | bytes) -> None:
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda _command: "/tools/git")

        def windows_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            payload = kwargs["input"]
            text_mode = kwargs.get("text") or kwargs.get("encoding") or kwargs.get("universal_newlines")
            if text_mode:
                with io.BytesIO() as buffer, io.TextIOWrapper(buffer, encoding="utf-8", newline="\r\n", write_through=True) as pipe:
                    pipe.write(payload)
                    payload = buffer.getvalue()
            output = payload.hex() + "\n"
            return subprocess.CompletedProcess(args, 0, output if text_mode else output.encode(), "" if text_mode else b"")

        monkeypatch.setattr(subprocess_utils.subprocess, "run", windows_run)
        result = run_git_command_with_input(["--no-pager", "hash-object", "--stdin"], contents)
        assert result.returncode == 0
        expected = contents.encode("utf-8") if isinstance(contents, str) else contents
        assert result.stdout == expected.hex() + "\n"
        assert result.stderr == ""

    @pytest.mark.parametrize(
        "contents",
        ["", "line\n", "line\r\n", "line\r", "line é\nsecond\r\nlast\r\x00", b"line\r\n", b"byte\xe9\r\n\x00"],
        ids=["empty", "lf", "crlf", "cr", "mixed-unicode", "bytes-crlf", "raw-bytes"],
    )
    def test_hashes_the_same_bytes_as_a_literal_file(self, tmp_path: Path, contents: str | bytes) -> None:
        if shutil.which("git") is None:
            pytest.skip("git is required to compare stdin with file hashing")
        source = tmp_path / "payload.txt"
        source.write_bytes(contents.encode("utf-8") if isinstance(contents, str) else contents)
        expected = run_git_command(["--no-pager", "hash-object", "--no-filters", str(source)], cwd=tmp_path, timeout=30)
        actual = run_git_command_with_input(["--no-pager", "hash-object", "--no-filters", "--stdin"], contents, cwd=tmp_path, timeout=30)
        assert actual.returncode == 0
        assert actual.stdout == expected.stdout
        assert actual.stderr == ""

    def test_passes_stdin_to_git(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        calls: dict[str, Any] = {}
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda _command: "/usr/bin/git")

        def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            calls["args"] = args
            calls["kwargs"] = kwargs
            return subprocess.CompletedProcess(args, 0, stdout=b"hash\r\n", stderr=b"notice\rnext\r\n")

        monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)
        result = run_git_command_with_input(["hash-object", "--stdin"], "content", cwd=tmp_path)
        assert result.stdout == "hash\n"
        assert result.stderr == "notice\nnext\n"
        assert calls["args"] == ["/usr/bin/git", "hash-object", "--stdin"]
        assert calls["kwargs"]["cwd"] == tmp_path
        assert calls["kwargs"]["input"] == b"content"
        assert calls["kwargs"]["text"] is False
        assert "encoding" not in calls["kwargs"]

    @pytest.mark.parametrize("check", [True, False])
    def test_failed_command_preserves_text_diagnostics(self, monkeypatch: pytest.MonkeyPatch, check: bool) -> None:
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda _command: "/tools/git")
        monkeypatch.setattr(subprocess_utils.subprocess, "run", lambda args, **_kwargs: subprocess.CompletedProcess(args, 7, b"partial\r\n", b"failed\r\n"))
        if check:
            with pytest.raises(subprocess.CalledProcessError) as raised:
                run_git_command_with_input(["--no-pager", "hash-object", "--stdin"], "content", check=True)
            outcome = raised.value
            assert outcome.cmd == ["/tools/git", "--no-pager", "hash-object", "--stdin"]
        else:
            outcome = run_git_command_with_input(["--no-pager", "hash-object", "--stdin"], "content", check=False)
        assert outcome.returncode == 7
        assert outcome.stdout == "partial\n"
        assert outcome.stderr == "failed\n"

    @pytest.mark.parametrize(("encoding", "errors", "encoded"), [("latin-1", "strict", b"line \xe9\r\n"), ("ascii", "replace", b"line ?\r\n")])
    def test_honors_encoding_and_error_policy_without_newline_translation(
        self, monkeypatch: pytest.MonkeyPatch, encoding: str, errors: str, encoded: bytes
    ) -> None:
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda _command: "/tools/git")

        def echo(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            assert kwargs["input"] == encoded
            assert kwargs["text"] is False
            assert "encoding" not in kwargs
            assert "errors" not in kwargs
            assert "universal_newlines" not in kwargs
            return subprocess.CompletedProcess(args, 0, kwargs["input"], b"")

        monkeypatch.setattr(subprocess_utils.subprocess, "run", echo)
        result = run_git_command_with_input([], "line é\r\n", encoding=encoding, errors=errors, universal_newlines=True)
        assert result.stdout == encoded.decode(encoding).replace("\r\n", "\n")

    def test_allows_uncaptured_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda _command: "/tools/git")

        def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            assert kwargs["capture_output"] is False
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess_utils.subprocess, "run", run)
        result = run_git_command_with_input([], "content", capture_output=False)
        assert result.returncode == 0
        assert result.stdout is None
        assert result.stderr is None

    def test_preserves_timeout_and_partial_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess_utils, "get_safe_executable", lambda _command: "/tools/git")
        timeout = subprocess.TimeoutExpired(["/tools/git"], 3, output=b"partial\r\n", stderr=b"diagnostic\r\n")

        def run(_args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            assert kwargs["timeout"] == 3
            raise timeout

        monkeypatch.setattr(subprocess_utils.subprocess, "run", run)
        with pytest.raises(subprocess.TimeoutExpired) as raised:
            run_git_command_with_input([], "content", timeout=3)
        assert raised.value is timeout
        assert raised.value.output == b"partial\r\n"
        assert raised.value.stderr == b"diagnostic\r\n"

    def test_rejects_executable_override(self) -> None:
        kwargs: dict[str, Any] = {"executable": "/malicious/fake-git"}
        with pytest.raises(ValueError, match="Overriding 'executable' is not allowed"):
            run_git_command_with_input(["hash-object", "--stdin"], "content", **kwargs)


class TestAdditionalHelpers:
    def test_cpu_description_uses_macos_brand_and_architecture(self) -> None:
        result = subprocess_utils.subprocess.CompletedProcess(args=["sysctl"], returncode=0, stdout="Apple M4 Pro\n", stderr="")
        with (
            patch("research_repo_tools.process.platform.system", return_value="Darwin"),
            patch("research_repo_tools.process.platform.machine", return_value="arm64"),
            patch("research_repo_tools.process.platform.processor", return_value="arm"),
            patch("research_repo_tools.process.run_safe_command", return_value=result),
        ):
            assert cpu_description() == "Apple M4 Pro (arm64)"

    def test_run_cargo_command_uses_safe_executable(self) -> None:
        with (
            patch("research_repo_tools.process.get_safe_executable", return_value="/usr/bin/cargo") as mock_executable,
            patch("research_repo_tools.process.subprocess.run") as mock_run,
        ):
            run_cargo_command(["--version"])
        mock_executable.assert_called_once_with("cargo")
        mock_run.assert_called_once()
        args, _kwargs = mock_run.call_args
        assert args[0] == ["/usr/bin/cargo", "--version"]

    def test_run_safe_command_uses_safe_executable(self) -> None:
        with (
            patch("research_repo_tools.process.get_safe_executable", return_value="/usr/bin/gnuplot") as mock_executable,
            patch("research_repo_tools.process.subprocess.run") as mock_run,
        ):
            run_safe_command("gnuplot", ["--version"])
        mock_executable.assert_called_once_with("gnuplot")
        mock_run.assert_called_once()
        args, _kwargs = mock_run.call_args
        assert args[0] == ["/usr/bin/gnuplot", "--version"]

    @patch("research_repo_tools.process.run_git_command")
    def test_git_convenience_helpers(self, mock_run_git: MagicMock) -> None:

        def fake_run_git(args: list[str], **_kwargs: object) -> subprocess_utils.subprocess.CompletedProcess[str]:
            stdout_by_args = {
                ("rev-parse", "HEAD"): "abc123def456\n",
                ("remote", "get-url", "origin"): "https://github.com/example/repo.git\n",
                ("rev-parse", "--git-dir"): ".git\n",
                ("log", "--oneline", "-n", "1"): "abc123d message\n",
            }
            return subprocess_utils.subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout=stdout_by_args[tuple(args)])

        mock_run_git.side_effect = fake_run_git
        assert get_git_commit_hash() == "abc123def456"
        assert get_git_remote_url() == "https://github.com/example/repo.git"
        assert check_git_repo() is True
        assert check_git_history() is True
        assert [call_args.args[0] for call_args in mock_run_git.call_args_list] == [
            ["rev-parse", "HEAD"],
            ["remote", "get-url", "origin"],
            ["rev-parse", "--git-dir"],
            ["log", "--oneline", "-n", "1"],
        ]

    def test_find_project_root(self, tmp_path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "consumer"\nversion = "1.0.0"\n', newline="\n")
        assert (find_project_root() / "Cargo.toml").is_file()
