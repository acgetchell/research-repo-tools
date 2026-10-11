"""Installed publication/configuration/CLI contracts with inert synthetic evidence."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from research_repo_tools import changelog, cli, config, registry, toolchain, toolchain_config
from research_repo_tools.release_assets import GitHubRelease
from research_repo_tools.release_publishing import RequiredCheck, check_metadata, publish_reviewed_release, require_checks, validate_event, verify_publication


class TestPublishingConsumer(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="published consumer café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def settings(self, registry_name):
        return config.parse(
            {
                "publishing": {
                    "registry": registry_name,
                    "package": "sample",
                    "repository": "example/project",
                    "required-checks": [{"name": "native", "app-id": 15368}],
                }
            },
            root=self.root,
        )

    def test_python_and_rust_final_metadata_and_cli_contract(self):
        for registry_name in ["pypi", "crates-io"]:
            with self.subTest(registry=registry_name):
                manifest = self.root / ("pyproject.toml" if registry_name == "pypi" else "Cargo.toml")
                manifest.write_text(
                    f'[{"project" if registry_name == "pypi" else "package"}]\nname="sample"\nversion="1.2.3"\n', encoding="utf-8", newline="\n"
                )
                (self.root / "CHANGELOG.md").write_bytes(b"# Changelog\r\n\r\n## [1.2.3] - 2026-09-01\r\n\r\n- Reviewed.\r\n")
                settings = self.settings(registry_name)
                before = manifest.read_bytes()
                check_metadata(settings, "v1.2.3")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.run(cli.parser().parse_args(["release", "check", "v1.2.3"]), settings), 0)
                    self.assertEqual(cli.run(cli.parser().parse_args(["release", "check", "v1.2.3", "--previous-release", "v1.2.2"]), settings), 0)
                with self.assertRaisesRegex(ValueError, "metadata validation failed"), contextlib.redirect_stderr(io.StringIO()):
                    cli.run(cli.parser().parse_args(["release", "check", "v1.2.3", "--previous-release", "v1.2.3"]), settings)
                with self.assertRaises(ValueError):
                    check_metadata(settings, "v1.2.4")
                self.assertEqual(manifest.read_bytes(), before)
                manifest.unlink()

    def test_release_event_and_paginated_exact_commit_evidence(self):
        sha = "a" * 40
        event = {
            "action": "published",
            "repository": {"full_name": "example/project"},
            "release": {"id": 9, "tag_name": "v1.2.3", "draft": False, "prerelease": False},
        }
        self.assertEqual(validate_event(event, "example/project", sha, "refs/tags/v1.2.3").commit, sha)
        event["release"]["prerelease"] = True
        with self.assertRaises(ValueError):
            validate_event(event, "example/project", sha, "refs/tags/v1.2.3")
        checks = (RequiredCheck("native", 15368),)
        run: dict[str, object] = {"id": 7, "name": "native", "app": {"id": 15368}, "head_sha": sha, "status": "completed", "conclusion": "success"}
        require_checks([{"total_count": 1, "check_runs": [run]}], sha, checks)
        run["conclusion"] = "failure"
        with self.assertRaises(ValueError):
            require_checks([{"total_count": 1, "check_runs": [run]}], sha, checks)
        run["conclusion"] = "success"
        for provider, required_id in [(15368.0, 15368), (True, 1)]:
            with self.subTest(provider=provider):
                run["app"] = {"id": provider}
                with self.assertRaises(ValueError):
                    require_checks([{"total_count": 1, "check_runs": [run]}], sha, (RequiredCheck("native", required_id),))

    def test_registry_rejects_ambiguous_nonfinite_and_deep_json(self):
        class Response(io.BytesIO):
            status = 200
            url = "https://crates.io/api/v1/crates/sample/1.2.3"

        version = b'"version":{"crate":"sample","num":"1.2.3","yanked":false,"checksum":"' + b"a" * 64 + b'"}'
        depth = sys.getrecursionlimit() + 100
        for extra in [b'"version":{},', b'"extra":NaN,', b'"extra":' + b"[" * depth + b"0" + b"]" * depth + b","]:
            with self.subTest(prefix=extra[:20]):

                def response(request, **kwargs):
                    self.assertEqual(request.full_url, Response.url)
                    return Response(b"{" + extra + version + b"}")

                with patch("urllib.request.urlopen", response):
                    with self.assertRaises(registry.RegistryLookupError):
                        registry.lookup_version("crates-io", "sample", "1.2.3")

    def test_cli_gate_rejects_ambiguous_event_before_remote_checks(self):
        policy = self.root / "research.toml"
        policy.write_text(
            '[publishing]\nregistry="pypi"\npackage="sample"\nrepository="example/project"\nrequired-checks=[{name="native",app-id=15368}]\n',
            encoding="utf-8",
            newline="\n",
        )
        event = self.root / "event.json"
        event.write_bytes(
            b'{"action":"published","repository":{"full_name":"example/project"},'
            b'"release":{"id":9,"tag_name":"v1.2.3","draft":true,"draft":false,"prerelease":false}}'
        )
        environment = {
            "GITHUB_EVENT_NAME": "release",
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_SHA": "a" * 40,
            "GITHUB_REF": "refs/tags/v1.2.3",
        }
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, environment), patch("research_repo_tools.release_publishing.check_reviewed_release") as gate:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(["--config", str(policy), "release", "gate", "v1.2.3"]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("duplicate JSON field", stderr.getvalue())
        gate.assert_not_called()

    def test_changed_draft_blocks_mutation_after_final_recheck(self):
        draft = GitHubRelease("example/project", "v1.2.3", 9, True, False, False, ())
        changed = GitHubRelease("example/project", "v1.2.3", 9, True, True, False, ())
        with (
            patch("research_repo_tools.release_publishing.check_reviewed_release", side_effect=[draft, changed]),
            patch("research_repo_tools.release_publishing.run_git_command", return_value=subprocess.CompletedProcess([], 0, "a" * 40, "")),
            patch("research_repo_tools.release_assets.run_command_bytes") as command,
        ):
            with self.assertRaisesRegex(ValueError, "mutable stable draft"):
                publish_reviewed_release(self.settings("crates-io"), "v1.2.3")
        command.assert_not_called()

    def test_exact_registry_http_errors_and_post_upload_timeout(self):
        def absent(request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 404, "missing", Message(), None)

        with patch("urllib.request.urlopen", absent):
            self.assertFalse(registry.lookup_version("crates-io", "sample", "1.2.3").present)

        def unavailable(request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 429, "rate limited", Message(), None)

        with patch("urllib.request.urlopen", unavailable):
            with self.assertRaises(registry.RegistryLookupError):
                registry.lookup_version("crates-io", "sample", "1.2.3")
        release = GitHubRelease("example/project", "v1.2.3", 9, False, False, False, ())
        absent_result = registry.RegistryVersion("crates-io", "sample", "1.2.3", False)
        visible = registry.RegistryVersion("crates-io", "sample", "1.2.3", True)
        with patch("research_repo_tools.release_publishing.lookup_release", return_value=release), patch("research_repo_tools.registry.time.sleep"):
            with patch("research_repo_tools.registry.lookup_version", side_effect=[absent_result, visible]):
                verify_publication(self.settings("crates-io"), "v1.2.3", attempts=2, interval=0)
            with patch("research_repo_tools.registry.lookup_version", return_value=absent_result):
                with self.assertRaisesRegex(ValueError, "not yet visible"):
                    verify_publication(self.settings("crates-io"), "v1.2.3", attempts=2, interval=0)

    def test_registry_closes_http_error_responses(self):
        for code in [404, 429]:
            with self.subTest(code=code):
                body = io.BytesIO(b"registry response")
                error = urllib.error.HTTPError("https://crates.io/api/v1/crates/sample/1.2.3", code, "unavailable", Message(), body)

                def failed(*args, **kwargs):
                    raise error

                with patch("urllib.request.urlopen", failed):
                    if code == 404:
                        self.assertFalse(registry.lookup_version("crates-io", "sample", "1.2.3").present)
                    else:
                        with self.assertRaises(registry.RegistryLookupError):
                            registry.lookup_version("crates-io", "sample", "1.2.3")
                self.assertTrue(body.closed)

    def test_installed_resources_and_bounded_dependency_policy(self):
        for name in ["RELEASING.md", "publish-crates.yml", "publishing.toml"]:
            rendered = changelog.template(name, owner="example", repository="project")
            self.assertNotIn("__OWNER__", rendered)
            self.assertTrue(rendered.strip())
        for policy, condition in [("concise", 'commit.body and group != "Dependencies"'), ("preserve", "commit.body")]:
            rendered = changelog.template("cliff.toml", dependency_bodies=policy)
            self.assertIn("{%", tomllib.loads(rendered)["changelog"]["body"])
            self.assertIn(f"if {condition}", rendered)
        with self.assertRaises(ValueError):
            config.parse({"changelog": {"dependency-bodies": "deps-dev"}}, root=self.root)

    def test_installed_cli_generation_with_real_external_generator(self):
        # Synthetic git-cliff context avoids creating Git objects or refs. Hosted
        # changelog CI supplies the external generator; base installations need
        # no Cargo executable to exercise the other public contracts above.
        if shutil.which("git-cliff") is None:
            self.skipTest("external git-cliff is required for generation")
        body = (
            "Ruff [notes](https://github.com/astral-sh/ruff/releases/tag/0.16.9).\n"
            "Ty [comparison](https://github.com/astral-sh/ty/compare/0.0.83...0.0.84).\n"
            "setuptools [changelog](https://setuptools.pypa.io/en/latest/history.html).\n\n"
            "Use `Vec<T>`.\n\n```rust\nfn value<T>() -> Result<T, E>;\n```"
        )
        context = [
            {
                "version": "v1.2.3",
                "timestamp": 0,
                "previous": None,
                "submodule_commits": {},
                "commits": [
                    {
                        "id": "a" * 40,
                        "message": "bump authored tools",
                        "group": "Dependencies",
                        "links": [],
                        "footers": [],
                        "author": {"name": None, "email": None, "timestamp": 0},
                        "committer": {"name": None, "email": None, "timestamp": 0},
                        "conventional": True,
                        "merge_commit": False,
                        **{
                            host: {
                                "username": None,
                                "pr_author": None,
                                "pr_title": None,
                                "pr_number": None,
                                "pr_numbers": [],
                                "pr_labels": [],
                                "is_first_time": False,
                            }
                            for host in ("github", "gitlab", "gitea", "bitbucket", "azure_devops")
                        },
                        "scope": "deps-dev",
                        "body": body,
                        "raw_message": f"chore(deps-dev)!: bump authored tools\n\n{body}\n\nBREAKING CHANGE: Return `Result<T>`.\nMigrate explicitly.",
                        "breaking": True,
                        "breaking_description": "Return `Result<T>`.\nMigrate explicitly.",
                    },
                ],
                **{host: {"contributors": []} for host in ("github", "gitlab", "gitea", "bitbucket", "azure_devops")},
            }
        ]
        source = self.root / "context.json"
        source.write_text(json.dumps(context), encoding="utf-8", newline="\n")
        (self.root / "research.toml").write_text(
            '[changelog]\nowner="example"\nrepository="project"\ndependency-bodies="preserve"\n', encoding="utf-8", newline="\n"
        )

        def generate(program, args, **kwargs):
            # Use the real rendered configuration but a fixture history producer.
            return subprocess.run(
                [program, "--config", args[1], "--offline", "--no-exec", "--from-context", str(source)],
                cwd=self.root,
                capture_output=True,
                encoding="utf-8",
                check=True,
                timeout=30,
            )

        args = ["--config", str(self.root / "research.toml"), "changelog", "generate", "--tag", "v1.2.3", "--date", "2026-09-01"]
        with patch("research_repo_tools.changelog.run_safe_command", generate):
            self.assertEqual(cli.main(args), 0)
            before = (self.root / "CHANGELOG.md").read_bytes()
            self.assertEqual(cli.main(args), 0)
        result = before.decode("utf-8")
        self.assertIn("## [1.2.3] - 2026-09-01", result)
        self.assertIn("### Dependencies", result)
        self.assertIn("Ruff [notes]", result)
        self.assertIn("Ty [comparison]", result)
        self.assertIn("setuptools [changelog]", result)
        self.assertIn("Return `Result<T>`.\n  Migrate explicitly.", result)
        self.assertIn("fn value<T>() -> Result<T, E>;", result)
        self.assertEqual((self.root / "CHANGELOG.md").read_bytes(), before)

    def test_managed_cargo_home_preserves_oidc_token_and_failed_publish_status(self):
        plan = toolchain_config.Toolchain(self.root, "0.12.23", "3.14", toolchain_config.RustToolchain("1.98.0", (), (), "minimal"), ())
        runtime = toolchain.Runtime(plan)
        user_home = self.root / "user cargo"
        status = toolchain.Status("uv", "0.12.23", "0.12.23", sys.executable, True)
        with (
            patch.dict(os.environ, {"CARGO_HOME": str(user_home), "CARGO_REGISTRY_TOKEN": "inert temporary token"}),
            patch.object(runtime, "uv_status", return_value=status),
        ):
            environment = runtime._environment()
            self.assertEqual(environment["CARGO_HOME"], str(runtime.manager / "cargo"))
            self.assertNotEqual(environment["CARGO_HOME"], str(user_home))
            self.assertEqual(environment["CARGO_REGISTRY_TOKEN"], "inert temporary token")
            with (
                patch.object(runtime, "inspect", return_value=[]),
                patch.object(runtime, "environment", return_value=environment),
                patch("research_repo_tools.toolchain.resolve_executable", return_value=Path(sys.executable)),
                patch("research_repo_tools.toolchain.run_safe_command", return_value=subprocess.CompletedProcess([], 101)) as command,
            ):
                self.assertEqual(toolchain.run_command(runtime, ["cargo", "publish", "--locked", "--registry", "crates-io"]), 101)
                self.assertEqual(command.call_args.kwargs["env"]["CARGO_REGISTRY_TOKEN"], "inert temporary token")


if __name__ == "__main__":
    unittest.main()
