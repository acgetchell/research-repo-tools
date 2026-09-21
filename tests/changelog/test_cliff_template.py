"""Render packaged release summaries with the real, read-only git-cliff CLI."""

import json
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
    configuration.write_text(template("cliff.toml", owner="example", repository="consumer"), encoding="utf-8", newline="\n")
    arguments = ["--offline", "--no-exec", "--config", str(configuration)]
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
            "- Return `Result<T>`.\n  Use `convert()` for old storage.\n\n  Keep explicit conversions.",
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
        pytest.param(
            "refactor!: change generic API\n\nBREAKING CHANGE: Replace <Old> with ``Vector<T>`` and `Result<T, E>`.",
            "- Replace &lt;Old&gt; with ``Vector<T>`` and `Result<T, E>`.",
            id="code-spans",
        ),
        pytest.param(
            "refactor!: replace <Old> with `Vector<T>`",
            "- Replace &lt;Old&gt; with `Vector<T>`",
            id="bang-only-code-span",
        ),
        pytest.param(
            "refactor!: change generic API\n\nBREAKING CHANGE: Use this signature:\n\n```rust\nfn solve<T>() -> Result<T, Error>;\n```",
            "- Use this signature:\n\n  ```rust\n  fn solve<T>() -> Result<T, Error>;\n  ```",
            id="backtick-fenced-code",
        ),
        pytest.param(
            "refactor!: change generic API\n\nBREAKING CHANGE: Use this signature:\n\n~~~~rust\nfn solve<T>() -> Result<T, Error>;\n~~~~",
            "- Use this signature:\n\n  ~~~~rust\n  fn solve<T>() -> Result<T, Error>;\n  ~~~~",
            id="tilde-fenced-code",
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


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_ordinary_entries_preserve_rust_code_and_escape_prose(tmp_path: Path, fence: str) -> None:
    result = render(
        tmp_path,
        "feat: return `Result<T>` instead of <Legacy>\n\n"
        "Use ``Vec<T>``; keep literal `&lt;T&gt;`.\n\n"
        f"{fence}rust\nfn value<T>() -> Result<T, Error>;\n{fence}",
    )
    assert "Return `Result<T>` instead of &lt;Legacy&gt;" in result
    assert "Use ``Vec<T>``; keep literal `&lt;T&gt;`." in result
    assert f"{fence}rust\n  fn value<T>() -> Result<T, Error>;\n  {fence}" in result
    assert postprocess_text(result) == result


@pytest.mark.parametrize("scope", ["deps", "deps-dev", "deps-ci"])
def test_dependency_scopes_share_one_category(tmp_path: Path, scope: str) -> None:
    result = render(tmp_path, f"chore({scope}): bump pytest from 9.0 to 9.1\n\nDependency release details.")
    dependencies = result.split("### Dependencies\n\n", 1)[1].split("\n### ", 1)[0]
    assert "Bump pytest from 9.0 to 9.1" in dependencies
    assert "Dependency release details." not in result
    assert "### Maintenance" not in result


def test_breaking_dependency_scope_retains_migration_instructions(tmp_path: Path) -> None:
    result = render(tmp_path, "chore(deps-dev)!: bump toolkit\n\nBREAKING CHANGE: Require compiler 2.0.")
    assert "### Dependencies" in result
    assert "### ⚠️ Breaking Changes\n\n- Require compiler 2.0." in result


@pytest.mark.parametrize(
    "version,previous,expected",
    [
        (None, None, "[Unreleased]: https://github.com/example/consumer/commits/HEAD"),
        (None, "v1.0.0", "[Unreleased]: https://github.com/example/consumer/compare/v1.0.0...HEAD"),
        ("v1.0.0", None, "[1.0.0]: https://github.com/example/consumer/tree/v1.0.0"),
        ("v1.1.0", "v1.0.0", "[1.1.0]: https://github.com/example/consumer/compare/v1.0.0...v1.1.0"),
    ],
    ids=["unreleased-before-first-tag", "unreleased-after-tag", "first-release", "subsequent-release"],
)
def test_template_release_links(tmp_path: Path, version: str | None, previous: str | None, expected: str) -> None:
    configuration = tmp_path / "cliff.toml"
    configuration.write_text(template("cliff.toml", owner="example", repository="consumer"), encoding="utf-8", newline="\n")
    empty_release = {
        "commits": [],
        "timestamp": 0,
        "submodule_commits": {},
        **{host: {"contributors": []} for host in ("github", "gitlab", "gitea", "bitbucket", "azure_devops")},
    }
    release = {**empty_release, "version": version, "previous": None}
    if previous is not None:
        release["previous"] = {**empty_release, "version": previous}
    context = tmp_path / "context.json"
    context.write_text(json.dumps([release]), encoding="utf-8", newline="\n")
    result = run_safe_command(
        "git-cliff", ["--offline", "--no-exec", "--config", str(configuration), "--from-context", str(context)], cwd=tmp_path, check=False
    )
    assert result.returncode == 0, result.stderr
    assert expected in postprocess_text(result.stdout)
