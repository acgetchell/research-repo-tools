"""Synthetic tool state: no live installers, package registries, or Git mutations."""

import io
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from research_repo_tools import cli, config, toolchain, toolchain_bootstrap, toolchain_config
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
        self.python = self.add(directory / executable(Path(), "python"), "Python 3.14.7")

    def add(self, path, output):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic executable; calls are intercepted")
        path.chmod(0o700)
        self.outputs[str(path)] = output
        return path

    def install_manager(self):
        self.add(self.runtime.rustup, f"rustup {RUSTUP_VERSION}")

    def run(self, command, args, **kwargs):
        self.calls.append((command, args, kwargs))
        if command == str(self.runtime.rustup):
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
        if command in self.outputs:
            return subprocess.CompletedProcess([], 0, self.outputs[command], "")
        raise AssertionError(f"unexpected command: {command} {args}")


@pytest.fixture
def runtime(consumer, tmp_path, monkeypatch):
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
    assert installs[0] == ["python", "install", "--no-bin", "--no-registry", "3.14"]


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


def test_run_selects_verified_paths_preserves_arguments_and_exit_status(runtime, monkeypatch):
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
    assert toolchain.run_command(instance, ["--", "git-cliff", "a b", "$(no-shell)"]) == 23
    tool = instance.plan.cargo[1]
    assert calls[0][0] == str(executable(instance.cargo_root(tool) / "bin", "git-cliff"))
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
