"""Installed independent preparation and README retry boundaries."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cli import main
from research_repo_tools.criterion import COMPARISON_SCHEMA, Estimate, Sample, compare_samples, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance, fingerprint_files
from research_repo_tools.performance_release import (
    load_performance_release,
    load_preparation,
    load_readme_plan,
    measure_release,
    parse_preparation,
    prepare_performance,
    select_measurement_pair,
)
from research_repo_tools.publication import publish_publication
from research_repo_tools.release_discovery import PublishedRelease

CURRENT = "b" * 40
BASELINE = "a" * 40


def fixture(root):
    (root / "pyproject.toml").write_bytes(b'[project]\nname="fixture"\nversion="1.1.0"\n')
    (root / "source.py").write_bytes(b"# scientific fixture caf\xc3\xa9\r\n")
    (root / "measurement.toml").write_bytes(b'schema=1\ncommand=["inert"]\nsources=["source.py"]\nharness=["source.py"]\nminimum-release="v1.0.0"\n')
    (root / "report.toml").write_bytes(b'schema=1\ncurrent="report.md"\narchive="history"\ntitle="Timings"\n')
    (root / "release.toml").write_bytes(
        b'schema=1\nrepository="example/project"\nmeasurement="measurement.toml"\nreport="report.toml"\n'
        b'publication="publication.toml"\npreparation="scratch/preparation.json"\npayload="scratch/run.json"\nmanifest="scratch/evidence.json"\n'
    )
    (root / "README.md").write_bytes(b"# Results\r\n\r\n<!-- BEGIN -->\r\nold\r\n<!-- END -->\r\n")
    (root / "references.txt").write_bytes(b"Current: v1.1.0\nBaseline: v1.0.0\n")
    (root / "publication.toml").write_bytes(b"""schema=1
document="README.md"
payload="history/v1.1.0-vs-v1.0.0.comparison.json"
manifest="history/v1.1.0-vs-v1.0.0.evidence.json"
begin="<!-- BEGIN -->"
end="<!-- END -->"
unit="ns"
rows=[{benchmark="case", label="Case"}]
current-sources=["source.py"]
current-harness=["source.py"]
[provenance.baseline]
[provenance.current]
[[references]]
path="pyproject.toml"
pattern='version="(?P<value>[^"\\r\\n]+)"'
source="version"
[[references]]
path="references.txt"
pattern='Current: (?P<value>[^\\r\\n]+)'
source="tag"
[[references]]
path="references.txt"
pattern='Baseline: (?P<value>[^\\r\\n]+)'
source="previous-tag"
""")
    return load_performance_release(root, "release.toml")


def evidence(root):
    payload = serialize_comparison(compare_samples(Sample((("case", Estimate(10)),)), Sample((("case", Estimate(5)),))))
    digest = fingerprint_files(root, (Path("source.py"),))
    return Evidence(
        payload,
        COMPARISON_SCHEMA,
        tuple(
            (phase, Provenance(revision, digest, digest, (("release", tag), ("fingerprint-schema", "research-repo-tools/files/v1"))))
            for phase, revision, tag in (("baseline", BASELINE, "v1.0.0"), ("current", CURRENT, "v1.1.0"))
        ),
    )


class TestPreparation(unittest.TestCase):
    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "Git-mutating fixture disabled")
    def test_native_preparation_preserves_index_and_refs(self):
        from research_repo_tools.process import run_git_bytes

        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.enterContext(patch.dict(os.environ, environment, clear=True))
        with tempfile.TemporaryDirectory(prefix="native préparation ") as temporary:
            root = Path(temporary).resolve()
            settings = fixture(root)
            (root / ".gitignore").write_bytes(b"scratch/\n")

            def git(*arguments):
                return run_git_bytes(
                    [
                        "--no-pager",
                        "-c",
                        "core.autocrlf=false",
                        "-c",
                        "commit.gpgsign=false",
                        "-c",
                        f"core.hooksPath={root / 'disabled-hooks'}",
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        *arguments,
                    ],
                    cwd=root,
                ).stdout

            git("init", "--template=")
            git("add", ".")
            git("commit", "-m", "fixture")
            git("tag", "v1.0.0")
            head = git("--no-pager", "rev-parse", "HEAD").decode().strip()
            before = {path: path.read_bytes() for path in (root / ".git").rglob("*") if path.is_file()}
            with patch(
                "research_repo_tools.performance_release.published_releases", return_value=(PublishedRelease("v1.0.0", datetime(2024, 1, 1, tzinfo=UTC)),)
            ):
                record = prepare_performance(root, settings)
            self.assertEqual((record.current_revision, record.baseline_revision), (head, head))
            self.assertEqual(load_preparation(root, settings), record)
            self.assertEqual({path: path.read_bytes() for path in (root / ".git").rglob("*") if path.is_file()}, before)
            # An unignored output is rejected, even if the parent exists.
            with self.assertRaisesRegex(ValueError, "ignored"):
                prepare_performance(root, replace(settings, preparation="unignored.json"))
            self.assertFalse((root / "unignored.json").exists())

    def models(self):
        self.refs = {"HEAD": CURRENT, "v1.1.0": "c" * 40, "v1.0.0": BASELINE, "v0.9.0": "d" * 40}
        self.calls = []

        def git(args, **kwargs):
            self.calls.append(args)
            self.assertIn(args[1], ("ls-files", "check-ignore", "show-ref"))
            if args[1] == "show-ref":
                return subprocess.CompletedProcess(args, 1, b"")
            return subprocess.CompletedProcess(args, 0, b"")

        self.enterContext(patch("research_repo_tools.performance_release.run_git_bytes", side_effect=git))
        self.enterContext(patch("research_repo_tools.performance_release.resolve_revision", side_effect=lambda root, ref: self.refs[ref]))
        self.enterContext(
            patch("research_repo_tools.performance_release.published_releases", return_value=(PublishedRelease("v1.0.0", datetime(2024, 1, 1, tzinfo=UTC)),))
        )

    def test_prepare_measure_readme_and_retries_preserve_measurement_bytes(self):
        self.models()
        with tempfile.TemporaryDirectory(prefix="préparation ") as temporary:
            root = Path(temporary).resolve()
            settings = fixture(root)
            retained = evidence(root)
            with (
                patch("research_repo_tools.performance_acquisition.measure_pair", side_effect=AssertionError("preparation measured")),
                patch("research_repo_tools.publication.publish_publication", side_effect=AssertionError("preparation published")),
            ):
                record = prepare_performance(root, settings)
            self.assertEqual(record.identities, {"current": ("v1.1.0", CURRENT), "baseline": ("v1.0.0", BASELINE)})
            # Parsing in a separate interpreter proves the handoff needs no shell state.
            script = "from pathlib import Path; from research_repo_tools.performance_release import parse_preparation; import sys; r=parse_preparation(Path(sys.argv[1]).read_bytes()); print(r.current_revision, r.baseline_revision)"
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "-c", script, str(root / settings.preparation)], check=True, capture_output=True, encoding="utf-8"
            )
            self.assertEqual(result.stdout.strip(), f"{CURRENT} {BASELINE}")
            with patch("research_repo_tools.performance_acquisition.measure_pair", return_value=retained) as acquire:
                measure_release(root, settings, allow_git_mutations=True)
                self.assertEqual(acquire.call_count, 1)
            original_readme = (root / "README.md").read_bytes()
            files = {path: path.read_bytes() for folder in ("history", "scratch") for path in (root / folder).rglob("*") if path.is_file()}
            with (
                patch("research_repo_tools.performance_acquisition.measure_pair", side_effect=AssertionError("README measured")),
                patch("research_repo_tools.performance_release.published_releases", side_effect=AssertionError("README fetched provider state")),
            ):
                (root / "README.md").write_bytes(b"missing markers")
                with self.assertRaises(ValueError):
                    load_readme_plan(root, settings)
                (root / "README.md").write_bytes(original_readme)
                publish_publication(load_readme_plan(root, settings))
                first = (root / "README.md").read_bytes()
                self.assertNotEqual(first, original_readme)
                self.assertEqual(publish_publication(load_readme_plan(root, settings)), ())
                self.assertEqual((root / "README.md").read_bytes(), first)
                # Compatibility path never opens a missing preparation record.
                (root / settings.preparation).unlink()
                publish_publication(load_readme_plan(root, settings, identities=record.identities))
            self.assertEqual(
                {path: path.read_bytes() for path in files if path != root / settings.preparation},
                {path: value for path, value in files.items() if path != root / settings.preparation},
            )

    def test_publication_chronology_and_published_vs_prospective_selection(self):
        self.models()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = fixture(root)
            published = (
                PublishedRelease("v1.1.0", datetime(2024, 1, 3, tzinfo=UTC)),
                PublishedRelease("v1.0.0", datetime(2024, 1, 1, tzinfo=UTC)),
                PublishedRelease("v1.0.1", datetime(2024, 1, 2, tzinfo=UTC)),
            )
            self.refs["v1.0.1"] = "e" * 40
            with patch("research_repo_tools.performance_release.published_releases", return_value=published):
                record = prepare_performance(root, settings)
                self.assertFalse(record.working_tree)
                self.assertEqual(record.current_revision, "c" * 40)
                self.assertEqual(record.pair.baseline, "v1.0.1")
                pair, working = select_measurement_pair(root, settings.measurement)
                self.assertEqual((pair, working), (record.pair, record.working_tree))
            explicit = replace(settings, mode="explicit", current="v1.1.0", baseline="v1.0.0")
            with patch("research_repo_tools.performance_release.published_releases", side_effect=AssertionError("explicit pair queried provider")):
                self.assertEqual(prepare_performance(root, explicit).current_revision, "c" * 40)
            for releases in ((), (PublishedRelease("v1.1.0", datetime(2024, 1, 1, tzinfo=UTC)),)):
                with patch("research_repo_tools.performance_release.published_releases", return_value=releases), self.assertRaises(ValueError):
                    prepare_performance(root, settings)
            with self.assertRaisesRegex(ValueError, "requires v1.0.0"):
                prepare_performance(root, replace(explicit, baseline="v0.9.0"))

    def test_missing_malformed_partial_and_stale_records_preserve_prior_outputs(self):
        self.models()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = fixture(root)
            with self.assertRaises(FileNotFoundError):
                load_preparation(root, settings)
            prepare_performance(root, settings)
            original = (root / settings.preparation).read_bytes()
            raw = json.loads(original)
            for payload in (
                b"",
                b"{}",
                original.replace(b'"current": {', b'"unknown": {'),
                json.dumps({**raw, "schema": "old/v0"}).encode(),
                original.replace(b'"working_tree": true', b'"working_tree": 1'),
                original.replace(CURRENT.encode(), b"short"),
                b'{"schema": 1, "schema": 2}',
            ):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    parse_preparation(payload)
            for ref in ("HEAD", "v1.0.0"):
                prior = self.refs[ref]
                self.refs[ref] = "f" * 40
                with self.assertRaisesRegex(ValueError, "stale"):
                    load_preparation(root, settings)
                self.refs[ref] = prior
            # A tag created since preparation must agree with prospective HEAD.
            with (
                patch("research_repo_tools.performance_release.run_git_bytes", return_value=subprocess.CompletedProcess([], 0, b"")),
                self.assertRaisesRegex(ValueError, "prospective tag"),
            ):
                load_preparation(root, settings)
            with patch("research_repo_tools.performance_release.published_releases", side_effect=ValueError("provider failed")), self.assertRaises(ValueError):
                prepare_performance(root, settings)
            self.assertEqual((root / settings.preparation).read_bytes(), original)
            with self.assertRaisesRegex(ValueError, "allow-git-mutations"):
                prepare_performance(root, settings, fetch_tags=True)
            self.assertFalse(any("fetch" in command for command in self.calls))
            with (
                patch("research_repo_tools.performance_release.run_git_bytes", return_value=subprocess.CompletedProcess([], 1, b"")),
                self.assertRaisesRegex(ValueError, "ignored"),
            ):
                prepare_performance(root, settings)
            with patch("research_repo_tools.files._replace_path", side_effect=OSError("replacement denied")), self.assertRaises(OSError):
                prepare_performance(root, settings)
            self.assertEqual((root / settings.preparation).read_bytes(), original)

    def test_cli_partial_explicit_assertions_and_stale_plan_fail_without_reacquisition(self):
        self.models()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = fixture(root)
            args = ["--root", str(root), "performance"]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main([*args, "prepare", "release.toml"]), 0)
            with patch("research_repo_tools.performance_acquisition.measure_pair", return_value=evidence(root)):
                measure_release(root, settings, allow_git_mutations=True)
            plan = load_readme_plan(root, settings)
            before = (root / "README.md").read_bytes()
            self.refs["HEAD"] = "f" * 40
            with self.assertRaisesRegex(ValueError, "stale"):
                publish_publication(plan)
            self.assertEqual((root / "README.md").read_bytes(), before)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main([*args, "readme", "release.toml", "--current-release", "v1.1.0"]), 1)


if __name__ == "__main__":
    unittest.main()
