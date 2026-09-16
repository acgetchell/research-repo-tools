"""Render packaged release summaries with the real, read-only git-cliff CLI."""

import shutil
from pathlib import Path

import pytest

from research_repo_tools.changelog import template
from research_repo_tools.postprocess_changelog import postprocess_text
from research_repo_tools.process import run_git_command, run_safe_command

pytestmark = pytest.mark.skipif(shutil.which("git-cliff") is None, reason="external git-cliff is required for template rendering")


def render(tmp_path: Path, message: str) -> str:
    # An empty range reads the existing checkout; --with-commit only adds
    # messages to git-cliff's input and never creates Git objects or refs.
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists() or shutil.which("git") is None:
        pytest.skip("custom commit parsing requires an existing Git checkout")
    if run_git_command(["--no-pager", "rev-parse", "--verify", "HEAD"], cwd=root, check=False).returncode:
        pytest.skip("custom commit parsing requires an existing HEAD")
    configuration = tmp_path / "cliff.toml"
    configuration.write_text(template("cliff.toml", owner="example", repository="consumer"), encoding="utf-8")
    arguments = ["--offline", "--no-exec", "--config", str(configuration), "--strip", "footer"]
    arguments += ["--with-commit", message, "--with-commit", "fix: retain linked entry (#987)", "HEAD..HEAD"]
    result = run_safe_command("git-cliff", arguments, cwd=root, check=False)
    assert result.returncode == 0, result.stderr
    return postprocess_text(result.stdout)


@pytest.mark.parametrize(
    "message,expected",
    [
        pytest.param(
            "perf!: tune kernels\n\nKeep arithmetic unchanged.\n\nBREAKING CHANGE: Require compiler 2.0.",
            "- Require compiler 2.0.",
            id="compiler-footer",
        ),
        pytest.param(
            "fix: change result storage\n\nBREAKING CHANGE: Return `Result<T>`.\nUse `convert()` for old storage.\n\nKeep explicit conversions.",
            "- Return `Result&lt;T&gt;`.\n  Use `convert()` for old storage.\n\n  Keep explicit conversions.",
            id="multiline-footer",
        ),
        pytest.param(
            "refactor!: remove legacy API",
            "- Remove legacy API",
            id="bang-only",
        ),
        pytest.param(
            "chore(deps): bump toolkit\n\nBREAKING CHANGE: Require a newer runtime.",
            "- Require a newer runtime.",
            id="dependency-footer",
        ),
    ],
)
def test_template_retains_full_breaking_descriptions(tmp_path: Path, message: str, expected: str) -> None:
    result = render(tmp_path, message)
    summary = result.split("### ⚠️ Breaking Changes\n\n", 1)[1].split("\n### ", 1)[0].strip()
    assert summary == expected
    assert result.count("### ⚠️ Breaking Changes") == result.count("### Merged Pull Requests") == 1
    assert result.index("### ⚠️ Breaking Changes") < result.index("### Merged Pull Requests")
    pr_summary = result.split("### Merged Pull Requests\n\n", 1)[1].split("\n### ", 1)[0]
    assert "- Retain linked entry [#987](https://github.com/example/consumer/pull/987)" in pr_summary
    assert postprocess_text(result) == result


def test_ordinary_commit_does_not_create_a_breaking_summary(tmp_path: Path) -> None:
    result = render(tmp_path, "fix: preserve result storage")
    assert "### ⚠️ Breaking Changes" not in result
    assert "### Merged Pull Requests" in result
