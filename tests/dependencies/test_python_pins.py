"""Shared update python dev pins behavior and regression cases."""

import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from research_repo_tools import cli
from research_repo_tools import dependencies as update_python_dev_pins


def project_text(*requirements: str) -> str:
    """Return a minimal project with a mixed development requirement group."""
    rendered = "\n".join((f'    "{requirement}",' for requirement in requirements))
    return f'[build-system]\nrequires = ["setuptools>=83"]\n\n[project]\nname = "fixture"\nversion = "0.1.0"\nrequires-python = ">=3.14"\ndependencies = ["packaging>=26"]\n\n[dependency-groups]\ndev = [\n{rendered}\n]\n'


def resolution_metadata(args: list[str]) -> dict[str, object]:
    """Read the resolver's PEP 723 input while its temporary file exists."""
    assert args[:2] == ["export", "--script"]
    lines = Path(args[2]).read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# /// script" and lines[-1] == "# ///"
    return tomllib.loads("\n".join(line.removeprefix("# ") for line in lines[1:-1]))


def test_parse_project_selects_only_exact_simple_dev_pins() -> None:
    python_version, pins = update_python_dev_pins.parse_project(project_text("pytest>=9.1", "ruff==0.16.2", "semgrep==1.172.0", "ty~=0.0.66"))
    assert python_version == SpecifierSet(">=3.14")
    assert pins == [update_python_dev_pins.DevPin("ruff", "0.16.2"), update_python_dev_pins.DevPin("semgrep", "1.172.0")]


def test_parse_project_leaves_compound_and_wildcard_requirements_unmanaged() -> None:
    python_version, pins = update_python_dev_pins.parse_project(
        project_text("ruff==0.16.2,!=0.16.3", "semgrep==1.172.*", "ty==0.0.66; python_version >= '3.14'")
    )
    assert python_version == SpecifierSet(">=3.14")
    assert pins == []


def test_parse_project_accepts_group_without_exact_pins() -> None:
    python_version, pins = update_python_dev_pins.parse_project(project_text("pytest>=9.1", "ruff~=0.16"))
    assert python_version == SpecifierSet(">=3.14")
    assert pins == []


def test_tooling_group_is_a_retained_constraint_not_an_upgrade_target():
    text = project_text("ruff==0.16.2").replace("dev = [", 'tooling = ["research-repo-tools==0.1.0", "ruff<0.17"]\ndev = [{include-group="tooling"},')
    _, pins = update_python_dev_pins.parse_project(text)
    assert pins == [update_python_dev_pins.DevPin("ruff", "0.16.2")]
    requirements = update_python_dev_pins._resolution_requirements(text, pins)
    assert requirements == ["packaging>=26", "research-repo-tools==0.1.0", "ruff<0.17", "ruff"]
    masked_groups = update_python_dev_pins._masked_manifest(text, frozenset({"ruff"}))["dependency-groups"]
    assert isinstance(masked_groups, dict)
    assert masked_groups["tooling"] == [
        "research-repo-tools==0.1.0",
        "ruff<0.17",
    ]


@pytest.mark.parametrize(
    "groups",
    [
        'dev=[{include-group="absent"}]',
        'dev=[{include-group="tools"}]\ntools=[{include-group="dev"}]',
        'dev=[{include-group="tools", extra=true}]\ntools=[]',
    ],
)
def test_invalid_dependency_group_includes_rejected(groups):
    text = '[project]\nrequires-python=">=3.14"\n[dependency-groups]\n' + groups
    with pytest.raises((ValueError, TypeError)):
        update_python_dev_pins.parse_project(text)


def test_parse_resolution_preserves_direct_order_and_ignores_transitives() -> None:
    pins = [update_python_dev_pins.DevPin("ruff", "0.16.2"), update_python_dev_pins.DevPin("semgrep", "1.172.0")]
    output = "packaging==26.3\nsemgrep==1.174.0\nruff==0.16.4\nmcp==1.29.0\n"
    assert update_python_dev_pins.parse_resolution(output, pins) == [
        update_python_dev_pins.DevPin("ruff", "0.16.4"),
        update_python_dev_pins.DevPin("semgrep", "1.174.0"),
    ]


def test_parse_resolution_rejects_missing_direct_tool() -> None:
    pins = [update_python_dev_pins.DevPin("semgrep", "1.172.0")]
    with pytest.raises(ValueError, match="uv resolver output omitted direct development tool: semgrep"):
        update_python_dev_pins.parse_resolution("mcp==1.29.0\n", pins)


def test_resolve_latest_pins_preserves_retained_constraint_for_managed_distribution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(project_text("ruff==0.16.2", "ruff<0.17"), encoding="utf-8", newline="\n")
    calls: list[tuple[str, list[str], dict[str, object]]] = []

    def fake_run(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert resolution_metadata(args)["dependencies"] == ["packaging>=26", "ruff", "ruff<0.17"]
        calls.append((command, args, kwargs))
        return subprocess.CompletedProcess([command, *args], 0, stdout="ruff==0.16.4\n", stderr="")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", fake_run)
    resolved = update_python_dev_pins.resolve_latest_pins([update_python_dev_pins.DevPin("ruff", "0.16.2")], SpecifierSet(">=3.14"), tmp_path)
    assert resolved == [update_python_dev_pins.DevPin("ruff", "0.16.4")]
    assert not Path(calls[0][1][2]).exists()


def test_update_dev_pins_resolves_then_applies_one_exact_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original = project_text("pytest>=9.1", "ruff==0.16.2", "semgrep==1.172.0", "ty~=0.0.66")
    pyproject.write_text(original, encoding="utf-8", newline="\n")
    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text("version = 1\nruff = 0.16.2\nsemgrep = 1.172.0\n", encoding="utf-8", newline="\n")
    calls: list[tuple[str, list[str], dict[str, object]]] = []

    def fake_run(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, args, kwargs))
        if args[:2] == ["export", "--script"]:
            assert resolution_metadata(args) == {
                "requires-python": ">=3.14",
                "dependencies": ["packaging>=26", "pytest>=9.1", "ruff", "semgrep", "ty~=0.0.66"],
            }
            output = "ruff==0.16.4\nsemgrep==1.174.0\nmcp==1.29.0\n"
        else:
            pyproject.write_text(
                pyproject.read_text(encoding="utf-8").replace("ruff==0.16.2", "ruff==0.16.4").replace("semgrep==1.172.0", "semgrep==1.174.0"),
                encoding="utf-8",
                newline="\n",
            )
            uv_lock.write_text("version = 1\nruff = 0.16.4\nsemgrep = 1.174.0\n", encoding="utf-8", newline="\n")
            output = ""
        return subprocess.CompletedProcess([command, *args], 0, stdout=output, stderr="")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", fake_run)
    changes = update_python_dev_pins.update_dev_pins(pyproject)
    assert changes == {"ruff": ("0.16.2", "0.16.4"), "semgrep": ("1.172.0", "1.174.0")}
    assert calls[0] == (
        "uv",
        [
            "export",
            "--script",
            calls[0][1][2],
            "--format",
            "requirements.txt",
            "--no-header",
            "--no-annotate",
            "--no-hashes",
            "--directory",
            str(tmp_path),
            "--project",
            str(tmp_path),
        ],
        {"cwd": tmp_path, "timeout": update_python_dev_pins.UV_RESOLVE_TIMEOUT_SECONDS},
    )
    assert calls[1] == (
        "uv",
        ["add", "--dev", "--no-sync", "ruff==0.16.4", "semgrep==1.174.0", "--directory", str(tmp_path), "--project", str(tmp_path)],
        {"cwd": tmp_path, "timeout": update_python_dev_pins.UV_ADD_TIMEOUT_SECONDS},
    )
    assert uv_lock.read_text(encoding="utf-8") == "version = 1\nruff = 0.16.4\nsemgrep = 1.174.0\n"


def test_update_dev_pins_rolls_back_collateral_manifest_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original = project_text("pytest>=9.1", "ruff==0.16.2")
    pyproject.write_text(original, encoding="utf-8", newline="\n")

    def mutate_unmanaged_requirement(command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["export", "--script"]:
            return subprocess.CompletedProcess([command, *args], 0, stdout="ruff==0.16.4\n", stderr="")
        pyproject.write_text(original.replace("ruff==0.16.2", "ruff==0.16.4").replace("pytest>=9.1", "pytest>=9.2"), encoding="utf-8", newline="\n")
        return subprocess.CompletedProcess([command, *args], 0, stdout="", stderr="")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", mutate_unmanaged_requirement)
    with pytest.raises(ValueError, match="changed non-target manifest content"):
        update_python_dev_pins.update_dev_pins(pyproject)
    assert pyproject.read_text(encoding="utf-8") == original


def test_resolver_failure_leaves_manifest_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original = project_text("pytest>=9.1", "ruff==0.16.2")
    pyproject.write_text(original, encoding="utf-8", newline="\n")

    def failed_uv(_command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["uv", *args], stderr="resolver conflict")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", failed_uv)
    with pytest.raises(subprocess.CalledProcessError):
        update_python_dev_pins.update_dev_pins(pyproject)
    assert pyproject.read_text(encoding="utf-8") == original


def test_main_skips_uv_when_group_has_no_exact_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(project_text("pytest>=9.1", "ruff~=0.16"), encoding="utf-8", newline="\n")

    def unexpected_uv(*_args: object, **_kwargs: object) -> None:
        msg = "uv must not run without exact pins"
        raise AssertionError(msg)

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", unexpected_uv)
    assert update_python_dev_pins.main(["--pyproject", str(pyproject)]) == 0
    assert capsys.readouterr().out == "No exact direct Python development-tool pins to update.\n"


def test_main_reports_uv_diagnostics_without_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(project_text("semgrep==1.172.0"), encoding="utf-8", newline="\n")

    def failed_uv(_command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["uv", *args], stderr="resolver conflict")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", failed_uv)
    assert update_python_dev_pins.main(["--pyproject", str(pyproject)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "failed to update Python development-tool pins: resolver conflict\n"


def test_main_uses_stdout_when_uv_stderr_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(project_text("semgrep==1.172.0"), encoding="utf-8", newline="\n")

    def failed_uv(_command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["uv", *args], output="actionable resolver conflict", stderr="")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", failed_uv)
    assert update_python_dev_pins.main(["--pyproject", str(pyproject)]) == 1
    assert capsys.readouterr().err == "failed to update Python development-tool pins: actionable resolver conflict\n"


def test_update_dev_pins_rejects_nonstandard_manifest_before_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "release-tools.toml"
    original = project_text("ruff==0.16.2")
    manifest.write_text(original, encoding="utf-8", newline="\n")

    def unexpected_uv(*_args: object, **_kwargs: object) -> None:
        msg = "uv must not discover a sibling project for a nonstandard manifest"
        raise AssertionError(msg)

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", unexpected_uv)
    with pytest.raises(ValueError, match="conventional pyproject\\.toml"):
        update_python_dev_pins.update_dev_pins(manifest)
    assert manifest.read_text(encoding="utf-8") == original


def test_update_dev_pins_rejects_uv_workspace_member_before_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root_manifest = tmp_path / "pyproject.toml"
    root_manifest.write_text('[tool.uv.workspace]\nmembers = ["member"]\n', encoding="utf-8", newline="\n")
    root_lock = tmp_path / "uv.lock"
    root_lock.write_bytes(b"workspace lock\n")
    member = tmp_path / "member"
    member.mkdir()
    member_manifest = member / "pyproject.toml"
    member_manifest.write_text(project_text("ruff==0.16.2"), encoding="utf-8", newline="\n")

    def unexpected_uv(*_args: object, **_kwargs: object) -> None:
        msg = "uv must not run for a workspace member"
        raise AssertionError(msg)

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", unexpected_uv)
    with pytest.raises(TypeError, match="must not select a uv workspace member"):
        update_python_dev_pins.update_dev_pins(member_manifest)
    assert root_lock.read_bytes() == b"workspace lock\n"
    assert member_manifest.read_text(encoding="utf-8") == project_text("ruff==0.16.2")


def test_main_reports_resolver_timeout_and_leaves_project_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original = project_text("ruff==0.16.2")
    pyproject.write_text(original, encoding="utf-8", newline="\n")

    def timed_out(_command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        if not isinstance(timeout, int | float):
            msg = "expected a finite subprocess timeout"
            raise TypeError(msg)
        raise subprocess.TimeoutExpired(["uv", *args], timeout)

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", timed_out)
    assert update_python_dev_pins.main(["--pyproject", str(pyproject)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "timed out after 300 seconds" in captured.err
    assert pyproject.read_text(encoding="utf-8") == original


def test_main_rolls_back_manifest_and_lock_after_uv_add_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original_manifest = project_text("ruff==0.16.2")
    pyproject.write_text(original_manifest, encoding="utf-8", newline="\n")
    uv_lock = tmp_path / "uv.lock"
    original_lock = b"version = 1\n"
    uv_lock.write_bytes(original_lock)

    def time_out_after_mutation(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["export", "--script"]:
            return subprocess.CompletedProcess([command, *args], 0, stdout="ruff==0.16.4\n", stderr="")
        pyproject.write_text(original_manifest.replace("ruff==0.16.2", "ruff==0.16.4"), encoding="utf-8", newline="\n")
        uv_lock.write_text("partially updated\n", encoding="utf-8", newline="\n")
        timeout = kwargs.get("timeout")
        if not isinstance(timeout, int | float):
            msg = "expected a finite subprocess timeout"
            raise TypeError(msg)
        raise subprocess.TimeoutExpired(["uv", *args], timeout)

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", time_out_after_mutation)
    assert update_python_dev_pins.main(["--pyproject", str(pyproject)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "timed out after 300 seconds" in captured.err
    assert pyproject.read_text(encoding="utf-8") == original_manifest
    assert uv_lock.read_bytes() == original_lock


def test_main_rejects_symlinked_lock_without_mutating_link_or_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original_manifest = project_text("ruff==0.16.2")
    pyproject.write_text(original_manifest, encoding="utf-8", newline="\n")
    lock_target = tmp_path / "shared.lock"
    original_lock = b"version = 1\n"
    lock_target.write_bytes(original_lock)
    uv_lock = tmp_path / "uv.lock"
    uv_lock.symlink_to(lock_target.name)

    def unexpected_uv(*_args: object, **_kwargs: object) -> None:
        msg = "uv must not run with a symlinked lockfile"
        raise AssertionError(msg)

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", unexpected_uv)
    assert update_python_dev_pins.main(["--pyproject", str(pyproject)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "uv.lock must not be a symbolic link" in captured.err
    assert uv_lock.is_symlink()
    assert uv_lock.readlink().as_posix() == lock_target.name
    assert lock_target.read_bytes() == original_lock
    assert pyproject.read_text(encoding="utf-8") == original_manifest


@pytest.mark.parametrize("failed_file", ["pyproject.toml", "uv.lock"])
def test_failed_update_retains_recovery_bytes_after_rollback_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failed_file: str
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(project_text("ruff==0.16.2"), encoding="utf-8", newline="\n")
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"original lock\r\n")
    originals = {path: path.read_bytes() for path in (pyproject, lock)}

    def fail_update(command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["export", "--script"]:
            return subprocess.CompletedProcess([command, *args], 0, stdout="ruff==0.16.4\n", stderr="")
        pyproject.write_text("partial manifest\n", encoding="utf-8", newline="\n")
        lock.write_text("partial lock\n", encoding="utf-8", newline="\n")
        msg = "primary update failure"
        raise OSError(msg)

    original_replace = Path.replace

    def fail_restore(source: Path, target: Path) -> Path:
        if target == tmp_path / failed_file:
            raise PermissionError("rollback failure")
        return original_replace(source, target)

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", fail_update)
    monkeypatch.setattr(Path, "replace", fail_restore)
    assert update_python_dev_pins.main(["--pyproject", str(pyproject)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "primary update failure" in captured.err
    assert "rollback failure" in captured.err
    assert "Traceback" not in captured.err
    retained = set(tmp_path.iterdir()) - originals.keys()
    assert len(retained) == 1
    backup = retained.pop()
    assert backup.read_bytes() == originals[tmp_path / failed_file]
    assert str(backup) in captured.err
    for path, payload in originals.items():
        if path.name != failed_file:
            assert path.read_bytes() == payload


@pytest.mark.parametrize("executable", ["custom-uv", "./tools/uv"])
@pytest.mark.parametrize("explicit_root", [False, True])
def test_update_python_uses_selected_uv_for_resolution_and_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executable: str, explicit_root: bool
) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    manifest = root / "pyproject.toml"
    manifest.write_text(project_text("ruff==0.16.2"), encoding="utf-8", newline="\n")
    settings = (elsewhere if explicit_root else root) / "tools.toml"
    settings.write_text(f'[deps]\nuv="{executable}"\n', encoding="utf-8", newline="\n")
    calls = []

    def run(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["cwd"] == root
        calls.append((command, args[0]))
        if args[0] == "add":
            manifest.write_text(project_text("ruff==0.16.4"), encoding="utf-8", newline="\n")
        return subprocess.CompletedProcess([command, *args], 0, "ruff==0.16.4\n", "")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", run)
    monkeypatch.chdir(elsewhere)
    args = ["--config", str(settings)] + (["--root", str(root)] if explicit_root else [])
    assert cli.main([*args, "deps", "update-python"]) == 0
    selected = str(root / "tools/uv") if executable.startswith(".") else executable
    assert calls == [(selected, "export"), (selected, "add")]
    assert manifest.read_text(encoding="utf-8") == project_text("ruff==0.16.4")


def test_parse_project_leaves_compound_wildcard_and_marked_requirements_unmanaged() -> None:
    _python_version, pins = update_python_dev_pins.parse_project(
        project_text("ruff==0.16.2,!=0.16.3", "semgrep==1.172.*", "ty==0.0.66; python_version >= '3.14'")
    )
    assert pins == []


def test_resolve_latest_pins_keeps_ranged_constraints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(project_text("pytest>=9.1", "ruff==0.16.2", "ruff<0.17"), encoding="utf-8", newline="\n")
    calls: list[tuple[str, list[str], dict[str, object]]] = []

    def fake_run(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert resolution_metadata(args)["dependencies"] == ["packaging>=26", "pytest>=9.1", "ruff", "ruff<0.17"]
        calls.append((command, args, kwargs))
        return subprocess.CompletedProcess([command, *args], 0, stdout="ruff==0.16.4\n", stderr="")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", fake_run)
    resolved = update_python_dev_pins.resolve_latest_pins([update_python_dev_pins.DevPin("ruff", "0.16.2")], SpecifierSet(">=3.14"), tmp_path)
    assert resolved == [update_python_dev_pins.DevPin("ruff", "0.16.4")]
    assert len(calls) == 1
    assert calls[0][2] == {"cwd": tmp_path, "timeout": update_python_dev_pins.UV_RESOLVE_TIMEOUT_SECONDS}


def test_resolution_retains_project_and_compound_constraints_on_managed_distributions() -> None:
    manifest = project_text("packaging==26.2", "packaging<27", "packaging!=26.4; python_version >= '3.14'", "ruff==0.16.2,!=0.16.3")
    _python, pins = update_python_dev_pins.parse_project(manifest)
    assert update_python_dev_pins._resolution_requirements(manifest, pins) == [
        "packaging>=26",
        "packaging",
        "packaging<27",
        "packaging!=26.4; python_version >= '3.14'",
        "ruff==0.16.2,!=0.16.3",
    ]


def test_symlinked_lock_is_rejected_before_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(project_text("ruff==0.16.2"), encoding="utf-8", newline="\n")
    retained = tmp_path / "retained.lock"
    retained.write_bytes(b"must not change\n")
    lock = tmp_path / "uv.lock"
    try:
        lock.symlink_to(retained)
    except OSError:
        pytest.skip("symlink creation is unavailable on this runner")
    original = manifest.read_bytes()
    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", lambda *_args, **_kwargs: pytest.fail("uv ran before rejecting a symlink"))
    with pytest.raises(ValueError, match="symbolic link"):
        update_python_dev_pins.update_dev_pins(manifest)
    assert manifest.read_bytes() == original
    assert retained.read_bytes() == b"must not change\n"
    assert lock.is_symlink()


def test_update_dev_pins_resolves_then_applies_one_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original = project_text("pytest>=9.1", "ruff==0.16.2", "semgrep==1.172.0")
    pyproject.write_text(original, encoding="utf-8", newline="\n")
    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text("ruff = 0.16.2\nsemgrep = 1.172.0\n", encoding="utf-8", newline="\n")
    calls: list[tuple[str, list[str]]] = []

    def fake_run(command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, args))
        if args[:2] == ["export", "--script"]:
            output = "ruff==0.16.4\nsemgrep==1.174.0\n"
        else:
            pyproject.write_text(
                original.replace("ruff==0.16.2", "ruff==0.16.4").replace("semgrep==1.172.0", "semgrep==1.174.0"), encoding="utf-8", newline="\n"
            )
            uv_lock.write_text("ruff = 0.16.4\nsemgrep = 1.174.0\n", encoding="utf-8", newline="\n")
            output = ""
        return subprocess.CompletedProcess([command, *args], 0, stdout=output, stderr="")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", fake_run)
    changes = update_python_dev_pins.update_dev_pins(pyproject)
    assert changes == {"ruff": ("0.16.2", "0.16.4"), "semgrep": ("1.172.0", "1.174.0")}
    assert calls[1] == (
        "uv",
        ["add", "--dev", "--no-sync", "ruff==0.16.4", "semgrep==1.174.0", "--directory", str(tmp_path), "--project", str(tmp_path)],
    )
    snapshot = (pyproject.read_bytes(), uv_lock.read_bytes())
    assert update_python_dev_pins.update_dev_pins(pyproject) == {}
    assert len(calls) == 3
    assert calls[-1][1][:2] == ["export", "--script"]
    assert (pyproject.read_bytes(), uv_lock.read_bytes()) == snapshot


def test_update_dev_pins_rolls_back_manifest_and_lock_after_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original_manifest = project_text("ruff==0.16.2")
    pyproject.write_text(original_manifest, encoding="utf-8", newline="\n")
    uv_lock = tmp_path / "uv.lock"
    original_lock = b"version = 1\n"
    uv_lock.write_bytes(original_lock)

    def time_out_after_mutation(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["export", "--script"]:
            return subprocess.CompletedProcess([command, *args], 0, stdout="ruff==0.16.4\n", stderr="")
        pyproject.write_text(original_manifest.replace("ruff==0.16.2", "ruff==0.16.4"), encoding="utf-8", newline="\n")
        uv_lock.write_text("partially updated\n", encoding="utf-8", newline="\n")
        timeout = kwargs["timeout"]
        assert isinstance(timeout, int | float)
        raise subprocess.TimeoutExpired(["uv", *args], timeout)

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", time_out_after_mutation)
    with pytest.raises(subprocess.TimeoutExpired):
        update_python_dev_pins.update_dev_pins(pyproject)
    assert pyproject.read_text(encoding="utf-8") == original_manifest
    assert uv_lock.read_bytes() == original_lock


def test_update_dev_pins_removes_new_lock_after_failed_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original_manifest = project_text("ruff==0.16.2")
    pyproject.write_text(original_manifest, encoding="utf-8", newline="\n")
    uv_lock = tmp_path / "uv.lock"

    def fail_after_creating_lock(command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["export", "--script"]:
            return subprocess.CompletedProcess([command, *args], 0, stdout="ruff==0.16.4\n", stderr="")
        pyproject.write_text(original_manifest.replace("ruff==0.16.2", "ruff==0.16.4"), encoding="utf-8", newline="\n")
        uv_lock.write_text("partially created\n", encoding="utf-8", newline="\n")
        raise subprocess.CalledProcessError(1, [command, *args], stderr="injected failure")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", fail_after_creating_lock)
    with pytest.raises(subprocess.CalledProcessError, match="returned non-zero exit status 1"):
        update_python_dev_pins.update_dev_pins(pyproject)
    assert pyproject.read_text(encoding="utf-8") == original_manifest
    assert not uv_lock.exists()


def minimal_project_text(*requirements: str) -> str:
    """Return a minimal project with exact development-tool pins."""
    rendered = "\n".join((f'    "{requirement}",' for requirement in requirements))
    return f'[project]\nname = "fixture"\nversion = "0.1.0"\nrequires-python = ">=3.14"\n\n[dependency-groups]\ndev = [\n{rendered}\n]\n'


def test_parse_project_accepts_exact_simple_dev_pins() -> None:
    python_version, pins = update_python_dev_pins.parse_project(minimal_project_text("ruff==0.16.2", "semgrep==1.172.0"))
    assert python_version == SpecifierSet(">=3.14")
    assert pins == [update_python_dev_pins.DevPin("ruff", "0.16.2"), update_python_dev_pins.DevPin("semgrep", "1.172.0")]


def test_parse_project_preserves_non_exact_dev_requirement() -> None:
    _, pins = update_python_dev_pins.parse_project(minimal_project_text("ruff>=0.16"))
    assert pins == []


def test_exact_pin_transaction_without_build_metadata_or_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(minimal_project_text("ruff==0.16.2", "semgrep==1.172.0"), encoding="utf-8", newline="\n")
    calls: list[tuple[str, list[str], dict[str, object]]] = []

    def fake_run(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, args, kwargs))
        output = "ruff==0.16.4\nsemgrep==1.174.0\nmcp==1.29.0\n" if args[:2] == ["export", "--script"] else ""
        if args[0] == "export":
            assert resolution_metadata(args) == {"requires-python": ">=3.14", "dependencies": ["ruff", "semgrep"]}
        if args[0] == "add":
            pyproject.write_text(minimal_project_text("ruff==0.16.4", "semgrep==1.174.0"), encoding="utf-8", newline="\n")
        return subprocess.CompletedProcess([command, *args], 0, stdout=output, stderr="")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", fake_run)
    changes = update_python_dev_pins.update_dev_pins(pyproject)
    assert changes == {"ruff": ("0.16.2", "0.16.4"), "semgrep": ("1.172.0", "1.174.0")}
    assert calls[0][2] == {"cwd": tmp_path, "timeout": 300}
    assert calls[1] == (
        "uv",
        ["add", "--dev", "--no-sync", "ruff==0.16.4", "semgrep==1.174.0", "--directory", str(tmp_path), "--project", str(tmp_path)],
        {"cwd": tmp_path, "timeout": 300},
    )


@pytest.mark.parametrize("requires", [">=3.14.1", ">=3.14,<3.15", ">=3.14,!=3.14.1", "~=3.14.1", "==3.14.*"])
def test_resolver_receives_complete_python_constraints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requires: str) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(project_text("ruff==0.16.2").replace(">=3.14", requires), encoding="utf-8", newline="\n")
    constraints, pins = update_python_dev_pins.parse_project(manifest.read_text())
    sources: list[Path] = []

    def run(command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert resolution_metadata(args)["requires-python"] == str(SpecifierSet(requires))
        sources.append(Path(args[2]))
        return subprocess.CompletedProcess([command, *args], 0, "ruff==0.16.4\n", "")

    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", run)
    assert update_python_dev_pins.resolve_latest_pins(pins, constraints, tmp_path) == [update_python_dev_pins.DevPin("ruff", "0.16.4")]
    assert sources and all(not path.exists() for path in sources)


@pytest.mark.parametrize("requires", ["42", '"invalid"', '">=3.15,<3.14"'])
def test_invalid_python_constraints_fail_before_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requires: str) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(project_text("ruff==0.16.2").replace('">=3.14"', requires), encoding="utf-8", newline="\n")
    original = manifest.read_bytes()
    monkeypatch.setattr(update_python_dev_pins, "run_safe_command", lambda *_args, **_kwargs: pytest.fail("uv ran with invalid Python constraints"))

    assert cli.main(["--root", str(tmp_path), "deps", "update-python"]) == 1
    assert manifest.read_bytes() == original
    assert not manifest.with_name("uv.lock").exists()
