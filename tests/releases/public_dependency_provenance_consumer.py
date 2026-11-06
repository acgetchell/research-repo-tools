"""Upstream tooling policy exercised from source and installed distributions."""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
import venv
import zipfile
from importlib.metadata import distributions
from pathlib import Path
from typing import Any, override
from unittest.mock import patch

from research_repo_tools import _dependency_probe, cli, config, dependency_provenance, process, python_baseline, python_tools, registry
from research_repo_tools.dependency_provenance import check_development_dependency, check_release_dependencies

PACKAGE = "research-repo-tools"
VERSION = "0.2.6"
COMMIT = "a" * 40
URL = "https://github.com/example/tool.git"
WHEEL = "research_repo_tools-0.2.6-py3-none-any.whl"
DIST = "research_repo_tools-0.2.6.dist-info"
FILE_URL = "https://files.pythonhosted.org/packages/fixture/"


class Response(io.BytesIO):
    status = 200
    url = FILE_URL + WHEEL


class TestDependencyProvenance(unittest.TestCase):
    @override
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dependency café ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.installed = self.root / "installed"
        self.installed.mkdir()
        self.contents = {
            "research_repo_tools/__init__.py": b'__version__ = "0.2.6"\r\n# caf\xc3\xa9\n',
            "research_repo_tools/dependency_provenance.py": b"# synthetic installed checker location\n",
            f"{DIST}/METADATA": b"Metadata-Version: 2.4\nName: research-repo-tools\nVersion: 0.2.6\nRequires-Python: >=3.15\n",
            f"{DIST}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        self.contents[f"{DIST}/RECORD"] = "".join(f"{name},,\n" for name in (*self.contents, f"{DIST}/RECORD")).encode()
        for name, data in self.contents.items():
            path = self.installed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in self.contents.items():
                archive.writestr(name, data)
        self.wheel = stream.getvalue()
        self.digest = hashlib.sha256(self.wheel).hexdigest()
        self.manifest = (
            '[project]\nname="consumer"\nversion="1.0.0"\nrequires-python=">=3.15"\n'
            '[dependency-groups]\ndev=["research-repo-tools[python-tools]==0.2.6"]\n'
            "[tool.uv]\npackage=false\n"
            '[tool.research-repo-tools.release]\npublished-dependencies=["research-repo-tools"]\n'
        )
        self.lock = (
            'version=1\n[[package]]\nname="research-repo-tools"\nversion="0.2.6"\nsource={registry="https://pypi.org/simple"}\n'
            f'sdist={{url="{FILE_URL}research_repo_tools-0.2.6.tar.gz",hash="sha256:{"b" * 64}",size=10}}\n'
            f'wheels=[{{url="{FILE_URL}{WHEEL}",hash="sha256:{self.digest}",size={len(self.wheel)}}}]\n'
        )
        (self.root / "pyproject.toml").write_bytes(self.manifest.encode())
        (self.root / "uv.lock").write_bytes(self.lock.encode())
        (self.root / ".python-version").write_bytes(b"3.15\n")
        self.inventory: dict[str, Any] = {
            "info": {"name": PACKAGE, "version": VERSION},
            "urls": [
                {"filename": WHEEL, "url": FILE_URL + WHEEL, "size": len(self.wheel), "digests": {"sha256": self.digest}, "yanked": False},
                {
                    "filename": "research_repo_tools-0.2.6.tar.gz",
                    "url": FILE_URL + "research_repo_tools-0.2.6.tar.gz",
                    "size": 10,
                    "digests": {"sha256": "b" * 64},
                    "yanked": False,
                },
            ],
        }
        self.enterContext(
            patch.object(_dependency_probe, "distributions", side_effect=lambda **kwargs: distributions(**(kwargs or {"path": [str(self.installed)]})))
        )
        # Model the executing checker as belonging to this synthetic distribution.
        # A separate regression overrides this location to exercise source shadowing.
        self.enterContext(patch.object(dependency_provenance, "__file__", str(self.installed / "research_repo_tools/dependency_provenance.py")))
        self.network = self.enterContext(patch.object(registry, "_json", return_value=self.inventory))
        self.enterContext(patch.object(registry._OPENER, "open", side_effect=lambda *_args, **_kwargs: Response(self.wheel)))
        self.command = self.enterContext(patch.object(process, "run_command", return_value=subprocess.CompletedProcess([], 0, "", "")))

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def git(self):
        (self.root / "pyproject.toml").write_bytes((self.manifest + f'[tool.uv.sources]\nresearch-repo-tools={{git="{URL}",rev="{COMMIT}"}}\n').encode())
        (self.root / "uv.lock").write_bytes(self.lock.replace('registry="https://pypi.org/simple"', f'git="{URL}?rev={COMMIT}#{COMMIT}"').encode())
        self.direct({"url": URL, "vcs_info": {"vcs": "git", "requested_revision": COMMIT, "commit_id": COMMIT}})

    def direct(self, value):
        (self.installed / DIST / "direct_url.json").write_bytes(json.dumps(value).encode())

    def test_published_pin_checks_artifacts_and_installed_bytes_without_mutation(self):
        before = self.snapshot()
        (identity,) = check_release_dependencies(self.root)
        self.assertEqual((identity.name, identity.version, identity.source, identity.revision), (PACKAGE, VERSION, dependency_provenance.INDEX, None))
        self.assertEqual(self.snapshot(), before)
        self.assertIn("--check", self.command.call_args.args[1])
        self.assertIn("--offline", self.command.call_args.args[1])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["--root", str(self.root), "release", "dependencies"]), 0)
        self.assertIn("Verified 1 published", output.getvalue())

    def test_exact_git_pin_preserves_baseline_and_tools_without_claiming_publication(self):
        self.git()
        before = self.snapshot()
        authority = python_baseline.PythonBaseline(">=3.15", "3.15", VERSION)
        with patch.object(python_baseline, "baseline", return_value=authority), patch.object(python_tools, "version", return_value=VERSION):
            python_baseline.check(self.root)
            python_baseline.check_minimum(self.root)
            python_tools.check_declarations(tomllib.loads((self.root / "pyproject.toml").read_text(encoding="utf-8")))
        identity = check_development_dependency(self.root)
        assert identity is not None
        self.assertEqual(identity.revision, COMMIT)
        self.network.assert_not_called()
        self.command.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "published registry dependency"):
            check_release_dependencies(self.root)
        self.network.assert_not_called()

    def test_development_rejects_floating_lock_and_installed_commit_drift(self):
        self.git()
        paths = (self.root / "pyproject.toml", self.root / "uv.lock", self.installed / DIST / "direct_url.json")
        for path in paths:
            original = path.read_bytes()
            for replacement in (b"main", b"c" * 40):
                with self.subTest(path=path.name, replacement=replacement):
                    path.write_bytes(original.replace(COMMIT.encode(), replacement))
                    before = self.snapshot()
                    with self.assertRaises(ValueError):
                        check_development_dependency(self.root)
                    self.assertEqual(self.snapshot(), before)
                path.write_bytes(original)
        self.network.assert_not_called()

    def test_release_rejects_all_source_overrides_before_any_network_or_commands(self):
        for source in (
            '{git="https://github.com/example/tool.git",rev="' + COMMIT + '"}',
            '{path="../tool"}',
            '{path="../tool",editable=true}',
            "{workspace=true}",
            '{url="https://example.invalid/tool.whl"}',
            '[{index="other",marker="sys_platform == \'win32\'"}]',
        ):
            with self.subTest(source=source):
                (self.root / "pyproject.toml").write_bytes((self.manifest + f"[tool.uv.sources]\nresearch-repo-tools={source}\n").encode())
                with self.assertRaisesRegex(ValueError, "Publish the supplying upstream version"):
                    check_release_dependencies(self.root)
        self.command.assert_not_called()
        self.network.assert_not_called()

    def test_requirement_lock_and_runtime_failures_are_closed(self):
        cases = (
            ("pyproject.toml", self.manifest.replace("==0.2.6", " @ https://example.invalid/tool.whl")),
            ("pyproject.toml", self.manifest.replace("==0.2.6", ">=0.2.6")),
            ("pyproject.toml", self.manifest.replace("==0.2.6", "==0.2.6; sys_platform == 'win32'")),
            ("pyproject.toml", self.manifest.replace("package=false", 'package=false\noverride-dependencies=["research-repo-tools==0.2.6"]')),
            ("uv.lock", self.lock.replace('version="0.2.6"', 'version="0.2.5"')),
            ("uv.lock", self.lock + self.lock[self.lock.index("[[package]]") :]),
            ("uv.lock", self.lock.replace('registry="https://pypi.org/simple"', 'editable="."')),
            ("uv.lock", self.lock.replace('registry="https://pypi.org/simple"', 'registry="https://private.invalid/simple"')),
            ("uv.lock", self.lock.replace("sha256:", "md5:")),
        )
        for name, data in cases:
            with self.subTest(name=name, data=data[:160]):
                (self.root / "pyproject.toml").write_bytes(self.manifest.encode())
                (self.root / "uv.lock").write_bytes(self.lock.encode())
                (self.root / name).write_bytes(data.encode())
                with self.assertRaises(ValueError):
                    check_release_dependencies(self.root)
        self.network.assert_not_called()
        self.command.assert_not_called()

    def test_same_version_direct_installs_and_missing_provenance_are_rejected(self):
        for origin in (
            {"url": URL, "vcs_info": {"vcs": "git", "commit_id": COMMIT}},
            {"url": "file:///tmp/tool", "dir_info": {"editable": True}},
            {"url": FILE_URL + WHEEL, "archive_info": {"hashes": {"sha256": self.digest}}},
            {},
            "malformed",
        ):
            self.direct(origin)
            with self.assertRaisesRegex(ValueError, "direct_url.json"):
                check_release_dependencies(self.root)
        self.network.assert_not_called()
        (self.installed / DIST / "direct_url.json").unlink()
        (self.installed / DIST / "RECORD").unlink()
        with self.assertRaisesRegex(ValueError, "RECORD is missing"):
            check_release_dependencies(self.root)

    def test_same_version_modified_installed_bytes_do_not_prove_publication(self):
        (self.installed / "research_repo_tools/__init__.py").write_bytes(b"# unreleased source with identical package version\n")
        with self.assertRaisesRegex(ValueError, "runtime bytes differ"):
            check_release_dependencies(self.root)

    def test_extra_installed_package_code_is_not_publication_evidence(self):
        relative = "research_repo_tools/unreleased_feature.py"
        (self.installed / relative).write_bytes(b"# same-version extra code\n")
        record = self.installed / DIST / "RECORD"
        for recorded in (False, True):
            with self.subTest(recorded=recorded):
                if recorded:
                    record.write_bytes(record.read_bytes() + f"{relative},,\n".encode())
                with self.assertRaisesRegex(ValueError, "unpublished installed files"):
                    check_release_dependencies(self.root)

    def test_executing_checker_cannot_shadow_a_valid_installed_distribution(self):
        shadow = self.root / "source checkout" / "dependency_provenance.py"
        shadow.parent.mkdir()
        shadow.write_bytes(b"# same version, separately imported implementation\n")
        with patch.object(dependency_provenance, "__file__", str(shadow)):
            with self.assertRaisesRegex(ValueError, "executing checker"):
                check_release_dependencies(self.root)
        self.network.assert_not_called()

    def test_registry_failures_and_hash_mismatch_are_not_accepted(self):
        for inventory in ({}, {"info": {"name": PACKAGE, "version": "0.2.5"}, "urls": []}, {"info": self.inventory["info"], "urls": []}):
            with patch.object(registry, "_json", return_value=inventory), self.assertRaises(ValueError):
                check_release_dependencies(self.root)
        self.inventory["urls"][0]["yanked"] = True
        with self.assertRaisesRegex(ValueError, "yanked"):
            check_release_dependencies(self.root)
        self.inventory["urls"][0]["yanked"] = False
        with patch.object(registry._OPENER, "open", return_value=Response(b"different bytes")), self.assertRaisesRegex(ValueError, "wheel bytes differ"):
            check_release_dependencies(self.root)

    def test_stale_lock_check_precedes_registry_and_has_recovery_guidance(self):
        self.command.side_effect = subprocess.CalledProcessError(1, ["uv", "lock", "--check"], stderr="lock would change")
        with self.assertRaisesRegex(ValueError, "resolve and sync uv.lock"):
            check_release_dependencies(self.root)
        self.network.assert_not_called()

    def test_changed_manifest_during_proof_is_rejected(self):
        def download(*_args, **_kwargs):
            (self.root / "pyproject.toml").write_bytes(self.manifest.replace("==0.2.6", "==0.2.7").encode())
            return Response(self.wheel)

        with patch.object(registry._OPENER, "open", side_effect=download), self.assertRaisesRegex(ValueError, "changed during verification"):
            check_release_dependencies(self.root)

    def test_policy_is_selected_upstream_only_and_cannot_be_disabled_by_empty_override(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            config.parse({"release": {"published-dependencies": [PACKAGE, "Research_Repo_Tools"]}}, root=self.root)
        (self.root / "pyproject.toml").write_bytes(self.manifest.replace('name="consumer"', f'name="{PACKAGE}"').encode())
        with self.assertRaisesRegex(ValueError, "package being released"):
            check_release_dependencies(self.root, packages=())
        (self.root / "pyproject.toml").write_bytes(
            self.manifest.replace('published-dependencies=["research-repo-tools"]', "published-dependencies=[]").encode()
        )
        self.assertEqual(check_release_dependencies(self.root), ())
        self.network.assert_not_called()

    def test_release_intent_fails_before_reading_campaign_configuration(self):
        self.git()
        before = self.snapshot()
        for command in (
            ("release", "dependencies"),
            ("performance", "prepare", "missing.toml", "--release-intent"),
            ("performance", "release", "missing.toml", "--release-intent"),
            ("performance", "measure", "missing.toml", "--payload", "x", "--manifest", "y", "--release-intent"),
        ):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(cli.main(["--root", str(self.root), *command]), 1)
                self.assertIn("published registry dependency", errors.getvalue())
        self.assertEqual(self.snapshot(), before)
        self.network.assert_not_called()

    def test_release_entry_points_reject_development_before_policy_io_or_effects(self):
        self.git()
        common = ("--checkout", str(self.root), "--commit", COMMIT, "--version", "1.0.0", "--output", "result.json")
        commands = [
            ("release", "evidence", "verify", "missing.toml", *common),
            ("release", "evidence", "published", "missing.toml", *common, "--expected", "approved.json"),
            ("release", "evidence", "create", "missing.toml", *common, "--attempt", "1", "--run-id", "1"),
            (
                "release",
                "evidence",
                "candidate",
                "missing.toml",
                "--checkout",
                str(self.root),
                "--acquisition-sha256",
                "b" * 64,
                "--manifest",
                "manifest.json",
                "--output",
                "result.json",
                "--payload",
                "payload.json",
                "--policy",
                "candidate.json",
            ),
            ("release", "assets", "preflight", "missing.toml", "--checkout", str(self.root), "--commit", COMMIT, "--tag", "v1.0.0", "--output", "target.json"),
        ]
        before = self.snapshot()
        for command in commands:
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(cli.main(["--root", str(self.root), *command]), 1)
                self.assertIn("published registry dependency", errors.getvalue())
        self.assertEqual(self.snapshot(), before)
        self.command.assert_not_called()
        self.network.assert_not_called()

    def test_public_asset_candidate_and_approval_apis_gate_before_network_or_writes(self):
        from research_repo_tools import candidate_reuse, release_assets, release_publishing

        self.git()
        settings = config.load(root=self.root)
        target = release_assets.ReleaseTarget("example/project", "v1.0.0", 1, COMMIT)
        callbacks = (
            lambda: release_assets.preflight_release_target(self.root, "example/project", "v1.0.0", COMMIT),
            lambda: release_assets.revalidate_release_target(self.root, target),
            lambda: release_assets.publish_release_asset(self.root, "example/project", "v1.0.0", self.root / "asset.tar.gz", target=target, publish=True),
            lambda: release_publishing.check_reviewed_release(settings, "v1.0.0", draft=True),
            lambda: release_publishing.verify_publication(settings, "v1.0.0"),
        )
        before = self.snapshot()
        for callback in callbacks:
            with self.assertRaisesRegex(ValueError, "published registry dependency"):
                callback()
        # The candidate gate precedes inspecting even a malformed untrusted handoff.
        untrusted: Any = None
        with self.assertRaisesRegex(ValueError, "published registry dependency"):
            candidate_reuse.verify_candidate_release(self.root, untrusted, untrusted, untrusted, untrusted, acquisition_sha256="b" * 64)
        self.assertEqual(self.snapshot(), before)
        self.network.assert_not_called()

    def test_ordinary_acquisition_does_not_inherit_release_rejection(self):
        self.git()
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(cli.main(["--root", str(self.root), "performance", "prepare", "missing.toml"]), 1)
        self.assertNotIn("published registry dependency", errors.getvalue())
        self.assertIn("missing.toml", errors.getvalue())
        self.network.assert_not_called()

    def test_mandatory_python_minimum_preserves_non_inheriting_local_artifact_consumers(self):
        source = self.manifest + '[tool.uv.sources]\nresearch-repo-tools={path="../local package.whl"}\n'
        (self.root / "pyproject.toml").write_bytes(source.encode())
        before = self.snapshot()
        with patch.object(python_baseline, "baseline", return_value=python_baseline.PythonBaseline(">=3.15", "3.15", VERSION)):
            python_baseline.check_minimum(self.root)
        with self.assertRaisesRegex(ValueError, "published registry dependency"):
            check_release_dependencies(self.root)
        self.assertEqual(self.snapshot(), before)
        self.network.assert_not_called()

    def test_installed_cli_rejects_release_pin_without_mutating_inputs(self):
        self.git()
        before = self.snapshot()
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", "-m", "research_repo_tools", "--root", str(self.root), "release", "dependencies"],
            cwd=self.root,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("published registry dependency", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def normal_environment(self, environment):
        # A fresh native venv exercises Windows Scripts and POSIX bin paths,
        # isolated UTF-8 transport and Unicode parent directories without Git.
        venv.EnvBuilder(with_pip=False, symlinks=sys.platform != "win32").create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        output = subprocess.run(
            [str(python), "-I", "-X", "utf8", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], check=True, capture_output=True, encoding="utf-8"
        )
        site = Path(output.stdout.strip())
        for name, data in self.contents.items():
            path = site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        # Preserve the real command transport only for the metadata-only probe.
        original_run = subprocess.run

        def execute(command, args, **kwargs):
            if Path(command).stem == "uv":
                return subprocess.CompletedProcess([], 0, "", "")
            return original_run([command, *args], cwd=kwargs["cwd"], check=True, capture_output=True, encoding="utf-8")

        self.command.side_effect = execute
        return site

    def test_normal_environment_is_observed_even_when_python_symlinks_match(self):
        site = self.normal_environment(self.root / ".venv")
        (site / "research_repo_tools/__init__.py").write_bytes(b"# different normal environment, identical version\n")
        with self.assertRaisesRegex(ValueError, "runtime bytes differ"):
            check_release_dependencies(self.root)

    def test_observation_never_executes_consumer_environment_startup(self):
        site = self.normal_environment(self.root / ".venv")
        marker = self.root / "executed.txt"
        (site / "consumer.pth").write_bytes(f"import pathlib; pathlib.Path({str(marker)!r}).write_bytes(b'executed')\n".encode())
        check_release_dependencies(self.root)
        self.assertFalse(marker.exists(), "release observation executed consumer startup code")

    def test_one_check_reuses_matching_wheel_bytes_but_a_later_check_reacquires(self):
        self.normal_environment(self.root / ".venv")
        with patch.object(registry._OPENER, "open", side_effect=lambda *_args, **_kwargs: Response(self.wheel)) as download:
            check_release_dependencies(self.root)
            self.assertEqual(download.call_count, 1)
            check_release_dependencies(self.root)
            self.assertEqual(download.call_count, 2)

    def test_custom_uv_environment_cannot_hide_same_version_drift(self):
        environment = self.root / "custom env"
        site = self.normal_environment(environment)
        (site / "research_repo_tools/__init__.py").write_bytes(b"# unreleased normal environment\n")
        with patch.dict(os.environ, {"UV_PROJECT_ENVIRONMENT": str(environment)}):
            with self.assertRaisesRegex(ValueError, "runtime bytes differ"):
                check_release_dependencies(self.root)

    def test_git_lock_query_rejects_unrecognized_blank_fields(self):
        self.git()
        lock = self.root / "uv.lock"
        lock.write_bytes(lock.read_bytes().replace(b"#", b"&branch=#"))
        with self.assertRaisesRegex(ValueError, "locked Git URL"):
            check_development_dependency(self.root)

    def test_malformed_project_name_uses_the_public_error_contract(self):
        (self.root / "pyproject.toml").write_bytes(self.manifest.replace('name="consumer"', "name=7").encode())
        with self.assertRaisesRegex(ValueError, "project.name"):
            check_release_dependencies(self.root)

    def test_lock_check_uses_the_observed_root_and_trusted_interpreter(self):
        # A real uv check must neither follow inherited project selectors nor
        # execute the consumer environment's startup hooks during discovery.
        site = self.normal_environment(self.root / ".venv")
        (self.root / "pyproject.toml").write_bytes(b'[project]\nname="consumer"\nversion="1.0.0"\nrequires-python=">=3.15"\n')
        (self.root / "uv.lock").unlink()
        subprocess.run(
            ["uv", "lock", "--offline", "--no-python-downloads", "--python", sys.executable],
            cwd=self.root,
            check=True,
            capture_output=True,
            timeout=30,
        )
        marker = self.root / "startup.txt"
        (site / "consumer.pth").write_bytes(f"import pathlib; pathlib.Path({str(marker)!r}).write_bytes(b'executed')\n".encode())
        before = self.snapshot()

        def execute(command, args, **kwargs):
            return subprocess.run([str(command), *args], **kwargs, check=True, capture_output=True, encoding="utf-8")

        self.command.side_effect = execute
        with patch.dict(os.environ, {"UV_PROJECT": str(self.root / "missing"), "UV_WORKING_DIR": str(self.root / "missing"), "UV_FROZEN": "true"}):
            dependency_provenance._check_lock(self.root)
        self.assertFalse(marker.exists())
        self.assertEqual(self.snapshot(), before)

    def test_registry_byte_sizes_must_be_integers(self):
        self.inventory["urls"][0]["size"] = float(len(self.wheel))
        with self.assertRaisesRegex(ValueError, "differs from PyPI"):
            check_release_dependencies(self.root)


if __name__ == "__main__":
    unittest.main()
