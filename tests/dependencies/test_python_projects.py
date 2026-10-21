"""Public update orchestration rejects bad inventories and isolates uv projects."""

import json
import subprocess
from pathlib import Path

import pytest

from research_repo_tools import cli, python_updates


def manifest(project: Path, *, projects: tuple[str, ...] = ()) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    path = project / "pyproject.toml"
    path.write_text(
        '[project]\nname="fixture"\nversion="0.0.0"\nrequires-python=">=3.15"\n'
        "[dependency-groups]\ndev=[]\n"
        '[tool.uv]\nrequired-version="==0.13.4"\n'
        f"[tool.research-repo-tools.deps]\npython-projects={json.dumps(projects)}\n",
        encoding="utf-8",
        newline="\n",
    )
    (project / ".python-version").write_text("3.15\n", encoding="utf-8", newline="\n")
    return path


@pytest.mark.parametrize(
    "projects,diagnostic",
    [
        (("missing",), "existing file"),
        (("../outside",), "repository-relative"),
        (("/absolute",), "repository-relative"),
        (("C:/outside",), "repository-relative"),
        (("tooling\\scanner",), "repository-relative"),
        ((".",), "repository-relative"),
        (("scanner", "scanner"), "duplicate"),
        (("scanner", "Scanner"), "duplicate"),
    ],
)
def test_invalid_inventory_fails_before_any_update(tmp_path, monkeypatch, capsys, projects, diagnostic):
    path = manifest(tmp_path, projects=projects)
    manifest(tmp_path / "scanner")
    original = path.read_bytes()
    monkeypatch.setattr(python_updates.dependencies, "main", lambda *args, **kwargs: pytest.fail("updated before preflight"))
    assert cli.main(["--root", str(tmp_path), "deps", "update-python", "--all-projects"]) == 1
    assert diagnostic in capsys.readouterr().err
    assert path.read_bytes() == original
    assert not (tmp_path / "uv.lock").exists()
    assert not (tmp_path / ".venv").exists()


def test_workspace_member_is_not_updated_as_an_independent_project(tmp_path, capsys):
    path = manifest(tmp_path, projects=("scanner",))
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('[tool.uv.workspace]\nmembers=["scanner"]\n')
    manifest(tmp_path / "scanner")
    assert cli.main(["--root", str(tmp_path), "deps", "update-python", "--all-projects"]) == 1
    assert "workspace member" in capsys.readouterr().err
    assert not (tmp_path / "uv.lock").exists()


@pytest.mark.parametrize("failure", [None, "pins", "root-lock", "scanner-lock"])
def test_update_isolates_environments_and_stops_on_failure(tmp_path, monkeypatch, failure):
    manifest(tmp_path, projects=("tooling/scanner", "tooling/later"))
    for name in ("scanner", "later"):
        manifest(tmp_path / "tooling" / name)
    monkeypatch.setattr(python_updates.toolchain.Runtime, "inspect", lambda self: [])
    monkeypatch.setattr(
        python_updates.toolchain.Runtime,
        "project_environment",
        lambda self: {
            "UV_PYTHON": "/other/python",
            "VIRTUAL_ENV": "/other/venv",
            "UV_PROJECT_ENVIRONMENT": "/other/venv",
            "UV_CONFIG_FILE": "/other/config",
            "UV_OFFLINE": "1",
            "PATH": "managed-tools",
        },
    )
    calls = []

    def pins(*args, **kwargs):
        assert kwargs["env"] == {"UV_OFFLINE": "1", "PATH": "managed-tools"}
        calls.append("pins")
        return 1 if failure == "pins" else 0

    def run(command, args, *, cwd, env, timeout):
        relative = cwd.relative_to(tmp_path).as_posix()
        label = ("root" if relative == "." else cwd.name) + "-" + args[0]
        calls.append(label)
        assert command == "uv"
        assert args[-4:] == ["--directory", str(cwd), "--project", str(cwd)]
        assert env == {"UV_OFFLINE": "1", "PATH": "managed-tools", "UV_PROJECT_ENVIRONMENT": str(cwd / ".venv")}
        assert ("--group" in args) == (relative == "." and args[0] == "sync")
        assert timeout > 0
        if label == failure:
            raise subprocess.CalledProcessError(23, [command, *args])

    monkeypatch.setattr(python_updates.dependencies, "main", pins)
    monkeypatch.setattr(python_updates, "run_command_live", run)
    assert cli.main(["--root", str(tmp_path), "deps", "update-python", "--all-projects"]) == (0 if failure is None else 1)
    expected = ["pins", "root-lock", "root-sync", "scanner-lock", "scanner-sync", "later-lock", "later-sync"]
    assert calls == (expected if failure is None else expected[: expected.index(failure) + 1])
