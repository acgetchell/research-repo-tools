"""Reject stale or incomplete publication inputs before installation begins."""

import importlib.util
import os
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def install_checker():
    spec = importlib.util.spec_from_file_location("check_install", ROOT / "scripts/check_install.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("utf8_setting", ["0", "1"])
def test_isolated_consumers_use_utf8_transport(tmp_path: Path, install_checker, utf8_setting: str) -> None:
    env = {
        **os.environ,
        "PYTHONHOME": str(tmp_path / "absent interpreter"),
        "PYTHONPATH": str(tmp_path),
        "PYTHONUTF8": utf8_setting,
        "PYTHONIOENCODING": "cp1252:strict",
        "PYTHONDONTWRITEBYTECODE": "0",
    }
    child = (
        "import sys\n"
        "assert sys.flags.isolated == 1\n"
        "assert sys.flags.utf8_mode == 1\n"
        "assert sys.dont_write_bytecode\n"
        "assert sys.stdout.encoding == sys.stderr.encoding == 'utf-8'\n"
        "print('分析 café')\n"
        "print('分析 café', file=sys.stderr)\n"
    )
    assert install_checker.run_isolated(Path(sys.executable), ["-c", child], cwd=tmp_path, env=env) == "分析 café\n"


def test_consumer_suite_outlasting_command_budget_completes_with_live_output(tmp_path: Path, install_checker, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Model the healthy 328-second Windows suite without waiting five minutes."""
    command = [str(sys.executable), "-c", "pass"]
    suite = tmp_path / "consumer café.py"
    env = {"PYTHONUTF8": "0"}
    calls = []

    def healthy_process(command, **kwargs):
        calls.append((command, kwargs))
        if kwargs["timeout"] < 328:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install_checker.subprocess, "run", healthy_process)
    ticks = iter((0.0, 328.0))
    monkeypatch.setattr(install_checker, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        install_checker.run(command, cwd=tmp_path, env=env)
    assert raised.value.timeout == 300
    install_checker.run_consumer_suite(Path(sys.executable), [str(suite), "TestTransport"], cwd=tmp_path, env=env)
    actual, settings = calls[-1]
    assert actual == [str(sys.executable), "-I", "-B", "-X", "utf8", "-u", str(suite), "TestTransport", "-v"]
    assert settings["timeout"] == 900
    assert settings["capture_output"] is False
    assert settings["check"] is True
    assert settings["cwd"] == tmp_path
    assert settings["env"] == env
    output = capsys.readouterr()
    assert "consumer café.py TestTransport (timeout 900s)" in output.out
    assert "consumer café.py TestTransport (328.00s)" in output.out
    assert output.err == ""


def test_consumer_suite_hang_remains_bounded_and_reports_elapsed_time(tmp_path: Path, install_checker, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def hung_process(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(install_checker.subprocess, "run", hung_process)
    ticks = iter((0.0, 900.0))
    monkeypatch.setattr(install_checker, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        install_checker.run_consumer_suite(Path(sys.executable), [str(tmp_path / "hung.py")], cwd=tmp_path, env={})
    assert raised.value.timeout == 900
    assert "hung.py (900.00s)" in capsys.readouterr().out


def test_consumer_suite_streams_verbose_utf8_stdout_and_stderr(tmp_path: Path, install_checker, capfd) -> None:
    suite = tmp_path / "consumer café.py"
    suite.write_text(
        "import sys, unittest\n"
        "class TestTransport(unittest.TestCase):\n"
        "    def test_utf8(self):\n"
        "        self.assertTrue(sys.flags.isolated)\n"
        "        self.assertTrue(sys.dont_write_bytecode)\n"
        "        self.assertTrue(sys.stdout.write_through)\n"
        "        self.assertEqual(sys.stdout.encoding, 'utf-8')\n"
        "        self.assertEqual(sys.stderr.encoding, 'utf-8')\n"
        "        print('分析 café stdout')\n"
        "        print('分析 café stderr', file=sys.stderr)\n"
        "unittest.main()\n",
        encoding="utf-8",
        newline="\n",
    )
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252:strict", "PYTHONDONTWRITEBYTECODE": "0"}
    install_checker.run_consumer_suite(Path(sys.executable), [str(suite), "TestTransport"], cwd=tmp_path, env=env)
    output = capfd.readouterr()
    assert "RUN:" in output.out and "END:" in output.out
    assert "分析 café stdout" in output.out
    assert "分析 café stderr" in output.err
    assert "test_utf8" in output.err and "Ran 1 test" in output.err and "OK" in output.err
    assert not (tmp_path / "__pycache__").exists()


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
@pytest.mark.parametrize("transport", ["text", "bytes", "empty"])
def test_install_failure_preserves_partial_output_and_original_error(
    install_checker, monkeypatch: pytest.MonkeyPatch, capsys, failure: str, transport: str
) -> None:
    stdout, stderr = {
        "text": ("suite progress café\n", "failing test café\n"),
        "bytes": (b"suite progress caf\xc3\xa9\r\n", b"partial UTF-8 \xe5\x88"),
        "empty": (None, None),
    }[transport]
    error = (
        subprocess.TimeoutExpired(["consumer"], 900, output=stdout, stderr=stderr)
        if failure == "timeout"
        else subprocess.CalledProcessError(23, ["consumer"], output=stdout, stderr=stderr)
    )

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(install_checker, "check", fail)
    monkeypatch.setattr(sys, "argv", ["check_install.py"])
    with pytest.raises(type(error)) as raised:
        install_checker.main()
    assert raised.value is error
    output = capsys.readouterr()
    expected_stdout, expected_stderr = {
        "text": ("suite progress café\n", "failing test café\n"),
        "bytes": ("suite progress café\r\n", "partial UTF-8 �"),
        "empty": ("", ""),
    }[transport]
    assert output.out == expected_stdout
    assert output.err == expected_stderr


@pytest.mark.parametrize("inventory", ["empty", "wheel-only", "sdist-only", "stale-wheel", "stale-sdist"])
def test_install_check_rejects_unexpected_distribution_set(tmp_path: Path, inventory: str) -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    wheel = f"research_repo_tools-{version}-py3-none-any.whl"
    sdist = f"research_repo_tools-{version}.tar.gz"
    names = {
        "empty": [],
        "wheel-only": [wheel],
        "sdist-only": [sdist],
        "stale-wheel": [wheel, sdist, "research_repo_tools-0.0.0-py3-none-any.whl"],
        "stale-sdist": [wheel, sdist, "research_repo_tools-0.0.0.tar.gz"],
    }[inventory]
    for name in names:
        (tmp_path / name).touch()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_install.py"), "--dist", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert f"Expected only {wheel} and {sdist}" in result.stderr
    assert "BadZipFile" not in result.stderr
    assert "PASS:" not in result.stdout


@pytest.fixture
def distribution_inventory(tmp_path: Path) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with zipfile.ZipFile(tmp_path / f"research_repo_tools-{version}-py3-none-any.whl", "w") as archive:
        for name in ("templates/cliff.toml", "toolchain_setup.py", "py.typed", "LICENSE"):
            archive.writestr(f"research_repo_tools/{name}", "")
    with tarfile.open(tmp_path / f"research_repo_tools-{version}.tar.gz", "w:gz") as archive:
        for name in ("tests/changelog/test_contract.py", "LICENSE"):
            archive.addfile(tarfile.TarInfo(f"research_repo_tools-{version}/{name}"))
    return tmp_path


def test_changelog_only_requires_external_generator_before_installing(distribution_inventory: Path, monkeypatch: pytest.MonkeyPatch, install_checker) -> None:
    monkeypatch.setattr(install_checker.shutil, "which", lambda program: "uv" if program == "uv" else None)
    with pytest.raises(RuntimeError, match="git-cliff must be installed"):
        install_checker.check(distribution_inventory, changelog_only=True)


def test_changelog_only_runs_one_installed_generation_contract_per_artifact(
    distribution_inventory: Path, monkeypatch: pytest.MonkeyPatch, install_checker
) -> None:
    monkeypatch.setattr(install_checker.shutil, "which", lambda program: program)
    monkeypatch.setattr(install_checker, "run", lambda *args, **kwargs: "")
    calls = []
    monkeypatch.setattr(install_checker, "run_consumer_suite", lambda python, args, **kwargs: calls.append((python, args)))
    install_checker.check(distribution_inventory, changelog_only=True)
    assert len(calls) == 2
    assert {python.parent.parent.parent.name for python, _ in calls} == {"wheel", "sdist"}
    for _, args in calls:
        assert Path(args[0]).name == "public_publishing_consumer.py"
        assert args[1:] == ["TestPublishingConsumer.test_installed_cli_generation_with_real_external_generator"]


def test_install_check_does_not_forward_external_project_locations(distribution_inventory: Path, monkeypatch: pytest.MonkeyPatch, install_checker) -> None:
    module = install_checker
    tmp_path = distribution_inventory
    # Intercept the first subprocess before it can create an environment or
    # honor inherited project-directory overrides.
    external = tmp_path / "external project"
    external.mkdir()
    sentinel = external / "uv.lock"
    sentinel.write_bytes(b"do not modify\n")
    selectors = ("UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_ENV_FILE")
    for name in selectors:
        monkeypatch.setenv(name, str(external))
    monkeypatch.setenv("UV_HTTP_TIMEOUT", "123")
    monkeypatch.setenv("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS", "1")
    monkeypatch.setattr(module.shutil, "which", lambda _: "uv")

    class CheckedEnvironment(Exception):
        pass

    def intercept(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
        assert command[:2] == ["uv", "venv"]
        assert not cwd.is_relative_to(external)
        assert not set(selectors).intersection(env)
        assert env["UV_HTTP_TIMEOUT"] == "123"
        assert env["RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS"] == "1"
        raise CheckedEnvironment

    monkeypatch.setattr(module, "run", intercept)
    with pytest.raises(CheckedEnvironment):
        module.check(tmp_path)
    assert sentinel.read_bytes() == b"do not modify\n"
