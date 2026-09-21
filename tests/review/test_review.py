"""Synthetic review contracts; no remote Git or CodeRabbit service access."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from research_repo_tools import cli, process, review

SHA = "a" * 40


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("Consumer instructions\n", newline="\n")
    (tmp_path / ".coderabbit.yaml").write_text("reviews: {}\n", newline="\n")
    return tmp_path


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    git = Mock(side_effect=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, SHA + ("\trefs/heads/main\n" if "ls-remote" in args else "\n")))
    rabbit = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(review, "get_safe_executable", lambda name: "/tools/coderabbit")
    monkeypatch.setattr(review, "run_git_command", git)
    monkeypatch.setattr(review, "run_safe_command", rabbit)
    return git, rabbit


def test_default_branch_verifies_remote_and_streams(root: Path, commands: tuple[Mock, Mock]) -> None:
    git, rabbit = commands
    assert cli.main(["--root", str(root), "review", "branch"]) == 0
    assert [call.args[0] for call in git.call_args_list] == [
        ["--no-pager", "rev-parse", "--verify", "--end-of-options", "refs/remotes/origin/main^{commit}"],
        ["--no-pager", "ls-remote", "--exit-code", "origin", "refs/heads/main"],
    ]
    assert all(call.kwargs["cwd"] == root for call in git.call_args_list)
    rabbit.assert_called_once_with(
        "/tools/coderabbit",
        ["review", "--agent", "--include-untracked", "--base=origin/main", "--config", str(root / "AGENTS.md"), str(root / ".coderabbit.yaml")],
        cwd=root,
        capture_output=False,
        check=False,
        timeout=None,
    )


@pytest.mark.parametrize("base", ["main", "feature/$(touch-pwned);literal", "a" * 40])
def test_explicit_base_is_one_argument_and_never_queries_remote(root: Path, commands: tuple[Mock, Mock], base: str) -> None:
    git, rabbit = commands
    assert cli.main(["--root", str(root), "review", "branch", f"--base={base}"]) == 0
    assert git.call_count == 1
    assert git.call_args.args[0][-1] == base + "^{commit}"
    assert f"--base={base}" in rabbit.call_args.args[1]


def test_uncommitted_skips_git_and_accepts_yml(root: Path, commands: tuple[Mock, Mock]) -> None:
    (root / ".coderabbit.yaml").rename(root / ".coderabbit.yml")
    git, rabbit = commands
    assert cli.main(["--root", str(root), "review", "uncommitted"]) == 0
    git.assert_not_called()
    args = rabbit.call_args.args[1]
    assert "--uncommitted" in args and not any(arg.startswith("--base") for arg in args)
    assert str(root / ".coderabbit.yml") in args


@pytest.mark.parametrize("state", ["stale", "missing", "partial-failure", "remote-timeout", "empty", "wrong-ref", "multiple", "bad-hash"])
def test_bad_freshness_never_starts_review(root: Path, commands: tuple[Mock, Mock], capsys: pytest.CaptureFixture[str], state: str) -> None:
    git, rabbit = commands
    remote = SHA + "\trefs/heads/main\n"
    results: list[object] = [subprocess.CompletedProcess([], 0, SHA + "\n")]
    if state == "missing":
        results = [subprocess.CalledProcessError(128, "git")]
    else:
        remote_results: dict[str, object] = {
            "stale": subprocess.CompletedProcess([], 0, "b" * 40 + "\trefs/heads/main\n"),
            "partial-failure": subprocess.CalledProcessError(1, "git", output=remote),
            "remote-timeout": subprocess.TimeoutExpired("git", 300, output=remote),
            "empty": subprocess.CompletedProcess([], 0, ""),
            "wrong-ref": subprocess.CompletedProcess([], 0, SHA + "\trefs/heads/other\n"),
            "multiple": subprocess.CompletedProcess([], 0, remote * 2),
            "bad-hash": subprocess.CompletedProcess([], 0, "abc\trefs/heads/main\n"),
        }
        results.append(remote_results[state])
    git.side_effect = results
    assert cli.main(["--root", str(root), "review", "branch"]) == 1
    rabbit.assert_not_called()
    diagnostic = capsys.readouterr().err
    assert "git fetch origin" in diagnostic if state in {"stale", "missing"} else "Cannot verify" in diagnostic


@pytest.mark.parametrize("base", ["", "--help", "main\nother", "main branch", "main\0"])
def test_invalid_base_rejected(root: Path, commands: tuple[Mock, Mock], base: str) -> None:
    git, rabbit = commands
    assert cli.main(["--root", str(root), "review", "branch", f"--base={base}"]) == 1
    git.assert_not_called()
    rabbit.assert_not_called()


@pytest.mark.parametrize("state", ["agents-missing", "config-missing", "ambiguous", "directory"])
def test_instruction_errors_stop_before_commands(root: Path, commands: tuple[Mock, Mock], state: str) -> None:
    git, rabbit = commands
    if state == "agents-missing":
        (root / "AGENTS.md").unlink()
    elif state == "ambiguous":
        (root / ".coderabbit.yml").write_text("reviews: {}\n", newline="\n")
    else:
        (root / ".coderabbit.yaml").unlink()
        if state == "directory":
            (root / ".coderabbit.yaml").mkdir()
    assert cli.main(["--root", str(root), "review", "uncommitted"]) == 1
    git.assert_not_called()
    rabbit.assert_not_called()


def test_missing_cli_is_actionable(root: Path, commands: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    missing = Mock(side_effect=process.ExecutableNotFoundError("missing"))
    monkeypatch.setattr(review, "get_safe_executable", missing)
    assert cli.main(["--root", str(root), "review", "branch"]) == 1
    assert "Install and authenticate it explicitly" in capsys.readouterr().err
    commands[0].assert_not_called()
    commands[1].assert_not_called()


@pytest.mark.parametrize("status,expected", [(0, 0), (7, 7), (-15, 143)])
def test_exit_status(root: Path, commands: tuple[Mock, Mock], status: int, expected: int) -> None:
    commands[1].return_value = subprocess.CompletedProcess([], status)
    assert cli.main(["--root", str(root), "review", "uncommitted"]) == expected


def test_interruption(root: Path, commands: tuple[Mock, Mock], capsys: pytest.CaptureFixture[str]) -> None:
    commands[1].side_effect = KeyboardInterrupt
    assert cli.main(["--root", str(root), "review", "uncommitted"]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_real_child_inherits_streams_and_has_no_default_timeout(
    root: Path, commands: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    script = root / "fake_coderabbit.py"
    script.write_text(
        "import sys, time\nprint('progress', flush=True)\ntime.sleep(0.1)\nprint('service unavailable', file=sys.stderr)\nsys.exit(9)\n", newline="\n"
    )
    monkeypatch.setattr(process, "DEFAULT_COMMAND_TIMEOUT_SECONDS", 0.001)

    def child(command: str, args: list[str], *, cwd: Path, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return process.run_safe_command(sys.executable, [str(script), *args], cwd=cwd, **kwargs)

    monkeypatch.setattr(review, "run_safe_command", child)
    assert cli.main(["--root", str(root), "review", "uncommitted"]) == 9
    output = capfd.readouterr()
    assert "progress" in output.out
    assert "service unavailable" in output.err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable stub; CLI behavior is tested on all platforms")
@pytest.mark.parametrize("template", [False, True])
@pytest.mark.parametrize(
    "recipe,expected",
    [
        (["review"], ["branch", "--base=origin/main"]),
        (["review", "topic/$(touch SHOULD_NOT_EXIST);literal"], ["branch", "--base=topic/$(touch SHOULD_NOT_EXIST);literal"]),
        (["review-uncommitted"], ["uncommitted"]),
    ],
)
def test_just_recipe_forwards_arguments_without_shell_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: bool, recipe: list[str], expected: list[str]
) -> None:
    import json
    import os

    source_root = Path(__file__).resolve().parents[2]
    source = source_root / ("src/research_repo_tools/templates/justfile" if template else "justfile")
    (tmp_path / "justfile").write_text(source.read_text(), newline="\n")
    uv = tmp_path / "uv"
    uv.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n", newline="\n")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = process.run_safe_command("just", ["--justfile", str(tmp_path / "justfile"), *recipe], cwd=tmp_path)
    args = json.loads(result.stdout)
    prefix = ["run", "--locked", *(["--group", "dev"] if template else []), "research-repo-tools", "review"]
    assert args == [*prefix, *expected]
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()
