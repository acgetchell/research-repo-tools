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

from research_repo_tools import cli, config, process, toolchain, toolchain_config
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
        self.add(executable(directory, "sh"), "")
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
                self.add(executable(self.runtime.cargo_root(tool) / "bin", tool.binary), f"{tool.version_label} {tool.version}".strip())
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


def test_cargo_upgrade_requires_declared_cargo_edit_even_when_on_path(runtime):
    instance, fake = runtime
    instance.sync()
    fake.add(executable(instance.rustup.parent, "cargo"), "cargo 1.98.0")
    fake.add(executable(fake.directory, "cargo-upgrade"), "cargo-edit-upgrade 0.13.13")
    fake.calls.clear()
    with pytest.raises(ValueError, match="cargo upgrade requires.*cargo-edit.*just setup"):
        toolchain.run_command(instance, ["--", "cargo", "upgrade", "--incompatible", "allow"])
    assert not fake.calls


def test_cargo_upgrade_requires_setup_and_selects_the_verified_tool(runtime, monkeypatch):
    instance, fake = runtime
    instance.sync()
    manifest = instance.plan.root / "pyproject.toml"
    manifest.write_text(manifest.read_text() + 'cargo-edit = "0.13.13"\n')
    instance.plan = toolchain_config.load(config.load(root=instance.plan.root))
    tool = next(tool for tool in instance.plan.cargo if tool.package == "cargo-edit")
    fake.add(executable(instance.rustup.parent, "cargo"), "cargo 1.98.0")
    fake.add(executable(fake.directory, "cargo-upgrade"), "cargo-edit-upgrade 0.13.13")
    fake.calls.clear()
    with pytest.raises(ValueError, match="toolchain is incomplete"):
        toolchain.run_command(instance, ["cargo", "upgrade"])
    assert not any("install" in args or args == ["upgrade"] for _, args, _ in fake.calls)

    instance.sync()
    installs = [args for _, args, _ in fake.calls if "install" in args]
    assert len(installs) == 1 and installs[0][-1] == "cargo-edit"
    assert installs[0][installs[0].index("--version") + 1] == "=0.13.13"
    assert instance.cargo_status(tool).ok
    original = fake.run
    selected = []

    def execute(command, args, **kwargs):
        if args == ["upgrade", "--incompatible", "allow"]:
            selected.append(shutil.which("cargo-upgrade", path=kwargs["env"]["PATH"]))
            return subprocess.CompletedProcess([], 0)
        return original(command, args, **kwargs)

    monkeypatch.setattr(toolchain, "run_safe_command", execute)
    assert toolchain.run_command(instance, ["cargo", "upgrade", "--incompatible", "allow"]) == 0
    assert selected == [str(executable(instance.cargo_root(tool) / "bin", "cargo-upgrade"))]


@pytest.mark.parametrize("available", [False, True])
def test_uv_prerequisite_failure_never_installs_a_replacement(runtime, available):
    instance, fake = runtime
    uv = executable(fake.directory, "uv")
    if available:
        fake.add(uv, "uv 0.12.14")
    else:
        uv.unlink()
    # Even a matching private copy cannot satisfy the external prerequisite.
    fake.add(executable(instance.base / "uv" / instance.plan.uv, "uv"), "uv 0.12.15")
    assert not instance.uv_status().ok
    with pytest.raises(RuntimeError, match="must be installed and available on PATH"):
        instance.sync()
    assert not any("install" in args for _, args, _ in fake.calls)


@pytest.mark.parametrize(
    "package,banner,arguments",
    [
        ("cargo-audit", "cargo-audit 0.22.2", ["--version"]),
        ("cargo-edit", "cargo-edit-upgrade 0.13.13", ["upgrade", "--version"]),
        ("cargo-machete", "0.9.2", ["--version"]),
        ("samply", "samply 0.13.1", ["--version"]),
        ("tectonic", "Tectonic 0.17.0", ["--version"]),
        ("tex-fmt", "tex-fmt 0.5.7", ["--version"]),
        ("typos-cli", "typos-cli 1.50.2", ["--version"]),
        ("taplo-cli", "taplo 0.10.0", ["--version"]),
        ("cargo-nextest", "cargo-nextest 0.9.144 (9718c77af 2026-09-10)\nrelease: 0.9.144", ["nextest", "--version"]),
        ("cargo-llvm-cov", "cargo-llvm-cov 0.9.1", ["llvm-cov", "--version"]),
    ],
)
def test_cargo_package_and_executable_version_contracts(runtime, monkeypatch, package, banner, arguments):
    instance, fake = runtime
    monkeypatch.setenv("CARGO", "/inherited/cargo")
    binary, args = toolchain_config.CARGO_TOOLS[package]
    version = banner.split()[0 if package == "cargo-machete" else 1]
    tool = toolchain_config.CargoTool(package, version, binary, args)
    fake.add(executable(instance.cargo_root(tool) / "bin", binary), banner)
    assert instance.cargo_status(tool).ok
    assert fake.calls[-1][1] == arguments
    assert "CARGO" not in fake.calls[-1][2]["env"]
    assert instance.cargo_status(tool).name
    fake.add(executable(instance.cargo_root(tool) / "bin", binary), banner.replace(version, "99.0.0"))
    assert not instance.cargo_status(tool).ok


def test_complete_catalog_installs_and_verifies_exact_declared_tools(runtime):
    instance, fake = runtime
    manifest = instance.plan.root / "pyproject.toml"
    source = manifest.read_text().split("[tool.research-repo-tools.toolchain.cargo]")[0]
    source += "[tool.research-repo-tools.toolchain.cargo]\n" + "".join(f'{package} = "1.2.3"\n' for package in toolchain_config.CARGO_TOOLS)
    manifest.write_text(source)
    instance.plan = toolchain_config.load(config.load(root=instance.plan.root))
    instance.sync()
    installs = [args for _, args, _ in fake.calls if "install" in args and "cargo" in args]
    assert {args[-1] for args in installs} == {
        "cargo-audit",
        "cargo-edit",
        "cargo-llvm-cov",
        "cargo-machete",
        "cargo-nextest",
        "dprint",
        "git-cliff",
        "rumdl",
        "samply",
        "taplo-cli",
        "tectonic",
        "tex-fmt",
        "typos-cli",
        "zizmor",
    }
    assert all("--locked" in args and args[args.index("--version") + 1] == "=1.2.3" for args in installs)
    assert all(instance.cargo_status(tool).ok for tool in instance.plan.cargo)
    assert manifest.read_text() == source


def test_tectonic_native_failure_keeps_pins_and_passes_through_discovery_environment(runtime, monkeypatch):
    instance, fake = runtime
    binary, args = toolchain_config.CARGO_TOOLS["tectonic"]
    tool = toolchain_config.CargoTool("tectonic", "0.17.0", binary, args)
    instance.plan = replace(instance.plan, cargo=(*instance.plan.cargo, tool))
    before = instance.plan.root.joinpath("pyproject.toml").read_bytes()
    monkeypatch.setenv("PKG_CONFIG_PATH", "/consumer/native/pkgconfig")
    monkeypatch.setenv("TECTONIC_DEP_BACKEND", "vcpkg")
    monkeypatch.setenv("VCPKG_ROOT", "/consumer/vcpkg")
    fake.fail_package = "tectonic"
    with pytest.raises(RuntimeError, match="Tectonic also requires native.*pkg-config.*vcpkg"):
        instance.sync()
    install = next(kwargs for _, args, kwargs in fake.calls if "install" in args and args[-1] == "tectonic")
    for name in ("PKG_CONFIG_PATH", "TECTONIC_DEP_BACKEND", "VCPKG_ROOT"):
        assert install["env"][name] == os.environ[name]
    assert instance.plan.root.joinpath("pyproject.toml").read_bytes() == before
    assert instance.cargo_status(instance.plan.cargo[0]).ok
    assert not instance.cargo_status(tool).ok


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
    plan = toolchain_config.load(replace(settings, toolchain=config.ToolchainSettings()))
    assert plan.rust is None and not plan.cargo


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


@pytest.mark.parametrize(
    "pin,requires,matching_version",
    [
        ("3.14.0", ">=3.14.1", None),
        ("3.14", ">=3.15", None),
        ("3.15", "<3.15", None),
        ("3.14", "==3.15.*", None),
        ("3.14", "~=3.15.1", None),
        ("3.14", ">=3.14.2,<3.14.2", None),
        ("3.14", ">=3.14,!=3.14.*", None),
        ("3.14", ">=3.14.1,<=3.14.1,!=3.14.1", None),
        ("3.14", ">=3.14.1,<3.15", "3.14.1"),
        ("3.14", ">=3.14.1,!=3.14.1,<3.14.3", "3.14.2"),
        ("3.14", "~=3.14.5", "3.14.5"),
        ("3.14", ">3.14.1000000", "3.14.1000001"),
        ("3.14.7", ">=3.14.1,<3.15", "3.14.7"),
    ],
)
def test_python_pin_requires_nonempty_project_constraint_intersection(consumer, pin, requires, matching_version):
    manifest = consumer / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('requires-python = ">=3.14"', f'requires-python = "{requires}"'))
    (consumer / ".python-version").write_text(pin)
    if matching_version is None:
        with pytest.raises(ValueError, match=r"\.python-version must satisfy project\.requires-python"):
            toolchain_config.load(config.load(root=consumer))
    else:
        plan = toolchain_config.load(config.load(root=consumer))
        assert matching_version in SpecifierSet(plan.python_request)


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


def setup_fake(runtime, monkeypatch, *, just_version=None, failure=None, directory=None):
    """Model uv's user-tool boundary without touching real shell profiles."""
    from research_repo_tools import toolchain_setup

    instance, fake = runtime
    user_bin = instance.plan.root.parent / "user bin"
    original = fake.run

    def run(command, args, **kwargs):
        if args[:1] not in (["tool"], ["sync"]):
            return original(command, args, **kwargs)
        fake.calls.append((command, args, kwargs))
        assert kwargs["cwd"] == instance.plan.root
        if failure == args[:2]:
            raise subprocess.CalledProcessError(1, [command, *args], stderr="synthetic setup failure")
        output = ""
        if args[:2] == ["tool", "install"]:
            fake.add(executable(user_bin, "just"), f"just {just_version or toolchain.version('rust-just')}")
        elif args[:2] == ["tool", "dir"]:
            output = str(user_bin) if directory is None else directory
        elif args[:2] == ["tool", "update-shell"]:
            assert str(user_bin) not in kwargs["env"]["PATH"].split(os.pathsep)
        elif args[0] == "sync":
            assert fake.installed_rust
            assert kwargs["env"]["RUSTUP_TOOLCHAIN"] == "1.98.0"
            selected_python = shutil.which("python", path=kwargs["env"]["PATH"])
            assert selected_python is not None and Path(selected_python) == fake.python
            for tool in instance.plan.cargo:
                selected_tool = shutil.which(tool.binary, path=kwargs["env"]["PATH"])
                assert selected_tool is not None and Path(selected_tool) == executable(instance.cargo_root(tool) / "bin", tool.binary)
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess([command, *args], 0, output, "")

    monkeypatch.setattr(toolchain_setup, "run_safe_command", run)
    return toolchain_setup


def test_setup_installs_user_just_and_tools_before_locked_project_sync(runtime, monkeypatch, capsys):
    instance, fake = runtime
    setup_module = setup_fake(runtime, monkeypatch)
    # A stale system Just must not mask the verified user installation.
    fake.add(executable(fake.directory, "just"), "just 1.0.0")
    fake.add(executable(fake.directory, "git-cliff"), "git-cliff 1.0.0")
    originals = {path: path.read_bytes() for path in instance.plan.root.iterdir()}
    setup_module.setup(instance)
    actions = [args for _, args, _ in fake.calls if "install" in args or args[0] in {"tool", "sync"}]
    assert actions[-4:] == [
        ["tool", "install", "--no-config", "--managed-python", "--python", "==3.14.*,>=3.14", f"rust-just=={toolchain.version('rust-just')}"],
        ["tool", "dir", "--bin", "--no-config"],
        ["tool", "update-shell", "--no-config"],
        ["sync", "--locked", "--managed-python", "--group", "dev"],
    ]
    assert any("cargo" in args and "install" in args for args in actions[:-4])
    assert "Setup complete" in capsys.readouterr().out
    assert {path: path.read_bytes() for path in instance.plan.root.iterdir()} == originals


@pytest.mark.parametrize("failure", [["tool", "install"], ["tool", "update-shell"], ["sync", "--locked"]])
def test_setup_failure_is_reported_without_success(runtime, monkeypatch, capsys, failure):
    instance, fake = runtime
    setup_fake(runtime, monkeypatch, failure=failure)
    monkeypatch.setattr(toolchain, "Runtime", lambda plan: instance)
    assert cli.main(["--root", str(instance.plan.root), "setup"]) == 1
    captured = capsys.readouterr()
    assert "synthetic setup failure" in captured.err
    assert "Setup complete" not in captured.out
    if failure[0] == "tool":
        assert not any(args[0] == "sync" for _, args, _ in fake.calls)


def test_setup_verifies_user_just_instead_of_project_just(runtime, monkeypatch):
    instance, fake = runtime
    setup_module = setup_fake(runtime, monkeypatch, just_version="1.0.0")
    with pytest.raises(RuntimeError, match="Just installation failed verification"):
        setup_module.setup(instance)
    assert not any(args[0] == "sync" or args[:2] == ["tool", "update-shell"] for _, args, _ in fake.calls)


@pytest.mark.parametrize("directory", ["", "relative/path"])
def test_setup_rejects_invalid_user_tool_directory(runtime, monkeypatch, directory):
    instance, fake = runtime
    setup_module = setup_fake(runtime, monkeypatch, directory=directory)
    with pytest.raises(ValueError, match="absolute executable directory"):
        setup_module.setup(instance)
    assert not any(args[0] == "sync" for _, args, _ in fake.calls)


def test_setup_requires_lockfile_before_installing(runtime, monkeypatch):
    instance, fake = runtime
    setup_module = setup_fake(runtime, monkeypatch)
    (instance.plan.root / "uv.lock").unlink()
    with pytest.raises(ValueError, match="requires a committed uv.lock"):
        setup_module.setup(instance)
    assert not fake.calls


@pytest.mark.parametrize("available", [False, True])
def test_setup_requires_working_recipe_shell_before_installing(runtime, monkeypatch, available):
    instance, fake = runtime
    setup_module = setup_fake(runtime, monkeypatch)
    shell = executable(fake.directory, "sh")
    original = fake.run
    if not available:
        shell.unlink()

    def run(command, args, **kwargs):
        if Path(command) == shell:
            raise subprocess.CalledProcessError(2, [command, *args], stderr="unsupported shell options")
        return original(command, args, **kwargs)

    monkeypatch.setattr(toolchain, "run_safe_command", run)
    status = instance.shell_status()
    assert not status.ok
    assert ("unsupported shell options" if available else "missing") in status.actual
    with pytest.raises(RuntimeError, match="working POSIX sh on PATH"):
        setup_module.setup(instance)
    assert not any("install" in args or args[0] in {"tool", "sync"} for _, args, _ in fake.calls)


def test_setup_requires_tooling_group_to_survive_full_sync(runtime, monkeypatch):
    instance, fake = runtime
    setup_module = setup_fake(runtime, monkeypatch)
    manifest = instance.plan.root / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('dev = [{include-group="tooling"}]', "dev = []"))
    with pytest.raises(ValueError, match="dev to include the tooling group"):
        setup_module.setup(instance)
    assert not fake.calls


@pytest.mark.parametrize("tooling", ["42", '["invalid @"]', '[{include-group="missing"}]', '[{include-group="dev"}]', "[{include-group=1}]"])
def test_setup_rejects_malformed_groups_before_installing(runtime, monkeypatch, tooling):
    instance, fake = runtime
    manifest = instance.plan.root / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('["research-repo-tools==0.1.0"]', tooling))
    original = manifest.read_bytes()
    setup_module = setup_fake(runtime, monkeypatch)
    monkeypatch.setattr(instance, "sync", lambda: pytest.fail("must reject before tool synchronization"))
    with pytest.raises((TypeError, ValueError)):
        setup_module.setup(instance)
    assert not fake.calls
    assert manifest.read_bytes() == original


def test_managed_cargo_tools_take_precedence_over_python_environment_binaries(runtime, monkeypatch):
    instance, fake = runtime
    instance.sync()
    fake.add(executable(fake.python.parent, "git-cliff"), "git-cliff 1.0.0")
    calls = []
    original = fake.run

    def run(command, args, **kwargs):
        if args == ["--help"]:
            calls.append(Path(command))
            return subprocess.CompletedProcess([], 0)
        return original(command, args, **kwargs)

    monkeypatch.setattr(toolchain, "run_safe_command", run)
    assert toolchain.run_command(instance, ["git-cliff", "--help"]) == 0
    assert calls == [executable(instance.cargo_root(instance.plan.cargo[1]) / "bin", "git-cliff")]


def test_setup_keeps_dev_when_default_groups_are_disabled(runtime, monkeypatch):
    instance, fake = runtime
    manifest = instance.plan.root / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace("[tool.uv]", "[tool.uv]\ndefault-groups=[]"))
    setup_module = setup_fake(runtime, monkeypatch)
    setup_module.setup(instance)
    sync_arguments = next(args for _, args, _ in fake.calls if args[0] == "sync")
    assert "--group" in sync_arguments and sync_arguments[sync_arguments.index("--group") + 1] == "dev"


@pytest.mark.parametrize("operation", ["inspect", "sync"])
def test_operations_probe_unchanged_uv_and_python_once(runtime, operation):
    instance, fake = runtime
    instance.sync()
    fake.calls.clear()
    getattr(instance, operation)()
    uv = executable(fake.directory, "uv")
    assert sum(Path(command) == uv and args == ["--version"] for command, args, _ in fake.calls) == 1
    assert sum(args[:2] == ["python", "find"] for _, args, _ in fake.calls) == 1
    assert sum(Path(command) == fake.python and args == ["--version"] for command, args, _ in fake.calls) == 1
    assert instance._probes.get() is None


def test_independent_operations_do_not_retain_probe_results(runtime):
    instance, fake = runtime
    instance.inspect()
    # Change the modeled executable without touching its file metadata: a new
    # independent operation must probe again even if all fingerprints match.
    fake.outputs[fake.python] = "Python 3.13.7"
    python = next(status for status in instance.inspect() if status.name == "Python")
    assert not python.ok and python.actual == "3.13.7"


def test_probe_cache_tracks_path_and_selected_executable_changes(runtime, monkeypatch, tmp_path):
    instance, fake = runtime
    with instance._operation():
        assert instance.python_status(instance.uv_status()).actual == "3.14.7"
        fake.outputs[fake.python] = "Python 3.14.8"
        fake.python.write_bytes(b"a replaced interpreter with different metadata")
        assert instance.python_status(instance.uv_status()).actual == "3.14.8"
        other = tmp_path / "other bin"
        fake.add(executable(other, "uv"), "uv 0.12.14")
        monkeypatch.setenv("PATH", str(other) + os.pathsep + os.environ["PATH"])
        assert not instance.uv_status().ok
        assert not instance.python_status(instance.uv_status()).ok


@pytest.mark.parametrize("override", [False, True])
def test_probe_cache_notices_new_project_environment_and_config_edits(runtime, monkeypatch, override):
    instance, fake = runtime
    with instance._operation():
        assert instance.python_status(instance.uv_status()).path == str(fake.python)
        environment = instance.plan.root / ("other environment" if override else ".venv")
        if override:
            monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(environment))
        python = executable(environment / ("Scripts" if os.name == "nt" else "bin"), "python")
        fake.add(python, "Python 3.14.6")
        configuration = environment / "pyvenv.cfg"
        configuration.write_text("synthetic virtual environment\n")
        assert instance.python_status(instance.uv_status()).actual == "3.14.6"
        fake.outputs[python] = "Python 3.14.9"
        configuration.write_text("changed virtual environment configuration\n")
        assert instance.python_status(instance.uv_status()).actual == "3.14.9"


@pytest.mark.parametrize("failed", [False, True])
def test_installer_attempt_invalidates_cached_probes(runtime, monkeypatch, failed):
    instance, fake = runtime
    original = fake.run

    def install(command, args, **kwargs):
        if args == ["repair"]:
            fake.outputs[fake.python] = "Python 3.14.8"
            if failed:
                raise subprocess.CalledProcessError(1, [command, *args], stderr="partial repair")
            return subprocess.CompletedProcess([], 0, "", "")
        return original(command, args, **kwargs)

    monkeypatch.setattr(toolchain, "run_safe_command", install)
    with instance._operation():
        uv = instance.uv_status()
        assert instance.python_status(uv).actual == "3.14.7"
        if failed:
            with pytest.raises(RuntimeError, match="partial repair"):
                instance._install(uv.path, ["repair"])
        else:
            instance._install(uv.path, ["repair"])
        assert instance.python_status(instance.uv_status()).actual == "3.14.8"


def test_failed_operation_discards_cached_probes(runtime):
    instance, fake = runtime
    fake.fail_package = "git-cliff"
    with pytest.raises(RuntimeError, match="registry unavailable"):
        instance.sync()
    assert instance._probes.get() is None
