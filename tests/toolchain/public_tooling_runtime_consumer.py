"""Deterministic source selection, availability and launch contracts."""

import ast
import contextlib
import http.client
import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.response
from email.message import Message
from pathlib import Path
from typing import Any, override
from unittest.mock import patch

from research_repo_tools import tooling_runtime as runtime
from research_repo_tools.changelog import template

SHA = "a" * 40
VERSION = "9.8.7"
REPOSITORY = "example/tooling"


class Response(io.BytesIO):
    status = 200

    def __init__(self, url, data):
        super().__init__(data)
        self.url = url


class RedirectResponse(urllib.response.addinfourl):
    msg = "Found"


class TestToolingRuntime(unittest.TestCase):
    @override
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tooling source café ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "pyproject.toml"
        self.original = b'[project]\nname="unchanged-consumer"\nversion="1.0.0"\n'
        self.manifest.write_bytes(self.original)
        self.release: dict[str, Any] = {
            "info": {"name": runtime.PACKAGE, "version": VERSION, "requires_python": ">=3.15,<4"},
            "urls": [{"url": "https://files.pythonhosted.org/packages/fixture.whl", "size": 42, "yanked": False, "packagetype": "bdist_wheel"}],
        }
        self.responses = {
            f"https://pypi.org/pypi/{runtime.PACKAGE}/{VERSION}/json": json.dumps(self.release).encode(),
            f"https://api.github.com/repos/{REPOSITORY}/git/commits/{SHA}": json.dumps({"sha": SHA}).encode(),
            f"https://raw.githubusercontent.com/{REPOSITORY}/{SHA}/pyproject.toml": b'[project]\nname="research-repo-tools"\nversion="9.8.7"\nrequires-python=">=3.15"\n',
        }
        self.urls = []

        def open_request(request, **kwargs):
            self.assertEqual(kwargs, {"timeout": 30})
            url = request.full_url
            self.urls.append(url)
            if url not in self.responses:
                raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)
            return Response(url, self.responses[url])

        self.http_open = runtime.urllib.request.OpenerDirector.open
        self.opener = self.enterContext(patch.object(runtime.urllib.request.OpenerDirector, "open", side_effect=open_request))
        self.launch = self.enterContext(patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)))

    @override
    def tearDown(self):
        self.assertEqual(self.manifest.read_bytes(), self.original)
        self.assertEqual(list(self.root.iterdir()), [self.manifest])

    def test_release_is_verified_before_launch_and_ambient_sources_cannot_replace_it(self):
        source = runtime.parse_source(release=VERSION)
        self.assertIsInstance(source, runtime.ReleasePin)
        with patch.dict(os.environ, {"UV_FIND_LINKS": "unpublished", "UV_INDEX": "https://private.invalid", "UV_PROJECT": "elsewhere"}):
            self.assertEqual(runtime.run_tooling(source, ("release", "dependencies"), root=self.root, release_intent=True), 7)
        self.assertEqual(self.urls, [f"https://pypi.org/pypi/{runtime.PACKAGE}/{VERSION}/json"])
        arguments = self.launch.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--from") + 1], "research-repo-tools==9.8.7")
        self.assertEqual(arguments[-4:], ["--root", str(self.root.resolve()), "release", "dependencies"])
        environment = self.launch.call_args.kwargs["env"]
        self.assertNotIn("UV_FIND_LINKS", environment)
        self.assertNotIn("UV_INDEX", environment)
        self.assertNotIn("UV_PROJECT", environment)

    def test_existing_commit_selects_exact_git_requirement_and_declared_python(self):
        source = runtime.parse_source(github=REPOSITORY, commit=SHA)
        self.assertIsInstance(source, runtime.GitHubPin)
        self.assertEqual(runtime.run_tooling(source, ("toolchain", "check"), root=self.root), 7)
        self.assertEqual(len(self.urls), 2)
        arguments = self.launch.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--from") + 1], f"research-repo-tools @ git+https://github.com/{REPOSITORY}.git@{SHA}")
        self.assertEqual(arguments[arguments.index("--python") + 1], ">=3.15")

    def test_optional_extras_preserve_the_exact_source_selection(self):
        for source, requirement in (
            (runtime.ReleasePin(VERSION), f"research-repo-tools[python-tools]=={VERSION}"),
            (runtime.GitHubPin(REPOSITORY, SHA), f"research-repo-tools[python-tools] @ git+https://github.com/{REPOSITORY}.git@{SHA}"),
        ):
            with self.subTest(source=source):
                runtime.run_tooling(source, ("toolchain", "check"), root=self.root, extras=("python-tools",))
                arguments = self.launch.call_args.args[0]
                self.assertEqual(arguments[arguments.index("--from") + 1], requirement)

    def test_optional_github_credentials_are_sent_only_to_the_api(self):
        for primary, fallback, expected in (
            ("primary-fixture", "fallback-fixture", "Bearer primary-fixture"),
            ("", "fallback-fixture", "Bearer fallback-fixture"),
            ("", "", None),
        ):
            with self.subTest(primary=primary, fallback=fallback), patch.dict(os.environ, {"GITHUB_TOKEN": primary, "GH_TOKEN": fallback}):
                self.opener.reset_mock()
                for source in (runtime.GitHubPin(REPOSITORY, SHA), runtime.ReleasePin(VERSION)):
                    self.assertEqual(runtime.run_tooling(source, ("toolchain", "check"), root=self.root), 7)
                requests = [call.args[0] for call in self.opener.call_args_list]
                self.assertEqual(len(requests), 3)
                self.assertEqual(requests[0].full_url, f"https://api.github.com/repos/{REPOSITORY}/git/commits/{SHA}")
                self.assertEqual(requests[0].get_header("Authorization"), expected)
                self.assertIsNone(requests[1].get_header("Authorization"))
                self.assertIsNone(requests[2].get_header("Authorization"))

    def test_invalid_arguments_and_extras_fail_before_lookup(self):
        for arguments, extras in (((), ()), (("check\x00",), ()), (("check",), ("python-tools", "python-tools")), (("check",), ("x] @ bad",))):
            with self.subTest(arguments=arguments, extras=extras), self.assertRaises(ValueError):
                runtime.run_tooling(runtime.ReleasePin(VERSION), arguments, root=self.root, extras=extras)
        self.opener.assert_not_called()
        self.launch.assert_not_called()

    def test_authenticated_redirect_is_rejected_before_contacting_a_second_host(self):
        headers = Message()
        headers["Location"] = "https://other.example.invalid/collect"
        url = f"https://api.github.com/repos/{REPOSITORY}/git/commits/{SHA}"
        response = RedirectResponse(io.BytesIO(b""), headers, url, 302)
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "fixture-token"}),
            patch.object(runtime.urllib.request.OpenerDirector, "open", new=self.http_open),
            patch.object(runtime.urllib.request.HTTPSHandler, "https_open", return_value=response) as transport,
            self.assertRaisesRegex(ValueError, "HTTP 302"),
        ):
            runtime.run_tooling(runtime.GitHubPin(REPOSITORY, SHA), ("toolchain", "check"), root=self.root)
        transport.assert_called_once()
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, url)
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture-token")
        self.assertTrue(response.closed)
        self.launch.assert_not_called()

    def test_nonexistent_version_and_commit_never_launch_or_fall_back(self):
        for source in (runtime.ReleasePin("9.8.8"), runtime.GitHubPin(REPOSITORY, "b" * 40)):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "does not exist.*HTTP 404"):
                runtime.run_tooling(source, ("toolchain", "check"), root=self.root)
        self.assertEqual(len(self.urls), 2)
        self.launch.assert_not_called()

    def test_git_release_intent_fails_before_lookup(self):
        with self.assertRaisesRegex(ValueError, "development-only"):
            runtime.run_tooling(runtime.GitHubPin(REPOSITORY, SHA), ("release", "gate", "v1.0.0"), root=self.root, release_intent=True)
        self.opener.assert_not_called()
        self.launch.assert_not_called()

    def test_incomplete_floating_and_competing_sources_fail_at_parse(self):
        cases = (
            {},
            {"release": ">=9.8.7"},
            {"release": "latest"},
            {"release": ""},
            {"github": REPOSITORY},
            {"commit": SHA},
            {"release": VERSION, "github": REPOSITORY, "commit": SHA},
            {"github": REPOSITORY, "commit": "main"},
            {"github": REPOSITORY, "commit": SHA[:7]},
            {"github": REPOSITORY, "commit": SHA.upper()},
            {"github": "https://github.com/" + REPOSITORY, "commit": SHA},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                runtime.parse_source(**values)
        self.opener.assert_not_called()
        self.launch.assert_not_called()

    def test_missing_yanked_and_malformed_releases_never_launch(self):
        url = f"https://pypi.org/pypi/{runtime.PACKAGE}/{VERSION}/json"
        for document in (
            {},
            {"info": self.release["info"], "urls": []},
            {"info": self.release["info"], "urls": [{**self.release["urls"][0], "yanked": True}]},
            {"info": {**self.release["info"], "version": "9.8.6"}, "urls": self.release["urls"]},
            {"info": {**self.release["info"], "requires_python": "--python evil"}, "urls": self.release["urls"]},
            {"info": self.release["info"], "urls": [{**self.release["urls"][0], "size": True}]},
        ):
            self.responses[url] = json.dumps(document).encode()
            with self.subTest(document=document), self.assertRaises(ValueError):
                runtime.run_tooling(runtime.ReleasePin(VERSION), ("toolchain", "check"), root=self.root)
        self.launch.assert_not_called()

    def test_unverified_or_wrong_project_commit_never_launches(self):
        commit_url = f"https://api.github.com/repos/{REPOSITORY}/git/commits/{SHA}"
        manifest_url = f"https://raw.githubusercontent.com/{REPOSITORY}/{SHA}/pyproject.toml"
        for url, data in (
            (commit_url, b'{"sha":"wrong"}'),
            (commit_url, b'{"sha":"a","sha":"b"}'),
            (manifest_url, b'[project]\nname="different-package"\n'),
            (manifest_url, b"malformed TOML"),
        ):
            original = self.responses[url]
            self.responses[url] = data
            with self.subTest(url=url, data=data), self.assertRaises(ValueError):
                runtime.run_tooling(runtime.GitHubPin(REPOSITORY, SHA), ("toolchain", "check"), root=self.root)
            self.responses[url] = original
        self.launch.assert_not_called()

    def test_unavailable_lookup_never_launches(self):
        with patch.object(runtime.urllib.request.OpenerDirector, "open", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(ValueError, "lookup failed"):
                runtime.run_tooling(runtime.ReleasePin(VERSION), ("toolchain", "check"), root=self.root)
        self.launch.assert_not_called()

    def test_truncated_lookup_is_a_clean_cli_failure_without_launch(self):
        with patch.object(Response, "read", side_effect=http.client.IncompleteRead(b"partial", 42)), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(runtime.main(["--version", VERSION, "--root", str(self.root), "--arguments-json", '["toolchain","check"]']), 1)
        self.assertIn("lookup failed", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.launch.assert_not_called()

    def test_cli_json_arguments_are_literal_and_missing_sources_are_clean_errors(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(runtime.main(["--version", "9.8.8", "--root", str(self.root), "--arguments-json", '["toolchain","check"]']), 1)
        self.assertIn("does not exist", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.launch.assert_not_called()
        literal = "$(touch should-not-exist); 'quoted'"
        self.assertEqual(
            runtime.main(["--version", VERSION, "--root", str(self.root), "--arguments-json", json.dumps(["toolchain", "run", "--", "echo", literal])]), 7
        )
        self.assertEqual(self.launch.call_args.args[0][-1], literal)

    def test_packaged_bootstrap_is_the_single_implementation_with_old_python_syntax(self):
        exported = template("python-bootstrap.py")
        self.assertEqual(exported, Path(runtime.__file__).read_text(encoding="utf-8"))
        ast.parse(exported, feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
