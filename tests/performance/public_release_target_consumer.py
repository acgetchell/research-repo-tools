"""Installed API/CLI release handoffs with synthetic GitHub data and inert bytes."""

import contextlib
import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, override
from unittest.mock import patch

from research_repo_tools import cli, config, release_assets
from research_repo_tools.release_assets import (
    ReleasePublicationUnknownError,
    ReleaseTarget,
    download_release_asset,
    parse_release_target,
    preflight_release_target,
    publish_release_asset,
    revalidate_release_target,
    serialize_release_target,
)
from research_repo_tools.release_publishing import publish_reviewed_release

REPOSITORY = "example/project"
TAG = "v1.2.3"
SHA = "a" * 40
PAYLOAD = b"inert\r\n\xff\x00baseline\n"


class GitHubFixture:
    def __init__(self):
        self.release: dict[str, Any] = {
            "url": f"https://api.github.com/repos/{REPOSITORY}/releases/9",
            "id": 9,
            "tag_name": TAG,
            "name": TAG,
            "draft": True,
            "immutable": False,
            "prerelease": False,
            "assets": [],
        }
        self.commit = SHA
        self.ref = f"refs/tags/{TAG}"
        self.annotation = False
        self.annotation_object = None
        self.ref_object = None
        self.tag_objects = {}
        self.calls = []
        self.uploaded = []
        self.published = 0
        self.on_upload = lambda: None
        self.on_download = lambda: None
        self.on_read = lambda: None
        self.publication_response = None
        self.upload_error = None
        self.releases = None
        self.asset_pages = None
        self.downloaded = PAYLOAD

    def asset(self):
        return {"id": 8, "name": "baseline.tar.gz", "state": "uploaded", "size": len(PAYLOAD), "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()}

    def run(self, program, args, **kwargs):
        assert program == "gh"
        self.calls.append(args)
        endpoint = f"repos/{REPOSITORY}/releases/9"
        if "POST" in args:
            assert args[3] == f"https://uploads.github.com/{endpoint}/assets?name=baseline.tar.gz"
            assert args[4:7] == ["--header", "Content-Type: application/octet-stream", "--input"]
            self.uploaded.append(Path(args[7]).read_bytes())
            if self.upload_error:
                raise self.upload_error
            self.release["assets"].append(self.asset())
            self.on_upload()
            value = self.asset()
        elif "PATCH" in args:
            assert args == ["api", "--method", "PATCH", endpoint, "--field", "draft=false"]
            self.published += 1
            self.release["draft"] = False
            value = self.release if self.publication_response is None else self.publication_response
            if isinstance(value, Exception):
                raise value
        elif args[0] == "release":
            value = {"databaseId": 9, "tagName": TAG}
        elif args[-1] == f"repos/{REPOSITORY}/releases?per_page=100":
            value = [[self.release]] if self.releases is None else self.releases
        elif args[-1] == endpoint:
            self.on_read()
            value = self.release
        elif args[-1] == f"{endpoint}/assets?per_page=100":
            value = [self.release["assets"]] if self.asset_pages is None else self.asset_pages
        elif args[-1] == f"repos/{REPOSITORY}/git/ref/tags/{TAG}":
            value = {
                "ref": self.ref,
                "object": self.ref_object
                if self.ref_object is not None
                else {"type": "tag" if self.annotation else "commit", "sha": "b" * 40 if self.annotation else self.commit},
            }
        elif args[-1] in self.tag_objects:
            value = self.tag_objects[args[-1]]
        elif args[-1] == f"repos/{REPOSITORY}/git/tags/" + "b" * 40:
            value = {"sha": "b" * 40, "object": {"type": "commit", "sha": self.commit}} if self.annotation_object is None else self.annotation_object
        else:
            raise AssertionError(f"unexpected API request: {args}")
        payload = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        return subprocess.CompletedProcess([program, *args], 0, payload, b"")

    def download(self, root, repository, identifier, limit):
        assert repository == REPOSITORY and identifier == 8
        self.on_download()
        return self.downloaded


class TestReleaseTargetConsumer(unittest.TestCase):
    def test_configured_asset_cli_observes_checkout_and_requires_captured_target(self):
        root = self.root.resolve()
        configuration = root / "assets.toml"
        configuration.write_bytes(b'schema=1\nrepository="example/project"\nexpected-title="tag"\n')
        destination = root / "target.json"
        output = root / "actions-output"
        output.write_bytes(b"")
        common = ["--root", str(root), "release", "assets"]
        assertions = ["assets.toml", "--checkout", str(root), "--commit", SHA, "--tag", TAG]
        with (
            patch("research_repo_tools.release_interfaces.resolve_revision", return_value=SHA),
            patch("research_repo_tools.release_interfaces.require_release_checkout") as observed,
            patch("research_repo_tools.release_assets.require_release_checkout"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main([*common, "preflight", *assertions, "--output", str(destination), "--github-output", str(output)]), 0)
            self.assertGreater(observed.call_count, 0)
            target = parse_release_target(destination.read_bytes())
            self.assertEqual((target.repository, target.tag, target.commit, target.release_id), (REPOSITORY, TAG, SHA, 9))
            self.assertIn(f"commit={SHA}\n".encode(), output.read_bytes())
            upload = [*common, "upload", *assertions, "--target", str(destination), "--asset", str(self.asset), "--approve"]
            self.assertEqual(cli.main(upload), 0)
            self.assertEqual(cli.main(upload), 0)  # Identical-byte retry is deliberate.
            self.server.commit = "c" * 40
            before = len(self.server.calls)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(upload), 1)
            self.assertFalse(any("POST" in call or "PATCH" in call for call in self.server.calls[before:]))

    @override
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="release target café ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.asset = self.root / "baseline.tar.gz"
        self.asset.write_bytes(PAYLOAD)
        self.server = GitHubFixture()
        self.download_transport = release_assets._download
        self.addCleanup(patch.stopall)
        patch("research_repo_tools.release_assets.run_command_bytes", side_effect=self.server.run).start()
        patch("research_repo_tools.release_assets._download", side_effect=self.server.download).start()

    def target(self):
        return preflight_release_target(self.root, REPOSITORY, TAG, SHA, expected_title=TAG)

    def upload(self, target=None, *, publish=True):
        publish_release_asset(self.root, REPOSITORY, TAG, self.asset, target=target or self.target(), publish=publish)

    def test_observed_checkout_drift_prevents_upload_or_publication(self):
        target = self.target()
        for phase in ("before-upload", "after-download"):
            with self.subTest(phase=phase):
                self.server.release["assets"] = []
                self.server.uploaded = []
                state = {"commit": "b" * 40 if phase == "before-upload" else SHA}

                def observe(args, **kwargs):
                    self.assertEqual(kwargs["cwd"], self.root / "source")
                    return subprocess.CompletedProcess(args, 0, "" if "status" in args else state["commit"], "")

                def changed():
                    state["commit"] = "b" * 40

                self.server.on_download = changed
                with (
                    patch("research_repo_tools.release_assets.run_git_command", observe),
                    patch("research_repo_tools.release_assets.run_git_bytes", return_value=subprocess.CompletedProcess([], 0, b"H tracked\0", b"")),
                ):
                    with self.assertRaisesRegex(ValueError, "observed release checkout"):
                        publish_release_asset(self.root, REPOSITORY, TAG, self.asset, target=target, checkout=self.root / "source", publish=True)
                self.assertEqual(self.server.published, 0)
                self.assertEqual(len(self.server.uploaded), 0 if phase == "before-upload" else 1)

    def test_preflight_lightweight_and_annotated_tags_roundtrip(self):
        for annotated in (False, True):
            with self.subTest(annotated=annotated):
                self.server.annotation = annotated
                target = self.target()
                self.assertEqual(target, ReleaseTarget(REPOSITORY, TAG, 9, SHA, TAG))
                payload = serialize_release_target(target)
                self.assertTrue(payload.endswith(b"\n"))
                self.assertNotIn(b"\r", payload)
                self.assertEqual(parse_release_target(payload), target)
                self.assertEqual(revalidate_release_target(self.root, target).identifier, 9)
        self.assertFalse(self.server.uploaded)
        self.assertEqual(self.server.published, 0)

    def test_target_rejects_invalid_identity_and_ambiguous_json(self):
        valid = json.loads(serialize_release_target(self.target()))
        for field, value in [
            ("schema", "v2"),
            ("repository", "../repo"),
            ("tag", "1.2.3"),
            ("tag", "v1.2.3-rc.1"),
            ("release_id", True),
            ("release_id", 0),
            ("commit", "a" * 39),
            ("commit", "A" * 40),
            ("expected_title", ""),
            ("expected_title", "line\nline"),
            ("expected_title", 1),
            ("extra", True),
        ]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    parse_release_target(json.dumps({**valid, field: value}).encode())
        for field in valid:
            with self.subTest(missing=field), self.assertRaises(ValueError):
                parse_release_target(json.dumps({key: value for key, value in valid.items() if key != field}).encode())
        for payload in [b'{"release_id":9,"release_id":10}', b"{}\xff", b'{"x":NaN}', b"[]"]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_release_target(payload)
        for identifier in [True, 0, -1]:
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                ReleaseTarget(REPOSITORY, TAG, identifier, SHA)

    def test_target_wire_format_has_an_independent_v1_oracle(self):
        payload = (
            "{\n"
            '  "commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n'
            '  "expected_title": "café baseline",\n'
            '  "release_id": 9,\n'
            '  "repository": "example/project",\n'
            '  "schema": "research-repo-tools/release-target/v1",\n'
            '  "tag": "v1.2.3"\n'
            "}\n"
        ).encode("utf-8")
        target = ReleaseTarget(REPOSITORY, TAG, 9, SHA, "café baseline")
        self.assertEqual(serialize_release_target(target), payload)
        self.assertEqual(parse_release_target(payload), target)

    def test_target_rejects_unicode_line_separators_before_api_access(self):
        for separator in ("\u0085", "\u2028", "\u2029"):
            title = "first" + separator + "second"
            with self.subTest(separator=separator):
                with self.assertRaisesRegex(ValueError, "single line"):
                    ReleaseTarget(REPOSITORY, TAG, 9, SHA, title)
                value = {
                    "schema": "research-repo-tools/release-target/v1",
                    "repository": REPOSITORY,
                    "tag": TAG,
                    "release_id": 9,
                    "commit": SHA,
                    "expected_title": title,
                }
                with self.assertRaisesRegex(ValueError, "single line"):
                    parse_release_target(json.dumps(value).encode("utf-8"))
                with self.assertRaisesRegex(ValueError, "single line"):
                    preflight_release_target(self.root, REPOSITORY, TAG, SHA, expected_title=title)
        self.assertFalse(self.server.calls)

    def test_download_preserves_real_child_bytes_and_rejects_translation(self):
        self.server.release["draft"] = False
        self.server.release["assets"] = [self.server.asset()]
        destination = self.root / "download café.tar.gz"
        popen = subprocess.Popen
        for emitted in (PAYLOAD, PAYLOAD.replace(b"\r\n", b"\n")):
            with self.subTest(emitted=emitted):
                destination.write_bytes(b"prior artifact")
                program = f"import sys; sys.stdout.buffer.write({emitted!r})"
                with (
                    patch("research_repo_tools.release_assets._download", self.download_transport),
                    patch("research_repo_tools.release_assets.resolve_executable", return_value=Path(sys.executable)),
                    patch(
                        "research_repo_tools.release_assets.subprocess.Popen",
                        side_effect=lambda command, **kwargs: popen([sys.executable, "-c", program], **kwargs),
                    ),
                ):
                    if emitted == PAYLOAD:
                        download_release_asset(self.root, REPOSITORY, TAG, "baseline.tar.gz", destination)
                        self.assertEqual(destination.read_bytes(), PAYLOAD)
                    else:
                        with self.assertRaisesRegex(ValueError, "size differs"):
                            download_release_asset(self.root, REPOSITORY, TAG, "baseline.tar.gz", destination)
                        self.assertEqual(destination.read_bytes(), b"prior artifact")
        self.assertFalse(self.server.uploaded)
        self.assertEqual(self.server.published, 0)

    def test_repository_url_identity_accepts_case_only_spelling_differences(self):
        self.server.release["url"] = self.server.release["url"].replace(REPOSITORY, REPOSITORY.upper())
        self.upload(self.target())
        self.assertEqual(self.server.published, 1)

    def test_preflight_rejects_missing_duplicate_and_wrong_tag_refs(self):
        for releases in [[], [[]], [[self.server.release], [self.server.release]], {}, [[False]]]:
            with self.subTest(releases=releases):
                self.server.releases = releases
                with self.assertRaises(ValueError):
                    self.target()
        self.server.releases = None
        self.server.ref = f"refs/heads/{TAG}"
        with self.assertRaisesRegex(ValueError, "tag is missing or differs"):
            self.target()
        self.server.ref = f"refs/tags/{TAG}"
        self.server.commit = "b" * 40
        with self.assertRaisesRegex(ValueError, "commit differs"):
            self.target()

    def test_replaced_id_and_lifecycle_or_title_drift_prevent_upload(self):
        target = self.target()
        original = copy.deepcopy(self.server.release)
        for field, value in [
            ("id", 10),
            ("id", True),
            ("tag_name", "v1.2.4"),
            ("url", "https://api.github.com/repos/other/repo/releases/9"),
            ("draft", False),
            ("immutable", True),
            ("prerelease", True),
            ("draft", 1),
            ("name", "changed"),
            ("name", None),
        ]:
            with self.subTest(field=field):
                self.server.release = {**original, field: value}
                with self.assertRaises(ValueError):
                    self.upload(target)
                self.assertFalse(self.server.uploaded)
                self.assertEqual(self.server.published, 0)

    def test_malformed_and_cyclic_tag_objects_reject_preflight(self):
        for obj in [{}, {"type": "tree", "sha": SHA}, {"type": "commit", "sha": "bad"}, False]:
            with self.subTest(obj=obj):
                self.server.ref_object = obj
                with self.assertRaises(ValueError):
                    self.target()
        self.server.ref_object = None
        self.server.annotation = True
        for obj in [
            {"sha": "c" * 40, "object": {"type": "commit", "sha": SHA}},
            {"sha": "b" * 40, "object": {"type": "tag", "sha": "b" * 40}},
            {"sha": "b" * 40, "object": None},
        ]:
            with self.subTest(obj=obj):
                self.server.annotation_object = obj
                with self.assertRaises(ValueError):
                    self.target()
        self.assertEqual(self.server.published, 0)

    def test_annotated_tag_depth_limit_has_an_acyclic_boundary(self):
        for depth in (4, 5):
            with self.subTest(depth=depth):
                identifiers = [f"{index:040x}" for index in range(1, depth + 1)]
                self.server.ref_object = {"type": "tag", "sha": identifiers[0]}
                self.server.tag_objects = {
                    f"repos/{REPOSITORY}/git/tags/{identifier}": {
                        "sha": identifier,
                        "object": {"type": "tag" if index + 1 < depth else "commit", "sha": identifiers[index + 1] if index + 1 < depth else SHA},
                    }
                    for index, identifier in enumerate(identifiers)
                }
                self.server.calls.clear()
                if depth == 4:
                    self.assertEqual(self.target(), ReleaseTarget(REPOSITORY, TAG, 9, SHA, TAG))
                else:
                    with self.assertRaisesRegex(ValueError, "bounded commit identity"):
                        self.target()
                annotation_requests = [args for args in self.server.calls if args[-1].startswith(f"repos/{REPOSITORY}/git/tags/")]
                self.assertEqual(len(annotation_requests), depth)
                self.assertFalse(self.server.uploaded)
                self.assertEqual(self.server.published, 0)

    def test_invalid_post_upload_metadata_retains_attachment_without_publication(self):
        target = self.target()
        for field, value in [("digest", None), ("digest", "sha256:" + "0" * 64), ("size", 1), ("state", "starter")]:
            with self.subTest(field=field):
                self.server.release["assets"] = []
                self.server.on_upload = lambda: self.server.release["assets"][0].update({field: value})
                with self.assertRaises(ValueError):
                    self.upload(target)
                self.assertEqual(len(self.server.release["assets"]), 1)
                self.assertTrue(self.server.release["draft"])
                self.assertEqual(self.server.published, 0)

    def test_moved_tag_before_during_upload_and_download_never_publishes(self):
        target = self.target()
        for phase in ("before", "upload", "download"):
            with self.subTest(phase=phase):
                self.server.commit = SHA
                self.server.release["assets"] = []
                self.server.on_upload = lambda: None
                self.server.on_download = lambda: None

                def change():
                    self.server.commit = "c" * 40

                if phase == "before":
                    change()
                elif phase == "upload":
                    self.server.on_upload = change
                else:
                    self.server.on_download = change
                count = len(self.server.uploaded)
                with self.assertRaisesRegex(ValueError, "commit differs"):
                    self.upload(target)
                self.assertEqual(len(self.server.uploaded) - count, int(phase != "before"))
                self.assertEqual(self.server.published, 0)

    def test_lifecycle_title_and_asset_drift_after_attachment_never_publish(self):
        target = self.target()
        original = copy.deepcopy(self.server.release)
        for field, value in [("id", 10), ("name", "renamed"), ("draft", False), ("immutable", True), ("prerelease", True)]:
            with self.subTest(field=field):
                self.server.release = copy.deepcopy(original)
                self.server.on_download = lambda: self.server.release.update({field: value})
                with self.assertRaises(ValueError):
                    self.upload(target)
                self.assertEqual(self.server.published, 0)
        self.server.release = copy.deepcopy(original)
        self.server.on_download = lambda: self.server.release["assets"][0].update(id=11)
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.upload(target)
        self.assertEqual(self.server.published, 0)

    def test_upload_retry_checks_metadata_and_exact_bytes_and_publishes_last(self):
        target = self.target()
        self.upload(target, publish=False)
        self.assertEqual(self.server.uploaded, [PAYLOAD])
        self.assertEqual(self.server.published, 0)
        self.upload(target)
        self.assertEqual(self.server.uploaded, [PAYLOAD])
        self.assertEqual(self.server.published, 1)
        self.assertEqual(self.asset.read_bytes(), PAYLOAD)
        # An ordinary retry after publication requires inspection, never a new PATCH.
        with self.assertRaises(ValueError):
            self.upload(target)
        self.assertEqual(self.server.published, 1)

    def test_old_signature_captures_one_invocation_target(self):
        publish_release_asset(self.root, REPOSITORY, TAG[1:], self.asset)
        self.assertEqual(self.server.uploaded, [PAYLOAD])
        self.assertEqual(self.server.published, 0)

    def test_missing_conflicting_incomplete_duplicate_and_malformed_assets(self):
        target = self.target()
        for field, value in [
            ("digest", None),
            ("digest", "sha256:" + "0" * 64),
            ("digest", "sha1:bad"),
            ("size", 1),
            ("size", True),
            ("id", True),
            ("state", "starter"),
            ("state", None),
        ]:
            with self.subTest(field=field, value=value):
                self.server.release["assets"] = [{**self.server.asset(), field: value}]
                with self.assertRaises(ValueError):
                    self.upload(target)
                self.assertFalse(self.server.uploaded)
                self.assertEqual(self.server.published, 0)
        self.server.release["assets"] = []
        for pages in [[self.server.asset()], [[self.server.asset()], [self.server.asset()]], [[]], [False]]:
            with self.subTest(pages=pages):
                self.server.asset_pages = pages
                if pages == [[]]:
                    self.server.on_upload = lambda: None
                with self.assertRaises(ValueError):
                    self.upload(target)
                self.assertEqual(self.server.published, 0)
        self.server.asset_pages = None
        self.server.release["assets"] = [self.server.asset()]
        self.server.downloaded = b"different"
        with self.assertRaisesRegex(ValueError, "downloaded release asset differs"):
            self.upload(target)
        self.assertEqual(self.server.published, 0)

    def test_upload_failure_preserves_draft_and_does_not_publish(self):
        target = self.target()
        self.server.upload_error = subprocess.CalledProcessError(1, ["gh", "api"])
        with self.assertRaises(subprocess.CalledProcessError):
            self.upload(target)
        self.assertTrue(self.server.release["draft"])
        self.assertEqual(self.server.published, 0)

    def test_publication_invalid_or_unavailable_response_reports_unknown_once(self):
        target = self.target()
        published = {**self.server.release, "draft": False, "assets": [self.server.asset()]}
        for response in [
            b"not JSON",
            b'{"id":9,"id":10}',
            {},
            *[
                {**published, field: value}
                for field, value in [("id", 10), ("tag_name", "v1.2.4"), ("draft", True), ("prerelease", True), ("name", "renamed"), ("assets", [])]
            ],
            subprocess.TimeoutExpired("gh", 10),
            subprocess.CalledProcessError(1, ["gh", "api"]),
            OSError("unavailable"),
        ]:
            with self.subTest(response=response):
                self.server.release["draft"] = True
                self.server.release["assets"] = [self.server.asset()]
                self.server.publication_response = response
                count = self.server.published
                with self.assertRaisesRegex(ReleasePublicationUnknownError, "outcome unknown.*inspect"):
                    self.upload(target)
                self.assertEqual(self.server.published, count + 1)
                self.assertFalse(self.server.uploaded)
                self.assertFalse(any("DELETE" in call for call in self.server.calls))

    def test_successful_publication_may_make_release_immutable(self):
        target = self.target()
        self.server.publication_response = {**self.server.release, "draft": False, "immutable": True, "assets": [self.server.asset()]}
        self.upload(target)
        self.assertEqual(self.server.published, 1)

    def test_reviewed_publication_shares_target_and_response_validation(self):
        self.server.release["assets"] = [self.server.asset()]
        draft = revalidate_release_target(self.root, self.target())
        settings = config.parse(
            {
                "publishing": {
                    "registry": "pypi",
                    "package": "sample",
                    "repository": REPOSITORY,
                    "required-checks": [{"name": "native", "app-id": 15368}],
                    "required-assets": ["baseline.tar.gz"],
                }
            },
            root=self.root,
        )
        with (
            patch("research_repo_tools.release_publishing.check_reviewed_release", return_value=draft),
            patch("research_repo_tools.release_publishing.run_git_command", return_value=subprocess.CompletedProcess([], 0, SHA, "")),
        ):
            publish_reviewed_release(settings, TAG)
            self.assertEqual(self.server.published, 1)
            for response in [{}, {**self.server.release, "assets": []}]:
                with self.subTest(response=response):
                    self.server.release["draft"] = True
                    self.server.publication_response = response
                    with self.assertRaises(ReleasePublicationUnknownError):
                        publish_reviewed_release(settings, TAG)
            self.server.release["draft"] = True
            self.server.commit = "c" * 40
            count = self.server.published
            with self.assertRaisesRegex(ValueError, "commit differs"):
                publish_reviewed_release(settings, TAG)
            self.assertEqual(self.server.published, count)

    def test_cli_preflight_failure_preserves_output_and_unknown_publication_is_an_error(self):
        target_path = self.root / "target.json"
        target_path.write_bytes(serialize_release_target(self.target()))
        original = target_path.read_bytes()
        self.server.commit = "c" * 40
        args = ["--root", str(self.root), "performance", "release-draft", REPOSITORY, TAG, "--commit", SHA, "--output", str(target_path)]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(args), 1)
        self.assertEqual(target_path.read_bytes(), original)
        self.server.commit = SHA
        self.server.publication_response = {}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(
                cli.main(
                    ["--root", str(self.root), "performance", "release-upload", REPOSITORY, TAG, str(self.asset), "--target", str(target_path), "--publish"]
                ),
                1,
            )
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("publication outcome unknown", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(self.server.published, 1)

    def test_cli_target_file_stdout_validation_and_explicit_publication(self):
        settings = config.parse({}, root=self.root)
        target_path = self.root / "target.json"
        args = ["performance", "release-draft", REPOSITORY, TAG, "--commit", SHA, "--expected-title", TAG]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.run(cli.parser().parse_args(args), settings), 0)
        self.assertEqual(parse_release_target(output.getvalue().encode()), self.target())
        self.assertEqual(cli.run(cli.parser().parse_args([*args, "--output", str(target_path)]), settings), 0)
        upload = ["performance", "release-upload", REPOSITORY, TAG, str(self.asset), "--target", str(target_path)]
        self.assertEqual(cli.run(cli.parser().parse_args(upload), settings), 0)
        self.assertEqual(self.server.published, 0)
        self.assertEqual(cli.run(cli.parser().parse_args([*upload, "--publish"]), settings), 0)
        self.assertEqual(self.server.published, 1)
        target_path.write_bytes(b'{"release_id":9,"release_id":10}')
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["--root", str(self.root), *upload, "--publish"]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("duplicate JSON field", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(self.server.published, 1)

    def test_cli_explicit_empty_values_never_downgrade_release_binding(self):
        for value in ("", " "):
            for action, extra, diagnostic in [
                ("release-draft", ["--commit", value], "full lowercase GitHub commit SHA"),
                ("release-draft", ["--commit", SHA, "--output", value], "--output must be a nonempty path"),
                ("release-upload", [str(self.asset), "--target", value, "--publish"], "--target must be a nonempty path"),
            ]:
                with self.subTest(value=value, action=action, extra=extra):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        status = cli.main(["--root", str(self.root), "performance", action, REPOSITORY, TAG, *extra])
                    self.assertEqual(status, 1)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertIn(diagnostic, stderr.getvalue())
                    self.assertNotIn("Traceback", stderr.getvalue())
                    self.assertFalse(self.server.calls)
                    self.assertFalse(self.server.uploaded)
                    self.assertEqual(self.server.published, 0)
                    self.assertEqual(self.asset.read_bytes(), PAYLOAD)

    def test_mismatched_handoff_and_invalid_assertions_fail_before_api(self):
        target = ReleaseTarget("other/repo", TAG, 9, SHA)
        with self.assertRaisesRegex(ValueError, "repository/tag differ"):
            self.upload(target)
        for tag, commit in [("1.2.3", SHA), (TAG, "bad")]:
            with self.subTest(tag=tag, commit=commit), self.assertRaises(ValueError):
                preflight_release_target(self.root, REPOSITORY, tag, commit)
        self.assertFalse(self.server.calls)


if __name__ == "__main__":
    unittest.main()
