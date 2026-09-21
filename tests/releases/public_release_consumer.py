"""Installed release-policy contract tests using only supported package imports."""

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cli import main
from research_repo_tools.files import RecoveryError
from research_repo_tools.releases import (
    ReleaseAdapter,
    ReleaseContext,
    ReleasePolicy,
    ReleaseRule,
    ReleaseValidationError,
    StaleReleasePlanError,
    apply_release,
    check_release,
    discover_release,
    plan_release,
    published_releases,
)

DOI = "10.5281/zenodo.20033111"


def policy() -> ReleasePolicy:
    return ReleasePolicy(
        required_files=("Cargo.lock", "pyproject.toml", "uv.lock", "CITATION.cff", "CHANGELOG.md", "README.md", "REFERENCES.md"),
        exclude=("docs/evidence/**",),
        rules=(
            ReleaseRule("CITATION.cff", r"^doi: (?P<value>\S+)$", value=DOI),
            ReleaseRule("README.md", r"\[!\[DOI\]\([^)]*\)\]\(https://doi.org/(?P<value>[^)]+)\)", value=DOI),
            ReleaseRule("REFERENCES.md", r"^- DOI: <https://doi.org/(?P<value>[^>]+)>$", value=DOI),
            ReleaseRule(
                "README.md",
                r"https://github.com/example/consumer/blob/(?P<value>[^/]+)/[^\s)]+",
                source="tag",
                exclude=r"/docs/(?:PERFORMANCE\.md|archive/performance/)",
            ),
            ReleaseRule("docs/RELEASING.md", r"just performance-release (?P<value>v\S+) v\S+", source="tag"),
            ReleaseRule("docs/RELEASING.md", r"just performance-release v\S+ (?P<value>v\S+)", source="previous-tag"),
        ),
    )


class TestReleaseConsumer(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="release-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "consumer café"
        self.root.mkdir()
        self.policy = policy()
        files = {
            "Cargo.toml": '[package]\nname = "consumer"\nversion = "1.2.3"\n',
            "Cargo.lock": 'version = 4\n[[package]]\nname = "consumer"\nversion = "1.2.3"\n',
            "pyproject.toml": '[project]\nname = "consumer-tools"\nversion = "1.2.3"\n',
            "uv.lock": 'version = 1\n[[package]]\nname = "consumer-tools"\nversion = "1.2.3"\nsource = { virtual = "." }\n',
            "CITATION.cff": f"cff-version: 1.2.0\nversion: 1.2.3\ndoi: {DOI}\ndate-released: 2026-09-01\n",
            "CHANGELOG.md": "# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Previous.\n",
            "README.md": f"[![DOI](badge)](https://doi.org/{DOI})\n"
            "[source](https://github.com/example/consumer/blob/v1.2.3/src/lib.rs)\n"
            "[measured](https://github.com/example/consumer/blob/v1.1.0/docs/PERFORMANCE.md)\n"
            "[archive](https://github.com/example/consumer/blob/v1.0.0/docs/archive/performance/report.md)\n",
            "REFERENCES.md": f"- DOI: <https://doi.org/{DOI}>\n",
            "docs/RELEASING.md": "just performance-release v1.2.3 v1.2.2\n",
            "docs/evidence/measured.md": 'consumer = "0.9.0"\njust performance-release v0.9.0 v0.8.0\n',
            "docs/archive/old.md": 'consumer = "0.1.0"\n',
            "evidence.json": '{"measured": "1.1.0", "release": "1.2.3"}\n',
        }
        for name, text in files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.replace("\n", "\r\n").encode())
        # Rules use end-of-line anchors; tolerate the preserved CRLF bytes.
        self.policy = replace(self.policy, rules=tuple(replace(rule, pattern=rule.pattern.replace("$", r"\r?$")) for rule in self.policy.rules))

    def snapshot(self) -> dict[str, bytes]:
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def plan(self, **kwargs):
        return plan_release(self.root, "1.2.4", previous_tag="v1.2.3", release_date="2026-09-20", policy=self.policy, **kwargs)

    def test_discovery_and_preview_apply_preserve_historical_bytes(self) -> None:
        before = self.snapshot()
        discovery = discover_release(self.root, policy=self.policy)
        self.assertEqual(discovery.package.version, "1.2.3")
        self.assertNotIn(Path("docs/evidence/measured.md"), discovery.files)
        self.assertTrue(check_release(self.root, policy=self.policy, previous_tag="v1.2.2").ok)
        plan = self.plan()
        self.assertEqual(self.snapshot(), before)
        result = apply_release(plan)
        self.assertEqual(result.changed_paths, tuple(edit.path for edit in plan.edits))
        for edit in plan.edits:
            self.assertEqual((self.root / edit.path).read_bytes(), edit.after)
        after = self.snapshot()
        self.assertEqual(after["docs/RELEASING.md"], b"just performance-release v1.2.4 v1.2.3\r\n")
        self.assertEqual(after["docs/evidence/measured.md"], before["docs/evidence/measured.md"])
        self.assertEqual(after["docs/archive/old.md"], before["docs/archive/old.md"])
        self.assertEqual(after["CHANGELOG.md"], before["CHANGELOG.md"])
        self.assertIn(b"/blob/v1.2.4/src/lib.rs", after["README.md"])
        self.assertIn(b"/blob/v1.1.0/docs/PERFORMANCE.md", after["README.md"])
        self.assertIn(b"/blob/v1.0.0/docs/archive/performance/report.md", after["README.md"])
        self.assertFalse(self.plan().edits)

    def test_small_adapter_contributes_edits_and_validates_complete_candidate(self) -> None:
        calls = []

        def prepare(candidate: Path, context: ReleaseContext) -> dict[str, bytes]:
            self.assertEqual(context.previous_tag, "v1.2.3")
            self.assertIn(context.tag.encode(), (candidate / "README.md").read_bytes())
            report = json.loads((candidate / "evidence.json").read_bytes())
            report["release"] = context.version
            return {
                "evidence.json": json.dumps(report).encode(),
                "CHANGELOG.md": f"## [{context.version}] - {context.release_date}\n\n- Current.\n\n".encode() + (candidate / "CHANGELOG.md").read_bytes(),
            }

        def validate(candidate: Path, context: ReleaseContext) -> tuple[str, ...]:
            calls.append(context.tag)
            self.assertNotEqual(candidate, self.root)
            report = json.loads((candidate / "evidence.json").read_bytes())
            return () if report == {"measured": "1.1.0", "release": context.version} else ("invalid evidence selection",)

        adapter = ReleaseAdapter(("evidence.json",), prepare, validate)
        self.policy = replace(self.policy, final_changelog=True)
        original = self.snapshot()
        preview = self.plan(adapter=adapter)
        self.assertEqual(self.snapshot(), original)
        apply_release(preview)
        self.assertTrue(check_release(self.root, policy=self.policy, previous_tag="v1.2.3", adapter=adapter).ok)
        self.assertEqual(calls, ["v1.2.4", "v1.2.4"])

    def test_fixed_doi_failure_precedes_adapters_and_preserves_files(self) -> None:
        for name in ("CITATION.cff", "README.md", "REFERENCES.md"):
            path = self.root / name
            path.write_bytes(path.read_bytes().replace(DOI.encode(), b"10.5281/zenodo.99999999"))
        before = self.snapshot()
        self.assertFalse(check_release(self.root, policy=self.policy, previous_tag="v1.2.2").ok)
        adapter = ReleaseAdapter(prepare=lambda *_: self.fail("adapter called despite fixed DOI mismatch"))
        with self.assertRaisesRegex(ReleaseValidationError, DOI):
            self.plan(adapter=adapter)
        self.assertEqual(self.snapshot(), before)

    def test_missing_and_ambiguous_selectors_fail_checks_and_plans(self) -> None:
        target = self.root / "docs/RELEASING.md"
        for payload in (b"no command", target.read_bytes() * 2):
            target.write_bytes(payload)
            before = self.snapshot()
            for action in (lambda: check_release(self.root, policy=self.policy, previous_tag="v1.2.2"), self.plan):
                with self.assertRaisesRegex(ValueError, "requires exactly 1 matches"):
                    action()
                self.assertEqual(self.snapshot(), before)

    def test_missing_required_file_and_invalid_metadata_fail_without_writes(self) -> None:
        path = self.root / "uv.lock"
        before = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "required release file"):
            self.plan()
        path.write_bytes(before)
        citation = self.root / "CITATION.cff"
        citation.write_bytes(citation.read_bytes().replace(b"version: 1.2.3", b'version: "'))
        original = self.snapshot()
        with self.assertRaisesRegex(ValueError, "version must be"):
            self.plan()
        self.assertEqual(self.snapshot(), original)

    def test_adapter_rejection_and_invalid_edits_never_publish(self) -> None:
        adapters = (
            ReleaseAdapter(validate=lambda *_: ("evidence validation failed",)),
            ReleaseAdapter(prepare=lambda *_: {"../escape": b"bad"}),
            ReleaseAdapter(prepare=lambda *_: {"unselected.json": b"bad"}),
            ReleaseAdapter(prepare=lambda *_: {"README.md": b"removed required references"}),
        )
        before = self.snapshot()
        for adapter in adapters:
            with self.assertRaises(ValueError):
                self.plan(adapter=adapter)
            self.assertEqual(self.snapshot(), before)

    def test_adapter_cannot_mutate_candidate_behind_validation(self) -> None:
        def mutate(candidate: Path, _context: ReleaseContext) -> tuple[str, ...]:
            (candidate / "Cargo.toml").write_bytes(b"bad")
            return ()

        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "must not mutate"):
            self.plan(adapter=ReleaseAdapter(validate=mutate))
        self.assertEqual(self.snapshot(), before)

    def test_stale_sources_and_new_active_files_reject_apply(self) -> None:
        plan = self.plan()
        target = self.root / "README.md"
        original = target.read_bytes()
        target.write_bytes(original + b"concurrent edit")
        before = self.snapshot()
        with self.assertRaises(StaleReleasePlanError):
            apply_release(plan)
        self.assertEqual(self.snapshot(), before)
        target.write_bytes(original)
        (self.root / "new.md").write_bytes(b"new active document")
        with self.assertRaises(StaleReleasePlanError):
            apply_release(plan)

    def test_failed_replacement_rolls_back_all_candidate_edits(self) -> None:
        plan = self.plan()
        before = self.snapshot()
        replace_path = Path.replace
        calls = 0

        def fail_once(source: Path, target: Path) -> Path:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated release publication failure")
            return replace_path(source, target)

        with patch.object(Path, "replace", fail_once), self.assertRaisesRegex(OSError, "publication failure"):
            apply_release(plan)
        self.assertEqual(self.snapshot(), before)

    def test_failed_rollback_retains_recovery_bytes(self) -> None:
        plan = self.plan()
        first = self.root / plan.edits[0].path
        second = self.root / plan.edits[1].path
        replace_path = Path.replace

        def fail(source: Path, target: Path) -> Path:
            if target == second or (target == first and source.suffix == ".bak"):
                raise OSError("simulated release failure")
            return replace_path(source, target)

        with patch.object(Path, "replace", fail), self.assertRaises(ExceptionGroup) as raised:
            apply_release(plan)
        recovery = raised.exception.exceptions[1]
        self.assertIsInstance(recovery, RecoveryError)
        assert isinstance(recovery, RecoveryError) and recovery.backup is not None
        self.assertEqual(recovery.backup.read_bytes(), plan.edits[0].before)

    def test_cli_configuration_uses_identical_policy_for_check_preview_and_apply(self) -> None:
        config = self.root / "release.toml"
        lines = ["[release]", 'exclude = ["docs/evidence/**"]', "required-files = " + json.dumps(list(self.policy.required_files))]
        for rule in self.policy.rules:
            lines.extend(["[[release.rules]]", "path = " + json.dumps(rule.path), "pattern = " + json.dumps(rule.pattern)])
            for name in ("value", "source", "exclude"):
                if value := getattr(rule, name):
                    lines.append(name + " = " + json.dumps(value))
        config.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        args = ["--root", str(self.root), "--config", str(config), "release"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([*args, "check", "--previous-release", "v1.2.2"]), 0)
            before = self.snapshot()
            update = [*args, "update", "1.2.4", "--previous-release", "v1.2.3", "--date", "2026-09-20"]
            self.assertEqual(main([*update, "--dry-run"]), 0)
            self.assertEqual(self.snapshot(), before)
            preview = self.plan()
            self.assertEqual(main(update), 0)
            for edit in preview.edits:
                self.assertEqual((self.root / edit.path).read_bytes(), edit.after)

    def test_discovery_filters_and_orders_published_releases(self) -> None:
        releases = [
            {"tagName": tag, "isDraft": draft, "isPrerelease": pre, "publishedAt": "2026-09-01T00:00:00Z"}
            for tag, draft, pre in (("v1.2.9", False, False), ("v1.2.10", False, False), ("v2.0.0", True, False), ("v1.3.0-rc.1", False, True))
        ]
        # Public API's gh process boundary; no package implementation patches.
        import subprocess

        response = subprocess.CompletedProcess(["gh"], 0, json.dumps(releases).encode(), b"")
        with patch("shutil.which", return_value=str(Path(__file__).resolve())), patch("subprocess.run", return_value=response):
            result = published_releases(self.root)
        self.assertEqual([item.tag for item in result], ["v1.2.10", "v1.2.9"])


if __name__ == "__main__":
    unittest.main()
