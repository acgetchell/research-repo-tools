"""Portable runtime policy contracts, exercised from both installed artifacts."""

import contextlib
import hashlib
import io
import json
import os
import platform
import sys
import sysconfig
import tempfile
import unittest
import zipfile
from email.parser import Parser
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from types import SimpleNamespace
from typing import get_args, get_type_hints, override
from unittest.mock import patch

from research_repo_tools import cli, runtime_policy, runtime_policy_build
from research_repo_tools.evidence import deterministic_json, sha256
from research_repo_tools.runtime_policy import RuntimePolicy, parse_runtime_policy, runtime_requirements, verify_runtime
from research_repo_tools.runtime_policy_build import install_runtime, record_runtime_policy, resolve_runtime


class Registry:
    def __init__(self) -> None:
        self.responses: dict[str, bytes] = {}

    def read(self, url: str) -> bytes:
        return self.responses[url]


class TestRuntimePolicy(unittest.TestCase):
    @override
    def setUp(self) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="runtime policy caf\u00e9 "))
        self.base = Path(temporary)
        self.project = self.base / "dedicated runtime"
        self.project.mkdir()
        self.environment = self.project / ".venv"
        self.site = self.environment / ("Lib/site-packages" if os.name == "nt" else "lib/python3.15/site-packages")
        self.site.mkdir(parents=True)
        (self.environment / "pyvenv.cfg").write_bytes(f"include-system-site-packages = false\nversion_info = {platform.python_version()}\n".encode())
        (self.project / "pyproject.toml").write_bytes(
            b'[project]\nname="release-runtime"\nversion="0.0.0"\nrequires-python=">=3.15"\ndependencies=["application==1.0"]\n[tool.uv]\npackage=false\n'
        )
        (self.project / "dependency-sources.toml").write_bytes(b'schema=1\n[projects.runtime]\necosystem="python"\n')
        self.lock = 'version=1\n[[package]]\nname="release-runtime"\nversion="0.0.0"\nsource={virtual="."}\n'
        self.provider = Registry()
        self.install("application", "1.0", requires="dependency>=2")
        self.install("dependency", "2.0")
        (self.project / "uv.lock").write_bytes(self.lock.encode())
        # The inert provider fixture uses synthetic registry artifacts. Native uv
        # resolution/freshness is a separate boundary; assert its invocation.
        self.lock_check = self.enterContext(patch.object(runtime_policy_build, "_check_lock"))
        self.enterContext(patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.upper().startswith("UV_")}, clear=True))
        self.bundle = self.base / "approved bundle"

    def install(self, name: str, version: str, *, requires: str | None = None, tag: str = "py3-none-any") -> None:
        info = f"{name}-{version}.dist-info"
        contents = {
            f"{name}.py": b'raise RuntimeError("fixture packages must never be imported")\r\n',
            f"{info}/METADATA": (f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n" + (f"Requires-Dist: {requires}\n" if requires else "")).encode(),
            f"{info}/WHEEL": f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n".encode(),
        }
        record = f"{info}/RECORD"
        contents[record] = "".join(f"{path},,\n" for path in (*contents, record)).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for path, payload in contents.items():
                archive.writestr(path, payload)
                target = self.site / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
        payload = stream.getvalue()
        filename = f"{name}-{version}-{tag}.whl"
        url = f"https://files.pythonhosted.org/packages/fixture/{filename}"
        self.provider.responses[url] = payload
        digest = hashlib.sha256(payload).hexdigest()
        self.provider.responses[f"https://pypi.org/pypi/{name}/{version}/json"] = json.dumps(
            {
                "info": {"name": name, "version": version},
                "urls": [
                    {"filename": filename, "url": url, "size": len(payload), "digests": {"sha256": digest}, "yanked": False, "packagetype": "bdist_wheel"}
                ],
            }
        ).encode()
        self.lock += f'[[package]]\nname="{name}"\nversion="{version}"\nsource={{registry="https://pypi.org/simple"}}\nwheels=[{{url="{url}",hash="sha256:{digest}",size={len(payload)}}}]\n'

    def record(self) -> RuntimePolicy:
        return record_runtime_policy(self.project, self.bundle, provider=self.provider)

    def test_runtime_annotations_preserve_public_type_introspection(self) -> None:
        from research_repo_tools import runtime_policy_cli
        from research_repo_tools.config import Config
        from research_repo_tools.release_runtime import isolated_release_runtime

        self.assertIn(RuntimePolicy, get_args(get_type_hints(isolated_release_runtime)["policy"]))
        self.assertIs(get_type_hints(runtime_policy_cli.run)["settings"], Config)

    def test_record_and_checkout_free_verification_preserve_exact_bytes(self) -> None:
        policy = self.record()
        self.lock_check.assert_called_once_with(self.project.resolve())
        self.assertEqual(verify_runtime(policy, environment=self.environment, artifacts=self.bundle), ("application", "dependency"))
        self.assertEqual((self.bundle / "requirements.txt").read_bytes(), runtime_requirements(policy))
        self.assertIn(b"dependency==2.0 --hash=sha256:", runtime_requirements(policy))
        self.assertNotIn(str(self.project).encode(), policy.payload)
        # Verification uses the pinned handoff and retained wheels, not a project
        # lock, online resolver, consumer interpreter or local signing key.
        for filename in ("uv.lock", "pyproject.toml", "dependency-sources.toml"):
            (self.project / filename).unlink()
        with patch.object(self.provider, "read", side_effect=AssertionError("unexpected network")):
            self.assertEqual(len(verify_runtime(policy, environment=self.environment, artifacts=self.bundle)), 2)

    def test_installed_version_bytes_additions_and_missing_dependencies_fail(self) -> None:
        policy = self.record()
        target = self.site / "dependency.py"
        saved = target.read_bytes()
        target.write_bytes(saved.replace(b"\r\n", b"\n"))
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            verify_runtime(policy, environment=self.environment, artifacts=self.bundle)
        target.write_bytes(saved)
        metadata = self.site / "dependency-2.0.dist-info/METADATA"
        original = metadata.read_bytes()
        metadata.write_bytes(original.replace(b"Version: 2.0", b"Version: 2.1"))
        with self.assertRaisesRegex(ValueError, "version drift"):
            verify_runtime(policy, environment=self.environment, artifacts=self.bundle)
        metadata.write_bytes(original)
        for name in ("unexpected.py", "startup.pth", "cached.pyc"):
            with self.subTest(name=name):
                path = self.site / name
                path.write_bytes(b"untrusted")
                with self.assertRaisesRegex(ValueError, "unexpected file"):
                    verify_runtime(policy, environment=self.environment, artifacts=self.bundle)
                path.unlink()
        metadata.unlink()
        with self.assertRaisesRegex(ValueError, "name and version|missing|unexpected"):
            verify_runtime(policy, environment=self.environment, artifacts=self.bundle)

    def test_authentication_and_complete_closure_are_required(self) -> None:
        policy = self.record()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            parse_runtime_policy(policy.payload + b" ", expected_sha256=policy.digest)
        with self.assertRaises(TypeError):
            RuntimePolicy()
        for requirements in (["application>=2"], ["dependency==2.0"], ["missing==1"], ["application[missing]==1.0"]):
            with self.subTest(requirements=requirements):
                data = json.loads(policy.payload)
                data["requirements"] = requirements
                payload = deterministic_json(data)
                altered = parse_runtime_policy(payload, expected_sha256=sha256(payload))
                with self.assertRaisesRegex(ValueError, "missing|unexpected|extra"):
                    verify_runtime(altered, environment=self.environment, artifacts=self.bundle)

    def test_target_configuration_is_explicit_unambiguous_and_matches_python(self) -> None:
        policy = self.record()
        path = self.environment / "pyvenv.cfg"
        current = f"version_info = {platform.python_version()}\n"
        for text in (
            current,
            current + "include-system-site-packages\t=\ttrue\n",
            current + "include-system-site-packages=false\ninclude-system-site-packages=false\n",
            current + "include-ſystem-site-packages=false\n",
            current + "home=interpreter\u2028include-system-site-packages=false\n",
            "version_info=3.14.0\ninclude-system-site-packages=false\n",
            "include-system-site-packages=false\n",
        ):
            with self.subTest(configuration=text):
                path.write_bytes(text.encode())
                with self.assertRaisesRegex(ValueError, "site-packages|duplicate|Python version"):
                    verify_runtime(policy, environment=self.environment, artifacts=self.bundle)
        path.write_bytes((current + " include-system-site-packages \t=\t false \n").encode())
        self.assertEqual(len(verify_runtime(policy, environment=self.environment, artifacts=self.bundle)), 2)

    def test_short_uv_version_requires_the_known_base_interpreter(self) -> None:
        policy = self.record()
        path = self.environment / "pyvenv.cfg"
        home = Path(getattr(sys, "_base_executable", sys.executable)).resolve().parent
        text = f"include-system-site-packages = false\nversion_info = {sys.version_info.major}.{sys.version_info.minor}\nhome = {home}\n"
        path.write_bytes(text.encode())
        self.assertEqual(len(verify_runtime(policy, environment=self.environment, artifacts=self.bundle)), 2)
        # Explicit conflicting patches are never inferred away, even when the
        # base path names the trusted interpreter.
        path.write_bytes(text.replace(f"version_info = {sys.version_info.major}.{sys.version_info.minor}\n", "version_info = 3.15.999\n").encode())
        with self.assertRaisesRegex(ValueError, "Python version"):
            verify_runtime(policy, environment=self.environment, artifacts=self.bundle)

    def test_recorded_startup_hooks_are_rejected_before_wheel_verification(self) -> None:
        policy = self.record()
        record = self.site / "application-1.0.dist-info/RECORD"
        original = record.read_bytes()
        for name in ("hook.pth", "hook.start", "sitecustomize.py", "usercustomize.py"):
            with self.subTest(name=name):
                path = self.site / name
                path.write_bytes(b"application:startup\n")
                record.write_bytes(original + f"{name},,\n".encode())
                with self.assertRaisesRegex(ValueError, "startup hooks"):
                    verify_runtime(policy, environment=self.environment, artifacts=self.bundle)
                path.unlink()
                record.write_bytes(original)

    def test_startup_packages_and_native_extensions_are_rejected(self) -> None:
        policy = self.record()
        record = self.site / "application-1.0.dist-info/RECORD"
        original = record.read_bytes()
        for name in ("sitecustomize/__init__.py", "usercustomize/__init__.py", "sitecustomize" + EXTENSION_SUFFIXES[0]):
            with self.subTest(name=name):
                path = self.site / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"untrusted startup code")
                record.write_bytes(original + f"{name},,\n".encode())
                try:
                    with self.assertRaisesRegex(ValueError, "startup hooks"):
                        verify_runtime(policy, environment=self.environment, artifacts=self.bundle)
                finally:
                    path.unlink()
                    if path.parent != self.site:
                        path.parent.rmdir()
                    record.write_bytes(original)

    def test_metadata_is_bounded_before_distribution_discovery(self) -> None:
        policy = self.record()
        metadata = self.site / "application-1.0.dist-info/METADATA"
        with metadata.open("wb") as stream:
            stream.truncate(runtime_policy.MAX_POLICY_BYTES + 1)
        with patch.object(runtime_policy, "distribution_inventory") as discover:
            with self.assertRaisesRegex(ValueError, "metadata.*byte limit"):
                verify_runtime(policy, environment=self.environment, artifacts=self.bundle)
        discover.assert_not_called()

    def test_extra_names_use_packaging_normalization(self) -> None:
        metadata = {
            "application": Parser().parsestr(
                'Name: application\nVersion: 1.0\nProvides-Extra: fast-mode\nRequires-Dist: dependency>=2; extra == "fast-mode"\n'
            ),
            "dependency": Parser().parsestr("Name: dependency\nVersion: 2.0\n"),
        }
        runtime_policy._closure(["application[Fast_Mode]==1.0"], metadata)

    def executing_context(self):
        # Model only the executing interpreter boundary; keep the real host's
        # platform, installed-file verifier and policy checks unchanged.
        executing = SimpleNamespace(**vars(sys))
        executing.prefix = str(self.environment)
        executing.flags = SimpleNamespace(isolated=True)
        executing.dont_write_bytecode = True
        executing.path = [sysconfig.get_path("stdlib"), str(self.site)]
        if dynamic := sysconfig.get_config_var("DESTSHARED"):
            executing.path.append(dynamic)
        self.enterContext(patch.object(runtime_policy, "sys", executing))
        self.enterContext(patch.object(runtime_policy, "__file__", str(self.site / "checker.py")))
        return executing

    def test_executing_runtime_accepts_native_paths_but_rejects_unverified_stdlib_children(self) -> None:
        policy = self.record()
        executing = self.executing_context()
        self.assertEqual(runtime_policy.verify_executing_runtime(policy, artifacts=self.bundle), ("application", "dependency"))
        executing.path.append(str(Path(sysconfig.get_path("stdlib")) / "unverified-packages"))
        with self.assertRaisesRegex(ValueError, "external package location"):
            runtime_policy.verify_executing_runtime(policy, artifacts=self.bundle)

    def test_executing_runtime_accepts_standard_zip_in_platform_library_directory(self) -> None:
        policy = self.record()
        executing = self.executing_context()
        base = self.base / "base interpreter"
        executing.base_prefix = str(base)
        native_paths = executing.path.copy()
        abi_thread = "t" if sysconfig.get_config_var("Py_GIL_DISABLED") and os.name != "nt" else ""
        filename = f"python{sys.version_info.major}{sys.version_info.minor}{abi_thread}.zip"
        for library in ("lib", "lib64"):
            with self.subTest(library=library):
                executing.platlibdir = library
                standard_zip = (base if os.name == "nt" else base / library) / filename
                # CPython adds this entry even when the ZIP does not exist.
                self.assertFalse(standard_zip.exists())
                executing.path = [*native_paths, str(standard_zip)]
                self.assertEqual(runtime_policy.verify_executing_runtime(policy, artifacts=self.bundle), ("application", "dependency"))
                executing.path.append(str(base / "unapproved-library" / filename))
                with self.assertRaisesRegex(ValueError, "external package location"):
                    runtime_policy.verify_executing_runtime(policy, artifacts=self.bundle)

    def test_release_context_checks_policy_before_probes_and_writer_effects(self) -> None:
        from research_repo_tools import release_runtime
        from research_repo_tools.config import load

        policy = self.record()
        self.executing_context()
        checkout = self.base / "observed checkout"
        checkout.mkdir()
        settings = load(root=checkout)
        with patch.object(release_runtime, "_prepare", return_value=(dict(os.environ), self.base / "gh")) as prepare:
            with release_runtime.isolated_release_runtime(settings, policy=policy, artifacts=self.bundle):
                self.assertEqual(prepare.call_count, 1)
            (self.site / "dependency.py").write_bytes(b"changed")
            prepare.reset_mock()
            with self.assertRaisesRegex(ValueError, "bytes differ"):
                with release_runtime.isolated_release_runtime(settings, policy=policy, artifacts=self.bundle):
                    self.fail("writer must not execute with changed runtime bytes")
            prepare.assert_not_called()

    def test_recording_rejects_changes_between_initial_parse_and_graph_capture(self) -> None:
        original = runtime_policy_build.inspect_project

        def change_before_capture(*args, **kwargs):
            path = self.project / "pyproject.toml"
            path.write_bytes(path.read_bytes().replace(b"application==1.0", b"application>=1.0"))
            return original(*args, **kwargs)

        with patch.object(runtime_policy_build, "inspect_project", side_effect=change_before_capture):
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                self.record()
        self.lock_check.assert_not_called()
        self.assertFalse(self.bundle.exists())

    def test_malformed_runtime_project_has_an_actionable_cli_error(self) -> None:
        path = self.project / "pyproject.toml"
        path.write_bytes(path.read_bytes().replace(b'dependencies=["application==1.0"]\n', b""))
        with contextlib.redirect_stderr(io.StringIO()) as error:
            result = cli.main(["--root", str(self.base), "release", "runtime", "record", "--project", str(self.project), "--output", str(self.bundle)])
        self.assertEqual(result, 1)
        self.assertIn("requirement", error.getvalue())
        self.assertFalse(self.bundle.exists())

    def test_recording_rejects_incompatible_wheels_before_publishing_bundle(self) -> None:
        self.lock = self.lock[: self.lock.index('[[package]]\nname="application"')]
        self.provider.responses.clear()
        self.install("application", "1.0", requires="dependency>=2", tag="cp39-cp39-any")
        self.install("dependency", "2.0")
        (self.project / "uv.lock").write_bytes(self.lock.encode())
        with self.assertRaisesRegex(ValueError, "incompatible"):
            self.record()
        self.assertFalse(self.bundle.exists())

    def test_recording_preserves_a_destination_created_during_acquisition(self) -> None:
        original = self.provider.read

        def create_destination(url: str) -> bytes:
            if url.endswith(".whl") and not self.bundle.exists():
                self.bundle.mkdir()
                (self.bundle / "runtime.json").write_bytes(b"another writer's selection")
            return original(url)

        with patch.object(self.provider, "read", side_effect=create_destination):
            with self.assertRaisesRegex(ValueError, "new bundle directory"):
                self.record()
        self.assertEqual({path.name for path in self.bundle.iterdir()}, {"runtime.json"})
        self.assertEqual((self.bundle / "runtime.json").read_bytes(), b"another writer's selection")

    def test_offline_install_uses_pinned_policy_not_mutable_requirements_file(self) -> None:
        policy = self.record()
        (self.bundle / "requirements.txt").write_bytes(b"untrusted-package==9\n")
        directory = self.base / "new installed runtime"
        python = install_runtime(policy, directory, artifacts=self.bundle)
        self.assertTrue(python.is_file())
        self.assertEqual(verify_runtime(policy, environment=directory, artifacts=self.bundle), ("application", "dependency"))
        with self.assertRaisesRegex(ValueError, "new directory"):
            install_runtime(policy, directory, artifacts=self.bundle)

    def test_resolution_uses_structured_arguments_and_clean_environment(self) -> None:
        directory = self.base / "resolve space"

        def resolve(command, arguments, *, cwd, env):
            self.assertIn("--no-build", arguments)
            self.assertIn("--no-config", arguments)
            self.assertEqual(arguments[arguments.index("--project") + 1], str(directory.resolve()))
            self.assertNotIn("GH_TOKEN", env)
            self.assertNotIn("UV_INDEX", env)
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotEqual(cwd, directory)
            (directory / "uv.lock").write_bytes(self.lock.encode())

        with patch.dict(os.environ, {"GH_TOKEN": "sensitive", "UV_INDEX": "untrusted", "PYTHONPATH": "untrusted"}):
            with patch.object(runtime_policy_build, "run_command", side_effect=resolve), patch.object(runtime_policy_build, "inspect_project"):
                result = resolve_runtime(directory, ("application==1.0",))
        self.assertEqual(result, directory.resolve() / "uv.lock")
        self.assertIn(b'"application==1.0"', (directory / "pyproject.toml").read_bytes())

    def test_resolution_preserves_an_enclosing_workspaces_lock(self) -> None:
        parent = self.base / "enclosing workspace"
        parent.mkdir()
        (parent / "pyproject.toml").write_bytes(
            b'[project]\nname="outer"\nversion="0.0.0"\nrequires-python=">=3.15"\ndependencies=[]\n'
            b'[tool.uv]\npackage=false\n[tool.uv.workspace]\nmembers=["runtimes/*"]\n'
        )
        runtime_policy_build.run_command(
            "uv",
            ["lock", "--offline", "--no-config", "--no-build", "--no-python-downloads", "--python", sys.executable, "--project", str(parent)],
            cwd=self.base,
        )
        original = (parent / "uv.lock").read_bytes()
        directory = parent / "runtimes" / "isolated"
        # Exercise real uv workspace discovery without a registry dependency.
        # Provenance for active package graphs is covered by the other fixtures;
        # this inactive requirement makes this command-boundary test offline.
        with patch.object(runtime_policy_build, "inspect_project") as inspect:
            result = resolve_runtime(directory, ('application==1.0; python_version < "1"',))
        self.assertEqual((parent / "uv.lock").read_bytes(), original)
        self.assertEqual(result, directory.resolve() / "uv.lock")
        self.assertTrue(result.is_file())
        inspect.assert_called_once_with(directory.resolve(), runtime_policy_build.DependencyProject("python"))

    def test_changed_registry_wheel_and_concurrent_inputs_publish_nothing(self) -> None:
        url = next(url for url in self.provider.responses if url.endswith(".whl"))
        self.provider.responses[url] += b"tampered"
        with self.assertRaisesRegex(ValueError, "size differs"):
            self.record()
        self.assertFalse(self.bundle.exists())
        self.provider.responses[url] = self.provider.responses[url][:-8]
        original_read = self.provider.read

        def change_inputs(url: str) -> bytes:
            if url.endswith(".whl"):
                with (self.project / "pyproject.toml").open("ab") as stream:
                    stream.write(b"\n# changed\n")
            return original_read(url)

        with patch.object(self.provider, "read", side_effect=change_inputs), self.assertRaisesRegex(ValueError, "inputs changed"):
            self.record()
        self.assertFalse((self.bundle / "runtime.json").exists())

    def test_cli_verifies_pinned_bundle_and_rejects_unpaired_release_selection(self) -> None:
        policy = self.record()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = cli.main(
                [
                    "--root",
                    str(self.base),
                    "release",
                    "runtime",
                    "verify",
                    "--policy",
                    str(self.bundle / "runtime.json"),
                    "--sha256",
                    policy.digest,
                    "--environment",
                    str(self.environment),
                ]
            )
        self.assertEqual(result, 0)
        self.assertIn("Verified 2 runtime distributions", output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as error:
            result = cli.main(["--root", str(self.base), "release", "--runtime-policy", str(self.bundle / "runtime.json"), "check"])
        self.assertEqual(result, 1)
        self.assertIn("require each other", error.getvalue())

    def test_resolver_rejects_existing_directories_and_direct_requirements_before_effects(self) -> None:
        for directory, requirements in ((self.project, ("application==1",)), (self.base / "new", ("application @ https://example.com/a.whl",))):
            with self.subTest(requirements=requirements), patch.object(runtime_policy_build, "run_command") as run, self.assertRaises(ValueError):
                resolve_runtime(directory, requirements)
            run.assert_not_called()
        self.assertFalse((self.base / "new").exists())


if __name__ == "__main__":
    unittest.main()
