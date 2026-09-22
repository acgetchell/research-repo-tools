"""Installed public workflow checks using small representative retained inputs."""

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.ci import export_environment
from research_repo_tools.cli import main
from research_repo_tools.criterion import COMPARISON_SCHEMA, Estimate, Sample, compare_samples, parse_comparison, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance, deterministic_json, publish_evidence, sha256
from research_repo_tools.legacy_evidence import convert_csv
from research_repo_tools.measurement import MeasurementConfig, measure_checkout
from research_repo_tools.performance_reports import load_report_plan
from research_repo_tools.publication import publish_publication
from research_repo_tools.release_assets import package_baseline, read_baseline
from research_repo_tools.selection import argument_batches
from research_repo_tools.validation import run_checks
from research_repo_tools.worktrees import apply_snapshot, capture_snapshot, temporary_worktree

LEGACY_CONFIG = b"""schema = 1
schema-field = "schema"
schemas = ["old/v1"]
hash-field = "sha256"
header = ["schema", "name", "coverage", "bp", "bl", "bu", "cp", "cl", "cu"]
benchmark = "name"
statistic = "median"
unit = "ns"
equal = [["baseline.release", "baseline.label"]]
[constants]
schema = "old-csv/v1"
[coverage]
column = "coverage"
common = "paired"
added = "added"
missing = "missing"
[baseline]
point = "bp"
lower = "bl"
upper = "bu"
[current]
point = "cp"
lower = "cl"
upper = "cu"
[sources.baseline]
revision = "baseline.revision"
release = "baseline.release"
record = "baseline"
[sources.current]
revision = "current.revision"
release = "current.release"
record = "current"
"""


def legacy() -> tuple[bytes, bytes]:
    payload = b"schema,name,coverage,bp,bl,bu,cp,cl,cu\r\nold-csv/v1,shared,paired,10,8,12,5,4,6\r\nold-csv/v1,new,added,,,,3,,\r\n"
    manifest = deterministic_json(
        {
            "schema": "old/v1",
            "sha256": sha256(payload),
            "baseline": {"revision": "a" * 40, "release": "v1.0.0", "label": "v1.0.0", "source_digest": "b" * 64},
            "current": {"revision": "c" * 40, "release": "v1.1.0", "source_digest": "d" * 64},
        }
    )
    return payload, manifest


def retained(current: str = "v1.1.0", baseline: str = "v1.0.0", point: float = 5) -> Evidence:
    comparison = compare_samples(Sample((("shared", Estimate(10, 8, 12)),)), Sample((("shared", Estimate(point, 4, 6)),)))
    return Evidence(
        serialize_comparison(comparison),
        COMPARISON_SCHEMA,
        (("baseline", Provenance("a" * 40, context=(("release", baseline),))), ("current", Provenance("c" * 40, context=(("release", current),)))),
    )


class TestWorkflowConsumer(unittest.TestCase):
    def test_environment_export_rejects_malformed_and_duplicate_names_before_append(self) -> None:
        for raw in ('["GOOD", []]', '["GOOD", {}]', '["GOOD", null]', '["GOOD", "GOOD"]', "[]", '"GOOD"'):
            with self.subTest(names=raw), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "environment"
                original = b"EXISTING=yes\r\n"
                destination.write_bytes(original)
                with self.assertRaisesRegex(ValueError, "invalid environment name|nonempty unique sequence"):
                    export_environment(destination, json.loads(raw), environment={"GOOD": "valid"})
                self.assertEqual(destination.read_bytes(), original)

    def test_cli_measure_and_baseline_with_modeled_git_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.1.0"\n')
            (root / "point.txt").write_bytes(b"5")
            (root / "benchmark.py").write_bytes(
                b"import json\nfrom pathlib import Path\n"
                b"p=Path('target/criterion/work/new/estimates.json')\np.parent.mkdir(parents=True)\n"
                b"p.write_bytes(json.dumps({'median':{'point_estimate':int(Path('point.txt').read_bytes())}}).encode())\n"
            )
            (root / "benchmark.toml").write_text(
                "schema=1\ncommand=[" + json.dumps(sys.executable) + ',"benchmark.py"]\nsources=["benchmark.py","point.txt"]\nharness=["benchmark.py"]\n',
                encoding="utf-8",
                newline="\n",
            )
            created = []

            @contextmanager
            def worktree(source, destination, revision, *, allow_git_mutations):
                self.assertTrue(allow_git_mutations)
                destination.mkdir()
                created.append(destination)
                shutil.copyfile(source / "benchmark.py", destination / "benchmark.py")
                (destination / "point.txt").write_bytes(b"10" if destination.name == "baseline" else b"5")
                try:
                    yield destination
                finally:
                    shutil.rmtree(destination)

            def revision(path, reference):
                return ("a" if reference == "v1.0.0" or path.name == "baseline" else "b") * 40

            base = ["--root", str(root), "performance"]
            with patch("research_repo_tools.measurement.temporary_worktree", worktree), patch("research_repo_tools.measurement.resolve_revision", revision):
                self.assertEqual(
                    main(
                        [
                            *base,
                            "measure",
                            "benchmark.toml",
                            "v1.1.0",
                            "v1.0.0",
                            "--allow-git-mutations",
                            "--payload",
                            "pair.json",
                            "--manifest",
                            "pair.evidence.json",
                        ]
                    ),
                    0,
                )
            self.assertEqual(len(created), 2)
            self.assertTrue(all(not path.exists() for path in created))
            self.assertEqual(parse_comparison((root / "pair.json").read_bytes()).comparisons[0].speedup, 2)
            self.assertFalse((root / "target").exists())

            def verify(root, tag, files, *, revision):
                self.assertEqual((tag, revision, files["point.txt"]), ("v1.1.0", "b" * 40, b"5"))

            with (
                patch("research_repo_tools.measurement.resolve_revision", revision),
                patch("research_repo_tools.performance_workflows.resolve_revision", revision),
                patch("research_repo_tools.process.run_git_bytes", return_value=subprocess.CompletedProcess([], 0, b"", b"")),
                patch("research_repo_tools.publication.verify_tagged_files", verify),
            ):
                self.assertEqual(main([*base, "baseline", "benchmark.toml", "v1.1.0", "baseline.tar.gz"]), 0)
            sample, source = read_baseline(root / "baseline.tar.gz", expected_tag="v1.1.0")
            self.assertEqual(sample.estimates[0][1].point, 5)
            self.assertEqual(dict(source.context)["mode"], "tag")

    def test_legacy_baseline_hashes_identity_and_no_partial_shared_fallback(self) -> None:
        configuration = b"""schema=1
schema-field="schema"
schemas=[1,2]
metadata="criterion/metadata.json"
criterion="criterion"
sample="{tag}"
statistic="median"
unit="ns"
[source]
revision="commit"
release="tag"
record="$"
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            archive = root / "old.tar.gz"
            members = {
                "criterion/metadata.json": deterministic_json({"schema": 1, "tag": "v1.0.0", "commit": "a" * 40}),
                "criterion/work/v1.0.0/estimates.json": b'{"median":{"point_estimate":10,"confidence_interval":{"lower_bound":8,"upper_bound":12,"confidence_level":0.95}}}',
            }

            def write_archive():
                with tarfile.open(archive, "w:gz") as output:
                    for name, data in members.items():
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        output.addfile(member, io.BytesIO(data))
                with tarfile.open(archive) as stored:
                    self.assertEqual(stored.getnames(), list(members))

            write_archive()
            original = archive.read_bytes()
            sample, source = read_baseline(archive, legacy_configuration=configuration, expected_tag="v1.0.0")
            self.assertEqual(sample.estimates[0][1], Estimate(10, 8, 12, 0.95))
            self.assertIsNone(source.source_sha256)
            self.assertIsNone(source.harness_sha256)
            self.assertEqual(dict(source.context)["legacy.archive-sha256"], sha256(original))
            self.assertEqual(archive.read_bytes(), original)
            with self.assertRaisesRegex(ValueError, "release|tag"):
                read_baseline(archive, legacy_configuration=configuration, expected_tag="v1.1.0")
            members["sample.json"] = b"broken shared data"
            write_archive()
            with self.assertRaisesRegex(ValueError, "exactly"):
                read_baseline(archive, legacy_configuration=configuration, expected_tag="v1.0.0")

    def test_measurement_with_real_child_and_modeled_git_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "benchmark.py").write_bytes(
                b"from pathlib import Path\n"
                b"p=Path('target/criterion/workload/new/estimates.json')\np.parent.mkdir(parents=True)\n"
                b'p.write_bytes(b\'{"median":{"point_estimate":2}}\')\n'
            )
            config = MeasurementConfig((sys.executable, "benchmark.py"), ("benchmark.py",), ("benchmark.py",))
            with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40):
                sample, provenance = measure_checkout(root, config, "v1.0.0", mode="tag")
                self.assertEqual(sample, Sample((("workload", Estimate(2)),)))
                self.assertEqual(dict(provenance.context)["fingerprint-schema"], "research-repo-tools/files/v1")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    measure_checkout(root, config, "v1.0.0", mode="tag")

    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "Git mutations disabled by local policy")
    def test_native_binary_snapshot_and_worktree_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "source"
            root.mkdir()

            def git(*args: str) -> bytes:
                return subprocess.run(["git", "--no-pager", "-c", "commit.gpgsign=false", *args], cwd=root, check=True, capture_output=True, timeout=60).stdout

            git("init")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "core.autocrlf", "false")
            (root / "tracked.bin").write_bytes(b"before\0\r\n")
            (root / ".gitattributes").write_bytes(b"* -text\n")
            git("add", ".")
            git("commit", "-m", "fixture")
            (root / "tracked.bin").write_bytes(b"after\xff\0\r\n")
            (root / "new file.txt").write_bytes(b"new\r\n")
            before = git("status", "--porcelain=v1", "-z")
            snapshot = capture_snapshot(root)
            destination = root.parent / "checkout"
            with temporary_worktree(root, destination, snapshot.revision, allow_git_mutations=True) as checkout:
                apply_snapshot(checkout, snapshot)
                self.assertEqual((checkout / "tracked.bin").read_bytes(), b"after\xff\0\r\n")
                self.assertEqual((checkout / "new file.txt").read_bytes(), b"new\r\n")
            self.assertFalse(destination.exists())
            self.assertEqual(git("status", "--porcelain=v1", "-z"), before)

    def test_published_assets_cli_uses_retained_baselines_without_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for tag, point in (("v1.0.0", 10), ("v1.1.0", 5)):
                package_baseline(Sample((("workload", Estimate(point)),)), Provenance("a" * 40, context=(("release", tag),)), root / f"{tag}.tar.gz")

            def download(root, repository, tag, name, destination):
                self.assertEqual(repository, "owner/repo")
                self.assertEqual(name, f"baseline-{tag}.tar.gz")
                shutil.copyfile(root / f"{tag}.tar.gz", destination)

            with patch("research_repo_tools.release_assets.download_release_asset", side_effect=download):
                self.assertEqual(
                    main(
                        [
                            "--root",
                            str(root),
                            "performance",
                            "assets",
                            "v1.1.0",
                            "v1.0.0",
                            "--repository",
                            "owner/repo",
                            "--asset-template",
                            "baseline-{tag}.tar.gz",
                            "--payload",
                            "comparison.json",
                            "--manifest",
                            "manifest.json",
                            "--report",
                            "report.md",
                        ]
                    ),
                    0,
                )
            comparison = parse_comparison((root / "comparison.json").read_bytes())
            self.assertEqual(comparison.comparisons[0].speedup, 2)
            self.assertIn(b"v1.1.0", (root / "report.md").read_bytes())

    def test_legacy_conversion_preserves_originals_and_digest_meaning(self) -> None:
        payload, manifest = legacy()
        converted = convert_csv(payload, manifest, LEGACY_CONFIG)
        comparison = parse_comparison(converted.payload)
        self.assertEqual(comparison.comparisons[0].speedup, 2)
        self.assertEqual(comparison.missing_baseline, ("new",))
        for _, source in converted.sources:
            self.assertIsNone(source.source_sha256)
            self.assertIsNone(source.harness_sha256)
            self.assertEqual(dict(source.context)["legacy.payload-sha256"], sha256(payload))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            convert_csv(payload.replace(b"\r\n", b"\n"), manifest, LEGACY_CONFIG)
        malformed = payload.replace(b"new,added", b"new,paired")
        data = json.loads(manifest)
        data["sha256"] = sha256(malformed)
        with self.assertRaisesRegex(ValueError, "coverage"):
            convert_csv(malformed, deterministic_json(data), LEGACY_CONFIG)

    def test_cli_conversion_promotion_rerender_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            payload, manifest = legacy()
            (root / "legacy.csv").write_bytes(payload)
            (root / "legacy.json").write_bytes(manifest)
            (root / "conversion.toml").write_bytes(LEGACY_CONFIG)
            (root / "report.toml").write_bytes(b'schema=1\ncurrent="docs/PERFORMANCE.md"\narchive="docs/evidence"\ntitle="Timing report"\n')
            base = ["--root", str(root), "performance"]
            self.assertEqual(
                main([*base, "convert", "conversion.toml", "legacy.csv", "legacy.json", "--payload", "new.json", "--manifest", "new.evidence.json"]), 0
            )
            self.assertEqual((root / "legacy.csv").read_bytes(), payload)
            self.assertEqual((root / "legacy.json").read_bytes(), manifest)
            self.assertEqual(main([*base, "promote", "report.toml", "--payload", "new.json", "--manifest", "new.evidence.json"]), 0)
            report = (root / "docs/PERFORMANCE.md").read_bytes()
            self.assertIn(b"Baseline/current", report)
            self.assertEqual(main([*base, "promote", "report.toml", "--check"]), 0)
            self.assertEqual((root / "docs/PERFORMANCE.md").read_bytes(), report)
            self.assertEqual(main([*base, "export", "new.json", "new.evidence.json", "analysis.csv"]), 0)
            rows = list(csv.DictReader(io.StringIO((root / "analysis.csv").read_text(encoding="utf-8"), newline="")))
            self.assertEqual({row["coverage"] for row in rows}, {"common", "added"})
            self.assertEqual({row["unit"] for row in rows}, {"ns"})

    def test_promotion_archives_previous_pair_and_rejects_stale_or_conflicting_plans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / "report.toml"
            config.write_bytes(b'schema=1\ncurrent="docs/PERFORMANCE.md"\narchive="docs/evidence"\ntitle="Timings"\n')
            publish_evidence(retained(), root / "new.json", root / "new.evidence.json")
            publish_publication(load_report_plan(root, "report.toml", payload="new.json", manifest="new.evidence.json"))
            first = (root / "docs/PERFORMANCE.md").read_bytes()
            publish_evidence(retained("v1.2.0", "v1.1.0"), root / "new.json", root / "new.evidence.json")
            plan = load_report_plan(root, "report.toml", payload="new.json", manifest="new.evidence.json")
            (root / "new.json").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                publish_publication(plan)
            self.assertEqual((root / "docs/PERFORMANCE.md").read_bytes(), first)
            publish_evidence(retained("v1.2.0", "v1.1.0"), root / "new.json", root / "new.evidence.json")
            publish_publication(load_report_plan(root, "report.toml", payload="new.json", manifest="new.evidence.json"))
            archived = root / "docs/evidence/v1.1.0-vs-v1.0.0.md"
            self.assertIn(b"](v1.1.0-vs-v1.0.0.comparison.json)", archived.read_bytes())
            self.assertIn(b"v1.1.0-vs-v1.0.0.md", (root / "docs/evidence/README.md").read_bytes())
            publish_evidence(retained(point=6), root / "new.json", root / "new.evidence.json")
            with self.assertRaisesRegex(ValueError, "immutable"):
                load_report_plan(root, "report.toml", payload="new.json", manifest="new.evidence.json")

    def test_current_report_can_share_its_archive_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "report.toml").write_bytes(b'schema=1\ncurrent="docs/evidence/current.md"\narchive="docs/evidence"\ntitle="Timings"\n')
            publish_evidence(retained(), root / "new.json", root / "new.evidence.json")
            publish_publication(load_report_plan(root, "report.toml", payload="new.json", manifest="new.evidence.json"))
            current = root / "docs/evidence/current.md"
            first = current.read_bytes()
            self.assertEqual(publish_publication(load_report_plan(root, "report.toml")), ())
            self.assertNotIn(b"current.md", (root / "docs/evidence/README.md").read_bytes())
            publish_evidence(retained("v1.2.0", "v1.1.0"), root / "new.json", root / "new.evidence.json")
            publish_publication(load_report_plan(root, "report.toml", payload="new.json", manifest="new.evidence.json"))
            self.assertNotEqual(current.read_bytes(), first)
            self.assertEqual((root / "docs/evidence/v1.1.0-vs-v1.0.0.md").read_bytes(), first)
            self.assertEqual((root / "docs/evidence/README.md").read_bytes(), b"# Archived performance reports\n\n- [v1.1.0-vs-v1.0.0](v1.1.0-vs-v1.0.0.md)\n")
            self.assertEqual(publish_publication(load_report_plan(root, "report.toml")), ())

    def test_baseline_asset_environment_and_configured_example(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sample = Sample((("example", Estimate(2, 1, 4, 0.95)),), "mean", "us")
            provenance = Provenance("a" * 40, context=(("release", "v1.0.0"),))
            package_baseline(sample, provenance, root / "baseline.tar.gz")
            self.assertEqual(read_baseline(root / "baseline.tar.gz"), (sample, provenance))
            export_environment(root / "environment", ["PATH"], environment={"PATH": "C:\\Program Files\\bin;D:\\bin"})
            self.assertEqual((root / "environment").read_bytes(), b"PATH=C:\\Program Files\\bin;D:\\bin\n")
            config = f'schema=1\n[[checks]]\nname="example"\ncommand=[{json.dumps(sys.executable)}, "-c", "print(12345)"]\nexpect=["12345"]\n'
            (root / "checks.toml").write_text(config, encoding="utf-8", newline="\n")
            run_checks(root, "checks.toml")
            (root / "checks.toml").write_text(config.replace('expect=["12345"]', 'expect=["missing"]'), encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "missing expected"):
                run_checks(root, "checks.toml")
            self.assertEqual(argument_batches(["lint"], ["space name", "-option"], batch_size=1), (("lint", "./space name"), ("lint", "./-option")))


if __name__ == "__main__":
    unittest.main()
