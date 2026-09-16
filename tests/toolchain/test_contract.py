"""Synthetic tool state: no live installers, package registries, or Git mutations."""

import io
import json
import os
import shutil
import subprocess
import sys
import venv
from dataclasses import replace
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from research_repo_tools import cli, config, files, process, toolchain, toolchain_bootstrap, toolchain_config
from research_repo_tools.toolchain_config import RUSTUP_VERSION, executable


@pytest.fixture
def consumer(tmp_path):
    root = tmp_path / "consumer with spaces"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.14"\n'
        '[dependency-groups]\ntooling = ["research-repo-tools==0.1.0"]\ndev = [{include-group="tooling"}]\n'
        '[tool.uv]\nrequired-version = "==0.12.15"\n'
        '[tool.research-repo-tools.toolchain.cargo]\ngit-cliff = "2.14.1"\ncargo-nextest = "0.9.100"\n'
    )
    (root / ".python-version").write_text("3.14\n")
    (root / "uv.lock").write_text("version = 1\n")
    (root / "rust-toolchain.toml").write_text(
        '[toolchain]\nchannel="1.98.0"\nprofile="default"\ncomponents=["llvm-tools-preview"]\ntargets=["aarch64-apple-darwin"]\n'
    )
    return root


class FakeTools:
    def __init__(self, runtime, directory):
        self.runtime = runtime
        self.directory = directory
        self.outputs = {}
        self.calls = []
        self.installed_rust = False
        self.fail_package = None
        self.python_available = True
        self.add(directory / executable(Path(), "uv"), "uv 0.12.15")
        self.add(directory / executable(Path(), "just"), f"just {toolchain.version('rust-just')}")
        self.add(directory / executable(Path(), "git"), "git version 2.50.1.windows.1")
        self.python = self.add(executable(directory.parent / "managed python", "python"), "Python 3.14.7")
        self.add(executable(directory, "python"), "Python 3.13.7")

    def add(self, path, output):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic executable; calls are intercepted")
        path.chmod(0o700)
        self.outputs[path] = output
        return path

    def install_manager(self):
        self.add(self.runtime.rustup, f"rustup {RUSTUP_VERSION}")

    def run(self, command, args, **kwargs):
        self.calls.append((command, args, kwargs))
        if Path(command) == self.runtime.rustup:
            if args[:2] == ["toolchain", "install"]:
                self.installed_rust = True
                self.add(executable(self.directory, "selected rustc"), "rustc 1.98.0 (abc 2026-08-01)")
                self.add(executable(self.directory, "selected cargo"), "cargo 1.98.0 (abc 2026-08-01)")
                return subprocess.CompletedProcess([command, *args], 0, "", "")
            if args[0] == "which":
                if not self.installed_rust:
                    raise subprocess.CalledProcessError(1, [command, *args], stderr="toolchain is not installed")
                return subprocess.CompletedProcess([], 0, str(executable(self.directory, f"selected {args[-1]}")), "")
            if args[:2] == ["component", "list"]:
                output = f"clippy-{self.runtime.host}\nrustfmt-{self.runtime.host}\nrust-docs-{self.runtime.host}\nllvm-tools-{self.runtime.host}\n"
                return subprocess.CompletedProcess([], 0, output, "")
            if args[:2] == ["target", "list"]:
                return subprocess.CompletedProcess([], 0, "aarch64-apple-darwin\n", "")
            if args[0] == "run" and "install" in args:
                package = args[-1]
                if package == self.fail_package:
                    raise subprocess.CalledProcessError(17, [command, *args], stderr="registry unavailable")
                tool = next(tool for tool in self.runtime.plan.cargo if tool.package == package)
                self.add(executable(self.runtime.cargo_root(tool) / "bin", tool.binary), f"{tool.binary} {tool.version}")
                return subprocess.CompletedProcess([], 0, "", "")
        if args[:2] == ["python", "find"]:
            assert "--no-python-downloads" in args and "--managed-python" in args and "--system" in args
            if not self.python_available:
                raise subprocess.CalledProcessError(1, [command, *args], stderr="managed Python is missing")
            return subprocess.CompletedProcess([], 0, str(self.python), "")
        if args[:2] == ["python", "install"]:
            self.python_available = True
            return subprocess.CompletedProcess([], 0, "", "")
        if Path(command) in self.outputs:
            return subprocess.CompletedProcess([], 0, self.outputs[Path(command)], "")
        raise AssertionError(f"unexpected command: {command} {args}")


@pytest.fixture
def runtime(consumer, tmp_path, monkeypatch):
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    monkeypatch.setenv("RESEARCH_REPO_TOOLS_HOME", str(tmp_path / "managed tools"))
    monkeypatch.setenv("PATH", str(tmp_path / "executables"))
    monkeypatch.setattr(toolchain_config, "host_target", lambda: "aarch64-apple-darwin")
    monkeypatch.setattr(toolchain, "host_target", lambda: "aarch64-apple-darwin")
    runtime = toolchain.Runtime(toolchain_config.load(config.load(root=consumer)))
    fake = FakeTools(runtime, tmp_path / "executables")
    monkeypatch.setattr(toolchain, "run_safe_command", fake.run)
    monkeypatch.setattr(runtime, "_install_rustup", fake.install_manager)
    return runtime, fake


def test_sync_then_repeat_is_noop_and_check_is_read_only(runtime, tmp_path):
    instance, fake = runtime
    before = sorted(tmp_path.rglob("*"))
    statuses = instance.inspect()
    assert any(not status.ok for status in statuses)
    assert sorted(tmp_path.rglob("*")) == before
    assert not any("install" in args for _, args, _ in fake.calls)
    instance.sync()
    assert all(status.ok for status in instance.inspect())
    installs = [args for _, args, _ in fake.calls if "install" in args]
    assert installs[0] == [
        "toolchain",
        "install",
        "1.98.0",
        "--profile",
        "default",
        "--no-self-update",
        "--component",
        "clippy,llvm-tools-preview,rust-docs,rustfmt",
        "--target",
        "aarch64-apple-darwin",
    ]
    assert len(installs) == 3
    assert all("--locked" in args and "--force" in args for args in installs[1:])
    assert any("=2.14.1" in args for args in installs)
    fake.calls.clear()
    instance.sync()
    assert not any("install" in args for _, args, _ in fake.calls)
    assert (instance.plan.root / "uv.lock").read_text() == "version = 1\n"


def test_missing_python_installed_before_rust(runtime):
    instance, fake = runtime
    fake.python_available = False
    instance.sync()
    installs = [args for _, args, _ in fake.calls if "install" in args]
    assert installs[0] == ["python", "install", "--no-bin", "--no-registry", "==3.14.*,>=3.14"]


def test_failure_retains_completed_tools_and_rerun_recovers(runtime):
    instance, fake = runtime
    fake.fail_package = "git-cliff"
    with pytest.raises(RuntimeError, match="registry unavailable"):
        instance.sync()
    assert instance.cargo_status(instance.plan.cargo[0]).ok
    assert not instance.cargo_status(instance.plan.cargo[1]).ok
    fake.fail_package = None
    fake.calls.clear()
    instance.sync()
    installs = [args for _, args, _ in fake.calls if "install" in args]
    assert len(installs) == 1 and installs[0][-1] == "git-cliff"


def test_distinct_versions_do_not_overwrite_each_other(runtime):
    instance, _ = runtime
    tool = instance.plan.cargo[0]
    changed = replace(tool, version="0.9.101")
    assert instance.cargo_root(tool) != instance.cargo_root(changed)
    other = toolchain.Runtime(replace(instance.plan, rust=replace(instance.plan.rust, channel="1.98.1")))
    assert instance.cargo_root(tool) != other.cargo_root(tool)


@pytest.mark.parametrize("command_name", ["git-cliff", "python"])
def test_run_selects_verified_paths_preserves_arguments_and_exit_status(runtime, monkeypatch, command_name):
    instance, fake = runtime
    instance.sync()
    real_fake = fake.run
    calls = []

    def execute(command, args, **kwargs):
        if args == ["a b", "$(no-shell)"]:
            calls.append((command, args, kwargs))
            return subprocess.CompletedProcess([], 23)
        return real_fake(command, args, **kwargs)

    monkeypatch.setattr(toolchain, "run_safe_command", execute)
    assert toolchain.run_command(instance, ["--", command_name, "a b", "$(no-shell)"]) == 23
    tool = instance.plan.cargo[1]
    expected = fake.python if command_name == "python" else executable(instance.cargo_root(tool) / "bin", "git-cliff")
    assert Path(calls[0][0]) == expected
    selected_python = shutil.which("python", path=calls[0][2]["env"]["PATH"])
    assert selected_python is not None and Path(selected_python) == fake.python
    assert calls[0][2]["env"]["RUSTUP_TOOLCHAIN"] == "1.98.0"
    assert calls[0][2]["env"]["CARGO_HOME"] != str(Path.home() / ".cargo")


def test_run_refuses_incomplete_state_without_installing(runtime):
    instance, fake = runtime
    with pytest.raises(ValueError, match="incomplete"):
        toolchain.run_command(instance, ["cargo", "build"])
    assert not any("install" in args for _, args, _ in fake.calls)


def test_invalid_private_uv_is_not_shadowed_by_good_global_uv(runtime):
    instance, fake = runtime
    fake.add(instance.uv, "uv 0.12.14")
    assert not instance.uv_status().ok


@pytest.mark.parametrize(
    "package,banner,arguments",
    [
        ("cargo-edit", "cargo-edit-upgrade 0.13.13", ["upgrade", "--version"]),
        ("typos-cli", "typos-cli 1.50.2", ["--version"]),
        ("taplo-cli", "taplo 0.10.0", ["--version"]),
        ("cargo-nextest", "cargo-nextest 0.9.144 (9718c77af 2026-09-10)\nrelease: 0.9.144", ["nextest", "--version"]),
        ("cargo-llvm-cov", "cargo-llvm-cov 0.9.1", ["llvm-cov", "--version"]),
    ],
)
def test_cargo_package_and_executable_version_contracts(runtime, package, banner, arguments):
    instance, fake = runtime
    binary, args = toolchain_config.CARGO_TOOLS[package]
    tool = toolchain_config.CargoTool(package, banner.split()[1], binary, args)
    fake.add(executable(instance.cargo_root(tool) / "bin", binary), banner)
    assert instance.cargo_status(tool).ok
    assert fake.calls[-1][1] == arguments


def test_wrong_version_after_successful_install_is_failure(runtime, monkeypatch):
    instance, fake = runtime
    original = fake.run

    def lie(command, args, **kwargs):
        result = original(command, args, **kwargs)
        if args and args[-1] == "git-cliff" and "install" in args:
            tool = instance.plan.cargo[1]
            fake.add(executable(instance.cargo_root(tool) / "bin", tool.binary), "git-cliff 2.14.0")
        return result

    monkeypatch.setattr(toolchain, "run_safe_command", lie)
    with pytest.raises(RuntimeError, match="failed verification"):
        instance.sync()


def test_check_json_reports_missing_tools_without_installing(runtime, monkeypatch, capsys):
    instance, fake = runtime
    monkeypatch.setattr(toolchain, "Runtime", lambda plan: instance)
    assert cli.main(["--root", str(instance.plan.root), "toolchain", "check", "--json"]) == 1
    statuses = json.loads(capsys.readouterr().out)
    assert next(item for item in statuses if item["name"] == "Python")["ok"]
    assert not next(item for item in statuses if item["name"] == "rustup")["ok"]
    assert not any("install" in args for _, args, _ in fake.calls)


@pytest.mark.parametrize("complete", [False, True])
def test_sync_dry_run_only_warns_when_tools_need_repair(runtime, monkeypatch, capsys, complete):
    instance, fake = runtime
    if complete:
        instance.sync()
    fake.calls.clear()
    capsys.readouterr()
    monkeypatch.setattr(toolchain, "Runtime", lambda plan: instance)
    assert cli.main(["--root", str(instance.plan.root), "toolchain", "sync", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert ("Dry run: FAIL entries need installation or prerequisite repair" in output) is not complete
    assert ("FAIL " in output) is not complete
    assert not any("install" in args for _, args, _ in fake.calls)


@pytest.mark.parametrize("replacement", ['required-version = ">=0.12.15"', 'required-version = "==0.12.15;echo unsafe"'])
def test_invalid_uv_pins_fail_before_operations(consumer, replacement):
    file = consumer / "pyproject.toml"
    file.write_text(file.read_text().replace('required-version = "==0.12.15"', replacement))
    with pytest.raises(ValueError, match="pin exactly"):
        toolchain_config.load(config.load(root=consumer))


def test_unsupported_uv_version_rejected_before_installation(consumer):
    file = consumer / "pyproject.toml"
    file.write_text(file.read_text().replace("==0.12.15", "==0.12.9"))
    with pytest.raises(ValueError, match="0.12.10 or newer"):
        toolchain_config.load(config.load(root=consumer))


@pytest.mark.parametrize(
    "contents",
    [
        '[toolchain]\nchannel="stable"',
        '[toolchain]\nchannel="1.98.0"\npath="/somewhere"',
        '[toolchain]\nchannel="1.98.0"\ncomponents=["--help"]',
        '[toolchain]\nchannel="1.98.0"\ncomponents=["clippy","clippy"]',
        '[toolchain]\nchannel="1.98.0"\nprofile=[]',
    ],
)
def test_invalid_rust_declarations_rejected(consumer, contents):
    (consumer / "rust-toolchain.toml").write_text(contents)
    with pytest.raises(ValueError):
        toolchain_config.load(config.load(root=consumer))


@pytest.mark.parametrize("pin", ['git-cliff = ">=2.14.1"', 'unknown = "1.0.0"', 'just = "1.58.0"'])
def test_invalid_cargo_declarations_rejected(consumer, pin):
    file = consumer / "pyproject.toml"
    file.write_text(file.read_text().replace('git-cliff = "2.14.1"', pin))
    with pytest.raises(ValueError):
        toolchain_config.load(config.load(root=consumer))


def test_python_only_plan_and_missing_rust_prerequisite(consumer):
    (consumer / "rust-toolchain.toml").unlink()
    with pytest.raises(ValueError, match="require a pinned"):
        toolchain_config.load(config.load(root=consumer))
    settings = config.load(root=consumer)
    plan = toolchain_config.load(replace(settings, sections={}))
    assert plan.rust is None and not plan.cargo


def test_bootstrap_generation_check_and_explicit_refresh(consumer):
    plan = toolchain_config.load(config.load(root=consumer))
    toolchain_bootstrap.generate(plan)
    toolchain_bootstrap.generate(plan, check=True)
    assert "__UV_VERSION__" not in (consumer / "bootstrap.sh").read_text()
    updated = replace(plan, uv="0.12.16")
    with pytest.raises(ValueError, match="stale"):
        toolchain_bootstrap.generate(updated, check=True)
    with pytest.raises(ValueError, match="exists"):
        toolchain_bootstrap.generate(updated)
    toolchain_bootstrap.generate(updated, force=True)
    toolchain_bootstrap.generate(updated, check=True)


def test_bootstrap_refuses_overwriting_any_file_before_writing_the_pair(consumer):
    (consumer / "bootstrap.ps1").write_text("maintainer content")
    plan = toolchain_config.load(config.load(root=consumer))
    with pytest.raises(ValueError, match="exists"):
        toolchain_bootstrap.generate(plan)
    assert not (consumer / "bootstrap.sh").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_bootstrap_rolls_back_both_launchers_on_publication_failure(consumer, monkeypatch, existing):
    plan = toolchain_config.load(config.load(root=consumer))
    if existing:
        toolchain_bootstrap.generate(plan)
    before = {path: path.read_bytes() for path in consumer.iterdir() if path.is_file()}
    original_replace = files._replace_path

    def fail_second(source, destination):
        if source.suffix == ".tmp" and destination.name == "bootstrap.ps1":
            raise PermissionError("second launcher is locked")
        original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(files, "_replace_path", fail_second)
        with pytest.raises(PermissionError, match="second launcher is locked"):
            toolchain_bootstrap.generate(replace(plan, uv="0.12.16"), force=True)
    assert {path: path.read_bytes() for path in consumer.iterdir() if path.is_file()} == before
    toolchain_bootstrap.generate(replace(plan, uv="0.12.16"), force=True)
    toolchain_bootstrap.generate(replace(plan, uv="0.12.16"), check=True)


def test_bootstrap_rejects_directory_before_replacing_either_launcher(consumer):
    plan = toolchain_config.load(config.load(root=consumer))
    shell = consumer / "bootstrap.sh"
    shell.write_bytes(b"previous launcher\n")
    (consumer / "bootstrap.ps1").mkdir()
    with pytest.raises(IsADirectoryError):
        toolchain_bootstrap.generate(plan, force=True)
    assert shell.read_bytes() == b"previous launcher\n"
    assert not list(consumer.glob(".*.tmp"))
    assert not list(consumer.glob(".*.bak"))


@pytest.mark.parametrize("environment_name", [None, "custom environment", "absolute"])
def test_run_python_preserves_consumer_dependencies(runtime, tmp_path, monkeypatch, environment_name):
    instance, fake = runtime
    instance.plan = replace(instance.plan, rust=None, cargo=(), python=f"{sys.version_info.major}.{sys.version_info.minor}")
    environment = instance.plan.root / (environment_name or ".venv")
    if environment_name == "absolute":
        environment = tmp_path / "external environment"
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(environment))
    elif environment_name:
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", environment_name)
    else:
        monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    managed = tmp_path / "standalone interpreter"
    for directory in (environment, managed):
        venv.EnvBuilder(with_pip=False).create(directory)
    scripts = "Scripts" if os.name == "nt" else "bin"
    python = executable(environment / scripts, "python")
    fake.python = executable(managed / scripts, "python")
    real_run = process.run_safe_command

    def execute(command, args, **kwargs):
        if Path(command) in {python, fake.python}:
            return real_run(command, args, **kwargs)
        return fake.run(command, args, **kwargs)

    monkeypatch.setattr(toolchain, "run_safe_command", execute)
    monkeypatch.setenv("PATH", os.pathsep.join([str(python.parent), str(fake.directory)]))
    monkeypatch.setenv("VIRTUAL_ENV", str(environment))
    site = Path(real_run(str(python), ["-c", 'import sysconfig; print(sysconfig.get_path("purelib"))']).stdout.strip())
    (site / "consumer_dependency.py").write_text('VALUE = "installed only in the consumer"\n')
    output = tmp_path / "result.txt"
    code = "import sys, consumer_dependency; from pathlib import Path; Path(sys.argv[1]).write_text(consumer_dependency.VALUE)"
    assert toolchain.run_command(instance, ["python", "-c", code, str(output)]) == 0
    assert output.read_text() == "installed only in the consumer"
    assert Path(instance.python_status(instance.uv_status()).path) == python


@pytest.mark.parametrize(
    "pin,requires,actual,ok",
    [
        ("3.14", ">=3.14.1", "3.14.7", True),
        ("3.14", ">=3.14.1", "3.14.0", False),
        ("3.14", ">=3.14.1", "3.15.0", False),
        ("3.14", ">=3.14,<3.14.7", "3.14.7", False),
        ("3.14", ">=3.14,!=3.14.7", "3.14.7", False),
        ("3.14.7", ">=3.14.1", "3.14.7", True),
        ("3.14.7", ">=3.14.1", "3.14.8", False),
    ],
)
def test_python_selection_enforces_resolved_patch_constraints(runtime, pin, requires, actual, ok):
    instance, fake = runtime
    manifest = instance.plan.root / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('requires-python = ">=3.14"', f'requires-python = "{requires}"'))
    (instance.plan.root / ".python-version").write_text(pin)
    instance.plan = toolchain_config.load(config.load(root=instance.plan.root))
    fake.outputs[fake.python] = f"Python {actual}"
    status = instance.python_status(instance.uv_status())
    assert status.ok is ok
    assert status.actual == actual
    lookup = next(args for _, args, _ in fake.calls if args[:2] == ["python", "find"])
    assert (actual in SpecifierSet(lookup[-1])) is ok


@pytest.mark.parametrize("actual", ["3.13.7", "3.14.0"])
def test_incompatible_consumer_python_falls_back_to_verified_managed_python(runtime, actual):
    instance, fake = runtime
    instance.plan = replace(instance.plan, requires_python=">=3.14.1")
    environment = instance.plan.root / ".venv"
    python = executable(environment / ("Scripts" if os.name == "nt" else "bin"), "python")
    fake.add(python, f"Python {actual}")
    (environment / "pyvenv.cfg").write_text("synthetic virtual environment\n")
    status = instance.python_status(instance.uv_status())
    assert status.ok and Path(status.path) == fake.python
    selected = shutil.which("python", path=instance.environment()["PATH"])
    assert selected is not None and Path(selected) == fake.python


def test_missing_python_install_respects_project_patch_constraints(runtime):
    instance, fake = runtime
    instance.plan = replace(instance.plan, rust=None, cargo=(), requires_python=">=3.14.1,<3.14.9")
    fake.python_available = False
    instance.sync()
    installs = [args for _, args, _ in fake.calls if "install" in args]
    assert len(installs) == 1
    assert installs[0][:4] == ["python", "install", "--no-bin", "--no-registry"]
    assert SpecifierSet(installs[0][-1]) == SpecifierSet("==3.14.*,>=3.14.1,<3.14.9")
    assert instance.python_status(instance.uv_status()).ok


def test_incompatible_exact_python_pin_is_rejected(consumer):
    manifest = consumer / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('requires-python = ">=3.14"', 'requires-python = ">=3.14.1"'))
    (consumer / ".python-version").write_text("3.14.0")
    with pytest.raises(ValueError, match="must satisfy"):
        toolchain_config.load(config.load(root=consumer))


def test_checksum_mismatch_prevents_installer_execution(runtime, monkeypatch):
    instance, fake = runtime
    monkeypatch.setattr(toolchain.urllib.request, "urlopen", lambda url, **kwargs: io.BytesIO(b"0" * 64 if url.endswith(".sha256") else b"bad installer"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        toolchain.Runtime._install_rustup(instance)
    assert not fake.calls


@pytest.mark.parametrize(
    "system,machine,libc,target",
    [
        ("Darwin", "arm64", "", "aarch64-apple-darwin"),
        ("Darwin", "x86_64", "", "x86_64-apple-darwin"),
        ("Linux", "x86_64", "glibc", "x86_64-unknown-linux-gnu"),
        ("Linux", "aarch64", "glibc", "aarch64-unknown-linux-gnu"),
        ("Windows", "AMD64", "", "x86_64-pc-windows-msvc"),
        ("Windows", "ARM64", "", "aarch64-pc-windows-msvc"),
    ],
)
def test_supported_installer_targets(monkeypatch, system, machine, libc, target):
    monkeypatch.setattr(toolchain_config.platform, "system", lambda: system)
    monkeypatch.setattr(toolchain_config.platform, "machine", lambda: machine)
    monkeypatch.setattr(toolchain_config.platform, "libc_ver", lambda: (libc, ""))
    assert toolchain_config.host_target() == target


def test_unsupported_platform_fails_before_installation(monkeypatch):
    monkeypatch.setattr(toolchain_config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(toolchain_config.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(toolchain_config.platform, "libc_ver", lambda: ("musl", ""))
    with pytest.raises(ValueError, match="glibc"):
        toolchain_config.host_target()
