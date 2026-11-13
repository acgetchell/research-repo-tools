"""Selected tool contracts, repeated from isolated wheel and sdist installs."""

import contextlib
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import override
from unittest.mock import patch

import yaml

from research_repo_tools import changelog, cli, config, prebuilt_tools, python_tools, registry, toolchain, toolchain_config
from research_repo_tools.release_assets import GitHubRelease
from research_repo_tools.toolchain_config import RUSTUP_VERSION, BinaryTool, executable


class SelectedTools:
    """Inert installer/process boundary; all selection and verification stays real."""

    def __init__(self, root):
        self.root = root
        self.plan = toolchain_config.load(config.load(root=root))
        self.runtime = toolchain.Runtime(self.plan)
        self.calls = []
        self.fail = None

    def add(self, path, output):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(output.encode("utf-8"))
        path.chmod(0o700)

    def manager(self, runtime):
        self.add(runtime.rustup, f"rustup {RUSTUP_VERSION}")

    def binary_install(self, tool, host, path, **kwargs):
        assert tool.name == "gitleaks", f"unselected binary installation: {tool.name}"
        assert host == self.runtime.host
        assert "GH_TOKEN" not in kwargs["env"] and "GITHUB_TOKEN" not in kwargs["env"]
        self.calls.append((tool.name, ["install"], kwargs))
        if self.fail == "install":
            raise OSError("inert installation failure")
        self.add(path, f"{tool.version}\n")

    def run(self, command, args, **kwargs):
        self.calls.append((str(command), args, kwargs))
        path = Path(command)
        if kwargs.get("check") is not False:
            assert "GH_TOKEN" not in kwargs["env"] and "GITHUB_TOKEN" not in kwargs["env"]
        if path == self.runtime.rustup:
            if args[:2] == ["toolchain", "install"]:
                assert args == ["toolchain", "install", "1.98.0", "--profile", "minimal", "--no-self-update"]
                for name in ("rustc", "cargo"):
                    self.add(executable(path.parent, name), f"{name} 1.98.0")
                return subprocess.CompletedProcess([], 0, "", "")
            if args[:1] == ["which"]:
                selected = executable(path.parent, args[-1])
                if not selected.exists():
                    raise subprocess.CalledProcessError(1, [command, *args], stderr="not installed")
                return subprocess.CompletedProcess([], 0, str(selected), "")
            if args[:4] == ["run", "1.98.0", "cargo", "install"]:
                assert args[-1] == "zizmor", f"unselected Cargo install: {args}"
                assert "--locked" in args and "=1.23.1" in args
                assert args[args.index("--target-dir") + 1] == str(self.runtime.build_home / self.runtime.host / "1.98.0")
                if self.fail == "install":
                    raise subprocess.CalledProcessError(17, [command, *args], stderr="inert install failure")
                self.add(executable(Path(args[args.index("--root") + 1]) / "bin", "zizmor"), "zizmor 1.23.1")
                return subprocess.CompletedProcess([], 0, "", "")
        if args == ["--version"] or (path == executable(path.parent, "gitleaks") and args == ["version"]):
            assert any(path == executable(path.parent, name) for name in ("rustup", "rustc", "cargo", "zizmor", "gitleaks")), f"unselected probe: {command}"
            return subprocess.CompletedProcess([], 0, "wrong version" if self.fail == "probe" else path.read_text(encoding="utf-8"), "")
        if path == executable(self.runtime.rustup.parent, "cargo") and args[:1] == ["publish"]:
            assert "--locked" in args and args[-2:] == ["--registry", "crates-io"]
            assert kwargs["env"]["SDKROOT"] == "native fixture"
            assert kwargs["env"]["CARGO_HOME"] == str(self.runtime.manager / "cargo")
            if "--dry-run" not in args:
                assert kwargs["env"]["CARGO_REGISTRY_TOKEN"] == "inert oidc token"
                (self.root / "published").write_bytes(b"one inert upload\n")
            return subprocess.CompletedProcess([], 101 if self.fail == "publish" else 0, "", "")
        raise AssertionError(f"unexpected or unrelated command: {command} {args}")

    def patches(self, stack):
        stack.enter_context(patch.object(toolchain, "run_safe_command", self.run))
        stack.enter_context(patch.object(prebuilt_tools, "run_safe_command", self.run))
        stack.enter_context(patch.object(prebuilt_tools, "install", self.binary_install))
        stack.enter_context(patch.object(toolchain.Runtime, "_install_rustup", lambda runtime: self.manager(runtime)))
        for name in ("uv_status", "python_status", "shell_status"):
            stack.enter_context(patch.object(toolchain.Runtime, name, side_effect=AssertionError(f"selected operation probed {name}")))
        stack.enter_context(patch.object(python_tools, "check", side_effect=AssertionError("selected operation checked unrelated Python tools")))


def catalog(root):
    (root / "pyproject.toml").write_bytes(
        b'[project]\nname="sample"\nversion="1.2.3"\nrequires-python=">=3.15"\n[tool.uv]\nrequired-version="==0.12.19"\n'
        b'[dependency-groups]\ntooling=["research-repo-tools==0.2.9"]\ndev=["pytest==9.0.2"]\n'
        b'[tool.research-repo-tools.toolchain.cargo]\nzizmor="1.23.1"\ntectonic="0.15.0"\ncargo-llvm-cov="0.6.21"\n'
        b'[tool.research-repo-tools.toolchain.binaries]\ngitleaks="8.30.1"\nosv-scanner="2.6.0"\n'
    )
    (root / ".python-version").write_bytes(b"3.15\r\n")
    (root / "rust-toolchain.toml").write_bytes(
        b'[toolchain]\nchannel="1.98.0"\nprofile="default"\ncomponents=["llvm-tools-preview"]\ntargets=["wasm32-unknown-unknown"]\n'
    )
    (root / "uv.lock").write_bytes(b'version = 1\r\n[[package]]\r\nname="sample"\r\nversion="1.2.3"\r\nsource={editable="."}\r\n')


class TestSelectedTools(unittest.TestCase):
    @override
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="selected tools café "))
        self.root = Path(directory).resolve()
        catalog(self.root)
        self.before = {p: p.read_bytes() for p in self.root.iterdir()}
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "RESEARCH_REPO_TOOLS_HOME": str(self.root / "managed home"),
                    "RESEARCH_REPO_TOOLS_CARGO_BUILD_HOME": str(self.root / "build cache"),
                    "GH_TOKEN": "inert lookup token",
                    "GITHUB_TOKEN": "inert token",
                    "SDKROOT": "native fixture",
                    "CARGO_REGISTRY_TOKEN": "inert oidc token",
                },
            )
        )
        self.tools = SelectedTools(self.root)
        stack = self.enterContext(contextlib.ExitStack())
        self.tools.patches(stack)

    def invoke(self, action, selected, *args):
        options = [part for name in selected for part in ("--tool", name)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(["--root", str(self.root), "toolchain", action, *options, *args])

    def test_cli_cargo_binary_and_multiple_selections_share_checked_environment(self):
        for names in (("gitleaks",), ("zizmor",), ("cargo",), ("zizmor", "gitleaks")):
            with self.subTest(names=names):
                self.tools.calls.clear()
                self.assertEqual(self.invoke("sync", names), 0)
                self.assertEqual(self.invoke("check", names, "--json"), 0)
                target = self.root / "command file"
                target.write_bytes(b"PREVIOUS=value\n")
                self.assertEqual(self.invoke("export", names, "--file", target.name), 0)
                payload = target.read_bytes()
                self.assertNotIn(b"\r", payload)
                self.assertTrue(payload.startswith(b"PREVIOUS=value\nRESEARCH_REPO_TOOLS_HOME="))
                exported = dict(line.split("=", 1) for line in payload.decode("utf-8").splitlines())
                self.assertNotIn("UV_PYTHON_INSTALL_DIR", exported)
                self.assertNotIn("GITHUB_TOKEN", exported)
                self.assertEqual("RUSTUP_TOOLCHAIN" in exported, names != ("gitleaks",))
                self.assertNotIn("tectonic", exported["PATH"])
                self.assertNotIn("osv-scanner", exported["PATH"])
                executable_name = "gitleaks" if "gitleaks" in names else "zizmor" if "zizmor" in names else "cargo"
                self.assertEqual(self.invoke("run", names, "--", executable_name, "version" if executable_name == "gitleaks" else "--version"), 0)
                if names == ("gitleaks",):
                    self.assertFalse(any("rustup" in command for command, _, _ in self.tools.calls))
                self.tools.calls.clear()
                self.assertEqual(self.invoke("sync", names), 0)
                self.assertFalse(any("install" in args for _, args, _ in self.tools.calls))
        self.assertEqual({path: path.read_bytes() for path in self.before}, self.before)
        self.assertFalse((self.root / ".venv").exists())

    def test_api_binary_and_multiple_selections_freeze_names_and_export_verified_paths(self):
        for selected in (["gitleaks"], ["zizmor", "gitleaks"]):
            with self.subTest(selected=selected):
                runtime = toolchain.Runtime(self.tools.plan, tools=selected)
                selected.clear()
                runtime.sync()
                self.assertTrue(all(status.ok for status in runtime.inspect()))
                output = self.root / "api export"
                runtime.export_environment(output)
                self.assertIn(str(runtime.binary_path(runtime.plan.binaries[0]).parent), runtime.environment()["PATH"].split(os.pathsep))
                self.assertNotIn("osv-scanner", output.read_text(encoding="utf-8"))
                output.unlink()

    def test_cli_json_reports_only_selection_and_required_rust_tools(self):
        for name, expected in (("gitleaks", {"gitleaks"}), ("zizmor", {"rustup", "rustc", "cargo", "zizmor"})):
            with self.subTest(name=name):
                self.assertEqual(self.invoke("sync", [name]), 0)
                with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(cli.main(["--root", str(self.root), "toolchain", "check", "--tool", name, "--json"]), 0)
                statuses = json.loads(stdout.getvalue())
                self.assertEqual({status["name"] for status in statuses}, expected)
                self.assertEqual(len(statuses), len(expected))
                self.assertEqual(stderr.getvalue(), "")
                for status in statuses:
                    self.assertEqual(set(status), {"name", "required", "actual", "path", "ok"})
                    self.assertIs(status["ok"], True)
                    self.assertTrue(Path(status["path"]).is_absolute())
                    self.assertTrue(Path(status["path"]).is_file())

    def test_api_selection_and_run_preserve_native_environment_exit_and_credentials(self):
        runtime = toolchain.Runtime(self.tools.plan, tools=["cargo"])
        runtime.sync()
        self.assertTrue(all(status.ok for status in runtime.inspect()))
        self.assertEqual(runtime.plan.rust, toolchain_config.RustToolchain("1.98.0", (), (), "minimal"))
        self.tools.fail = "publish"
        self.assertEqual(toolchain.run_command(runtime, ["cargo", "publish", "--locked", "--registry", "crates-io"]), 101)
        run = self.tools.calls[-1]
        self.assertEqual(run[2]["env"]["GH_TOKEN"], "inert lookup token")
        self.assertEqual(run[2]["env"]["CARGO_REGISTRY_TOKEN"], "inert oidc token")
        self.assertFalse(run[2]["check"])
        self.assertIsNone(run[2]["timeout"])

    def test_api_sync_and_export_preserve_unicode_paths_with_ascii_diagnostics(self):
        runtime = toolchain.Runtime(self.tools.plan, tools=["zizmor", "gitleaks"])
        output = self.root / "unicode export"
        for action in ("sync", "export"):
            if action == "export":
                runtime.sync()
            with self.subTest(action=action), io.BytesIO() as buffer:
                with io.TextIOWrapper(buffer, encoding="ascii", errors="strict", newline="\n") as stream, contextlib.redirect_stderr(stream):
                    if action == "sync":
                        runtime.sync()
                    else:
                        runtime.export_environment(output)
                        self.assertIn(str(self.root).encode("utf-8"), output.read_bytes())
                        self.assertNotIn(str(self.root).encode("ascii", errors="backslashreplace"), output.read_bytes())
                    self.assertEqual(stream.errors, "strict")
                    stream.flush()
                    self.assertIn(str(self.root).encode("ascii", errors="backslashreplace"), buffer.getvalue())
        self.assertTrue(all(status.ok for status in runtime.inspect()))

    def test_selected_cli_keeps_python_declarations_without_probing_inherited_tools(self):
        manifest = self.root / "pyproject.toml"
        text = manifest.read_text(encoding="utf-8").replace('dev=["pytest==9.0.2"]', 'dev=[{include-group="tooling"}]')
        text = python_tools.adopt_manifest(text).replace(
            "[tool.research-repo-tools.toolchain.cargo]",
            "[tool.research-repo-tools.toolchain]\ninherit-python-tools=true\n[tool.research-repo-tools.toolchain.cargo]",
        )
        manifest.write_text(text, encoding="utf-8", newline="\n")
        self.assertEqual(self.invoke("sync", ["gitleaks"]), 0)
        self.assertEqual(self.invoke("check", ["gitleaks"]), 0)
        self.assertEqual(self.invoke("export", ["gitleaks"], "--file", "inherited output"), 0)
        self.assertEqual(self.invoke("run", ["gitleaks"], "--", "gitleaks", "version"), 0)

    def test_invalid_selections_reject_before_discovery_or_effects(self):
        for names in ([], "zizmor", [""], ["zizmor", "zizmor"], ["unknown"], ["cargo-upgrade"], ["zizmor", "missing"], [None], {"zizmor"}, iter(())):
            with self.subTest(names=names), patch.object(toolchain, "host_target", side_effect=AssertionError("discovery before validation")):
                with self.assertRaises(ValueError):
                    toolchain.Runtime(self.tools.plan, tools=names)  # ty: ignore[invalid-argument-type]
        cargo = self.tools.plan.cargo
        for plan, names in (
            (replace(self.tools.plan, binaries=(BinaryTool("zizmor", "1.0.0"),)), ["zizmor"]),
            (replace(self.tools.plan, cargo=(*cargo, cargo[-1])), [cargo[-1].package]),
            (replace(self.tools.plan, rust=None), ["cargo"]),
            (replace(self.tools.plan, rust=None), ["zizmor"]),
        ):
            with self.subTest(names=names), self.assertRaises(ValueError):
                toolchain.Runtime(plan, tools=names)
        self.assertEqual(self.tools.calls, [])
        self.assertFalse(self.tools.runtime.base.exists())
        self.assertEqual(self.invoke("sync", ["gitleaks", "missing"]), 1)
        self.assertFalse(self.tools.runtime.base.exists())

    def test_failed_install_probe_or_export_never_emits_unverified_environment(self):
        output = self.root / "export output"
        output.write_bytes(b"RETAINED=yes\r\n")
        for name in ("zizmor", "gitleaks"):
            with self.subTest(name=name):
                self.tools.fail = "install"
                self.assertEqual(self.invoke("sync", [name]), 1)
                self.assertEqual(self.invoke("export", [name], "--file", str(output)), 1)
                self.assertEqual(output.read_bytes(), b"RETAINED=yes\r\n")
                self.tools.fail = None
                self.assertEqual(self.invoke("sync", [name]), 0)
                self.tools.fail = "probe"
                self.assertEqual(self.invoke("export", [name], "--file", str(output)), 1)
                self.assertEqual(output.read_bytes(), b"RETAINED=yes\r\n")
        self.tools.fail = None
        with patch.dict(os.environ, {"PATH": os.environ["PATH"] + "\nunsafe=value"}):
            self.assertEqual(self.invoke("export", ["gitleaks"], "--file", str(output)), 1)
        self.assertEqual(output.read_bytes(), b"RETAINED=yes\r\n")
        directory = self.root / "directory"
        directory.mkdir()
        self.assertEqual(self.invoke("export", ["gitleaks"], "--file", str(directory)), 1)


def cargo_workflow_fixture(arguments):
    """Run the real installed CLI; bootstrap and provider transports are inert."""
    root = Path.cwd()
    with (root / "calls.jsonl").open("ab") as stream:
        stream.write((json.dumps(arguments) + "\n").encode("utf-8"))
    if arguments[:1] == ["sync"]:
        assert arguments == ["sync", "--locked", "--managed-python", "--only-group", "tooling"]
        return
    assert arguments[:4] == ["run", "--locked", "--no-sync", "research-repo-tools"]
    fixture = SelectedTools(root)
    fixture.fail = "publish" if os.environ.get("FIXTURE_UPLOAD_TIMEOUT") else None
    sha = "a" * 40
    remote = {
        "": {"full_name": "example/package", "default_branch": "main"},
        "branches/main": {"name": "main", "protected": True},
        f"compare/{sha}...main": {"status": "identical"},
        f"commits/{sha}/check-runs?filter=latest&per_page=100": [
            {
                "total_count": 1,
                "check_runs": [{"id": 7, "name": "native", "app": {"id": 15368}, "head_sha": sha, "status": "completed", "conclusion": "success"}],
            }
        ],
    }

    def registry_read(url):
        assert url == "https://crates.io/api/v1/crates/sample/1.2.3"
        return {"version": {"crate": "sample", "num": "1.2.3", "yanked": False, "checksum": "b" * 64}} if (root / "published").exists() else registry._NOT_FOUND

    with contextlib.ExitStack() as stack:
        fixture.patches(stack)
        # Bootstrap is inert here. Installed dependency provenance has its own
        # consumer suite; this fixture owns shell composition and tool selection.
        stack.enter_context(patch("research_repo_tools.dependency_provenance.check_release_dependencies", return_value=()))
        stack.enter_context(patch("research_repo_tools.release_publishing.run_git_command", return_value=subprocess.CompletedProcess([], 0, sha, "")))
        stack.enter_context(patch("research_repo_tools.release_publishing.require_release_checkout"))
        stack.enter_context(patch("research_repo_tools.release_publishing._remote_commit", return_value=sha))
        stack.enter_context(patch("research_repo_tools.release_publishing._api", side_effect=lambda root, repo, endpoint, **kw: remote[endpoint]))
        stack.enter_context(
            patch("research_repo_tools.release_publishing.lookup_release", return_value=GitHubRelease("example/package", "v1.2.3", 91, False, False, False, ()))
        )
        stack.enter_context(patch.object(registry, "_json", side_effect=registry_read))
        sys.argv = ["research_repo_tools", *arguments[4:]]
        runpy.run_module("research_repo_tools", run_name="__main__")


class TestCargoWorkflowShell(unittest.TestCase):
    def test_installed_template_executes_with_only_core_cargo_and_keeps_timeout_recovery(self):
        document = yaml.safe_load(changelog.template("publish-crates.yml", owner="example", repository="package"))
        shell = shutil.which("bash")
        if os.name == "nt":
            # Resolve Git for Windows Bash, excluding the WSL launcher.
            sh = shutil.which("sh")
            shell = str(Path(sh).with_name("bash.exe")) if sh else None
        self.assertIsNotNone(shell, "supported workflow tests require bash (Git for Windows supplies it)")
        with tempfile.TemporaryDirectory(prefix="cargo workflow café ") as directory:
            root = Path(directory).resolve()
            catalog(root)
            with (root / "pyproject.toml").open("ab") as stream:
                stream.write(
                    b'\n[tool.research-repo-tools.publishing]\nregistry="crates-io"\npackage="sample"\nrepository="example/package"\n'
                    b'required-checks=[{name="native", app-id=15368}]\n'
                )
            (root / "Cargo.toml").write_bytes(b'[package]\nname="sample"\nversion="1.2.3"\n')
            (root / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Reviewed notes.\n")
            event = root / "release event.json"
            event.write_bytes(
                json.dumps(
                    {
                        "action": "published",
                        "repository": {"full_name": "example/package"},
                        "release": {"id": 91, "tag_name": "v1.2.3", "draft": False, "prerelease": False},
                    }
                ).encode()
            )
            env = dict(
                os.environ,
                FIXTURE_PYTHON=Path(sys.executable).as_posix(),
                FIXTURE_SCRIPT=Path(__file__).resolve().as_posix(),
                GITHUB_REPOSITORY="example/package",
                GITHUB_EVENT_NAME="release",
                GITHUB_EVENT_PATH=str(event),
                GITHUB_SHA="a" * 40,
                GITHUB_REF="refs/tags/v1.2.3",
                RELEASE_TAG="v1.2.3",
                SDKROOT="native fixture",
                RESEARCH_REPO_TOOLS_HOME=str(root / "managed home"),
                RESEARCH_REPO_TOOLS_CARGO_BUILD_HOME=str(root / "build cache"),
                MSYS2_ARG_CONV_EXCL="*",
            )
            env.pop("CARGO_REGISTRY_TOKEN", None)
            original = {path: path.read_bytes() for path in root.iterdir()}
            prefix = 'uv() { "$FIXTURE_PYTHON" -I -X utf8 "$FIXTURE_SCRIPT" --cargo-workflow-fixture "$@"; }\n'
            for name, job in document["jobs"].items():
                # Fresh managed home per job models separate hosted runners.
                env["RESEARCH_REPO_TOOLS_HOME"] = str(root / f"managed {name}")
                token = None
                for step in job["steps"]:
                    if step.get("id") == "auth":
                        token = "inert oidc token"
                    if "run" not in step:
                        continue
                    upload = "cargo publish" in step["run"] and "--dry-run" not in step["run"]
                    child_env = {**env, **({"FIXTURE_UPLOAD_TIMEOUT": "1"} if upload else {})}
                    if "CARGO_REGISTRY_TOKEN" in (step.get("env") or {}):
                        self.assertEqual(step["env"]["CARGO_REGISTRY_TOKEN"], "${{ steps.auth.outputs.token }}")
                        assert token is not None, "token output must come from the completed auth step"
                        child_env["CARGO_REGISTRY_TOKEN"] = token
                    result = subprocess.run(
                        [str(shell), "-euo", "pipefail"],
                        input=prefix + step["run"],
                        cwd=root,
                        env=child_env,
                        text=True,
                        encoding="utf-8",
                        capture_output=True,
                        timeout=120,
                    )
                    self.assertEqual(result.returncode, 101 if upload else 0, f"{name}: {step['name']}\n{result.stdout}{result.stderr}")
                    if upload:
                        self.assertTrue((root / "published").exists())
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(sum("publish" in args and "--dry-run" not in args for args in calls), 1)
            self.assertEqual(calls[-1][-7:], ["release", "verify", "v1.2.3", "--attempts", "7", "--interval", "10"])
            self.assertEqual({path: path.read_bytes() for path in original}, original)
            self.assertFalse((root / ".venv").exists())


if __name__ == "__main__":
    if sys.argv[1:2] == ["--cargo-workflow-fixture"]:
        cargo_workflow_fixture(sys.argv[2:])
    else:
        unittest.main()
