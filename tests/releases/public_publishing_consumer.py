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
from typing import Any, cast, override
from unittest.mock import patch

from research_repo_tools import changelog, cli, config, registry, release_metadata, toolchain, toolchain_config, update_release
from research_repo_tools.just_inspect import dry_run, inspect_justfile
from research_repo_tools.release_assets import GitHubRelease
from research_repo_tools.release_publishing import RequiredCheck, check_metadata, publish_reviewed_release, require_checks, validate_event, verify_publication


class TestPublishingConsumer(unittest.TestCase):
    def test_all_unmet_checks_have_actionable_exact_commit_diagnostics(self):
        sha = "a" * 40
        checks = tuple(RequiredCheck(name, 15368) for name in ("running", "queued", "failed", "cancelled", "missing", "ambiguous", "wrong-provider", "passed"))
        runs = []
        for name, status, conclusion, provider in (
            ("running", "in_progress", None, 15368),
            ("queued", "queued", None, 15368),
            ("failed", "completed", "failure", 15368),
            ("cancelled", "completed", "cancelled", 15368),
            ("ambiguous", "completed", "success", 15368),
            ("ambiguous", "completed", "success", 15368),
            ("wrong-provider", "completed", "success", 27),
            ("wrong-provider", "queued", None, 28),
            ("passed", "completed", "success", 15368),
        ):
            identifier = len(runs) + 1
            runs.append(
                {
                    "id": identifier,
                    "name": name,
                    "app": {"id": provider},
                    "head_sha": sha,
                    "status": status,
                    "conclusion": conclusion,
                    "html_url": f"https://github.com/example/project/runs/{identifier}?token=secret",
                }
            )
        with self.assertRaises(ValueError) as caught:
            require_checks([{"total_count": len(runs), "check_runs": runs}], sha, checks)
        message = str(caught.exception)
        self.assertIn(sha, message)
        for name in ("running", "queued", "failed", "cancelled", "missing", "ambiguous", "wrong-provider"):
            self.assertIn(f"required check '{name}' (app ID 15368)", message)
        self.assertIn("pending/running", message)
        self.assertIn("status='in_progress', conclusion=None", message)
        self.assertIn("conclusion='cancelled'", message)
        wrong_provider = next(line for line in message.splitlines() if "required check 'wrong-provider'" in line)
        self.assertIn("missing; same name observed from app IDs [27, 28]", wrong_provider)
        self.assertIn("check ID 7, status='completed', conclusion='success', https://github.com/example/project/runs/7", wrong_provider)
        self.assertIn("check ID 8, status='queued', conclusion=None, https://github.com/example/project/runs/8", wrong_provider)
        self.assertIn("ambiguous (2 results)", message)
        self.assertIn("https://github.com/example/project/runs/1", message)
        self.assertIn("Wait for the existing run to succeed, then manually retry", message)
        self.assertNotIn("secret", message)
        self.assertNotIn("required check 'passed'", message)

    @override
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

    def test_normalize_interrupt_restores_original_changelog(self):
        path = self.root.resolve() / "CHANGELOG.md"
        original = b"# Changelog\r\n\r\n- Existing entry\r\n\r\n"
        path.write_bytes(original)
        replace = Path.replace
        interrupt = KeyboardInterrupt("after changelog replacement")

        def interrupted_replace(source, destination):
            result = replace(source, destination)
            if source.suffix == ".tmp" and Path(destination) == path:
                raise interrupt
            return result

        with patch.object(Path, "replace", interrupted_replace), self.assertRaises(KeyboardInterrupt) as caught:
            cli.main(["--root", str(self.root), "changelog", "normalize"])
        self.assertIs(caught.exception, interrupt)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(set(self.root.iterdir()), {self.root / path.name})

    def test_installed_sync_changelog_date_preserves_unicode_prose_and_exact_bytes(self):
        (self.root / "pyproject.toml").write_text('[project]\nname="sample"\nversion="1.2.3"\n', encoding="utf-8", newline="\n")
        (self.root / "CITATION.cff").write_text("version: 1.2.3\ndate-released: 2026-09-07\n", encoding="utf-8", newline="\n")
        path = self.root / "CHANGELOG.md"
        source = (
            "# Changelog\n\nPreserve pre\u2028amble.\n\n## [Unreleased]\n\n- Earlier body\x85text\u2029continues.\n\n"
            "## [1.2.3](https://example.org/v1.2.3) - 2000-01-01\n\n- Target notes."
        )
        for newline in ("\n", "\r\n"):
            for final_newline in (False, True):
                with self.subTest(newline=newline, final_newline=final_newline):
                    text = (source + ("\n" if final_newline else "")).replace("\n", newline)
                    path.write_bytes(text.encode("utf-8"))
                    before = {item: item.read_bytes() for item in self.root.iterdir()}
                    before[path] = text.replace(" - 2000-01-01", " - 2026-09-07").encode("utf-8")
                    args = ["v1.2.3", "--repo-root", str(self.root), "--sync-changelog-date"]
                    self.assertEqual(update_release.main(args), 0)
                    self.assertEqual({item: item.read_bytes() for item in self.root.iterdir()}, before)
                    self.assertEqual(update_release.main(args), 0)
                    self.assertEqual({item: item.read_bytes() for item in self.root.iterdir()}, before)

    def test_installed_generation_applies_dates_after_unicode_separators(self):
        policy = self.root / "research.toml"
        policy.write_text('[changelog]\nowner="example"\nrepository="project"\n', encoding="utf-8", newline="\n")
        path = self.root / "CHANGELOG.md"
        original = "# Changelog\n\n## [1.2.2] - 2026-09-06\n\n- Previous.\n\n## [1.2.1] - 2026-09-05\n\n- First.\n"
        generated = (
            "# Changelog\n\nPreserve pre\u2028amble.\n\n## [1.2.3] - 2000-01-01\n\n"
            "- Latest body\x85text\u2029continues.\n\n## [1.2.2] - 2000-01-02\n\n- Previous.\n\n## [1.2.1]"
        )
        expected = generated.replace(" - 2000-01-01", " - 2026-09-07").replace(" - 2000-01-02", " - 2026-09-06") + " - 2026-09-05\n"
        args = ["--config", str(policy), "changelog", "generate", "--tag", "v1.2.3", "--date", "2026-09-07"]
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=newline):
                path.write_bytes(original.encode("utf-8"))
                with patch(
                    "research_repo_tools.changelog.run_safe_command", return_value=subprocess.CompletedProcess([], 0, generated.replace("\n", newline), "")
                ):
                    self.assertEqual(changelog.generate(config.load(policy), tag="v1.2.3", released="2026-09-07", dry_run=True), expected)
                    self.assertEqual(path.read_bytes(), original.encode("utf-8"))
                    self.assertEqual(cli.main(args), 0)
                    self.assertEqual(path.read_bytes(), expected.encode("utf-8"))
                    self.assertEqual(cli.main(["--config", str(policy), "changelog", "generate"]), 0)
                    self.assertEqual(path.read_bytes(), expected.encode("utf-8"))

    def test_installed_version_diagnostic_keeps_heading_text_after_unicode_separators(self):
        (self.root / "pyproject.toml").write_text('[project]\nname="sample"\nversion="1.2.3"\n', encoding="utf-8", newline="\n")
        path = self.root / "CHANGELOG.md"
        heading = "## [1.2.2] - 2026-09-06"
        source = f"# Changelog\n\nBefore\u2028heading.\n\n## [Unreleased]\n\n- Earlier\x85body\u2029text.\n\n{heading}\n"
        path.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
        mismatches = release_metadata.find_version_mismatches(self.root)
        self.assertEqual(len(mismatches), 1)
        reference = mismatches[0].reference
        self.assertEqual(reference.kind, release_metadata.ReferenceKind.CHANGELOG)
        self.assertEqual(reference.line, 9)
        self.assertEqual(reference.text, heading)

    def test_installed_normalization_preserves_literal_email_entities(self):
        path = self.root / "CHANGELOG.md"
        source = (
            "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n"
            "- Keep `&lt;single@example.com&gt;` and ``&lt;multi@example.com&gt;\nand `nested` &lt;other@example.com&gt;``.\n\n"
            "```text\nCo-Authored-By: Code &lt;fenced@example.com&gt;\n```\n\n"
            "| Literal |\n| --- |\n| &lt;table@example.com&gt; |\n\n"
            "Co-Authored-By: Dev &lt;author@example.com&gt;\n"
        )
        path.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
        expected = source.replace("&lt;author@example.com&gt;", "<author@example.com>").encode("utf-8")
        args = ["--root", str(self.root), "changelog", "normalize"]
        self.assertEqual(cli.main(args), 0)
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(cli.main(args), 0)
        self.assertEqual(path.read_bytes(), expected)

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

    def test_installed_release_recipes_use_prepared_metadata_before_tagging(self):
        (self.root / "justfile").write_text(changelog.template("justfile"), encoding="utf-8", newline="\n")
        recipes = inspect_justfile(self.root)
        self.assertFalse(recipes.aliases)
        for recipe in ["release-tag", "release-tag-preview", "release-update"]:
            self.assertNotIn(recipe, recipes.recipes)
        for recipe in ["tag", "tag-preview"]:
            with self.subTest(recipe=recipe):
                output = dry_run(self.root, recipe, ["v1.2.3"]).stderr
                self.assertLess(output.index("release check --final-release"), output.index("changelog tag"))
                self.assertEqual("--dry-run" in output, recipe == "tag-preview")
        for arguments in [["v1.2.3"], ["v1.2.3", "--previous-release", "v1.2.2", "--date", "2026-09-01", "--dry-run"]]:
            with self.subTest(arguments=arguments):
                self.assertIn('release update "$@"', dry_run(self.root, "update-version", arguments).stderr)
        self.assertIn('release verify --attempts 7 --interval 10 "$@"', dry_run(self.root, "release-verify", ["v1.2.3"]).stderr)

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
        context: list[dict[str, Any]] = [
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
        description = (
            "The source fields in\n"
            "ConversionError::Failed, QualityError::EdgeLength,\n"
            "RandomError::Vector and\n"
            "AngleError::Failed now contain Box<LaError>.\n"
            "Dereference the boxed source or downcast Error::source() to Box<LaError>.\n"
            "Existing From<LaError> conversions remain available."
        )
        # Real git-cliff parsing is covered by the native template suite. Keep
        # its split-trailer representation here to exercise the packaged
        # template through the installed CLI without requiring a Git checkout.
        commits = cast("list[dict[str, Any]]", context[0]["commits"])
        commits.append(
            {
                **context[0]["commits"][0],
                "id": "b" * 40,
                "message": "box error sources",
                "body": None,
                "raw_message": f"refactor!: box error sources\n\nBREAKING CHANGE: {description}\n\nRefs #42",
                "breaking_description": "The source fields in",
                "footers": [
                    {"token": "BREAKING CHANGE", "separator": ":", "value": "The source fields in", "breaking": True},
                    {"token": "ConversionError", "separator": ":", "value": ":Failed, QualityError::EdgeLength,", "breaking": False},
                    {"token": "RandomError", "separator": ":", "value": ":Vector and", "breaking": False},
                    {
                        "token": "AngleError",
                        "separator": ":",
                        "value": ":Failed now contain Box<LaError>.\nDereference the boxed source or downcast Error::source() to Box<LaError>.\n"
                        "Existing From<LaError> conversions remain available.",
                        "breaking": False,
                    },
                    {"token": "Refs", "separator": " #", "value": "42", "breaking": False},
                ],
            }
        )
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
        summary = result.split("### ⚠️ Breaking Changes\n\n", 1)[1].split("\n### ", 1)[0]
        expected = description.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "\n  ")
        self.assertIn("- " + expected, summary)
        self.assertNotIn("Refs", summary)
        self.assertIn("fn value<T>() -> Result<T, E>;", result)
        self.assertEqual((self.root / "CHANGELOG.md").read_bytes(), before)

        # Repeat with the citation-owned default through the same real generator.
        policy = self.root / "research.toml"
        policy.write_bytes(policy.read_bytes() + b'date-source="citation"\n')
        citation = self.root / "CITATION.cff"
        citation.write_bytes(b"version: 1.2.3\r\ndate-released: '2026-09-01'\r\n")
        snapshot = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("research_repo_tools.changelog.run_safe_command", generate), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args[:-2]), 0)
            self.assertEqual(cli.main(args[:-2]), 0)
            self.assertEqual(cli.main([*args[:-2], "--dry-run"]), 0)
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, snapshot)

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
