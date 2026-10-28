"""First-use report checks run unchanged from source, wheel and sdist."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cli import main
from research_repo_tools.criterion import COMPARISON_SCHEMA, Estimate, Sample, compare_samples, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance, publish_evidence
from research_repo_tools.performance_reports import load_report_plan
from research_repo_tools.publication import publish_publication
from research_repo_tools.report_inventory import check_report


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None for path in root.rglob("*")}


class TestReportInventory(unittest.TestCase):
    def fixture(self, root, schema=1):
        (root / "report.toml").write_bytes(
            f'schema={schema}\ncurrent="docs/history/current.md"\narchive="docs/history"\ntitle="Timings"\n'
            '[initialization]\nallow-empty=true\ndirectories=["scratch", "figures"]\nguards=["publication.toml"]\n'.encode()
        )

    def test_empty_cli_and_api_are_read_only_and_explicit(self):
        for schema in (1, 2):
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.fixture(root, schema)
                before = snapshot(root)
                with patch("research_repo_tools.measurement.measure_pair", side_effect=AssertionError("check measured")):
                    self.assertEqual(check_report(root, "report.toml").status, "uninitialized")
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(["--root", str(root), "performance", "promote", "report.toml", "--check"]), 0)
                self.assertIn("uninitialized", output.getvalue())
                self.assertEqual(snapshot(root), before)
                (root / "report.toml").write_bytes((root / "report.toml").read_bytes().split(b"[initialization]")[0])
                with self.assertRaises(ValueError):
                    check_report(root, "report.toml")

    def test_every_orphan_and_partial_directory_rejects(self):
        for schema in (1, 2):
            for name in (
                "docs/history/a.comparison.json",
                "docs/history/a.evidence.json",
                "docs/history/a.csv",
                "docs/history/old.md",
                "docs/history/index.json",
                "docs/history/latest.json",
                "docs/history/README.md",
                "figures/plot.svg",
                "scratch/run.json",
                "publication.toml",
                "docs/history/runs/partial/",
            ):
                with self.subTest(schema=schema, name=name), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.fixture(root, schema)
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.mkdir() if name.endswith("/") else path.write_bytes(b"orphan")
                    before = snapshot(root)
                    with self.assertRaisesRegex(ValueError, "orphaned"):
                        check_report(root, "report.toml")
                    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                        self.assertEqual(main(["--root", str(root), "performance", "promote", "report.toml", "--check"]), 1)
                    self.assertEqual(snapshot(root), before)

    def test_unsafe_aliases_and_dangling_links_never_mean_absence(self):
        for replacement in ('guards=["Report.toml"]', 'guards=["../escape"]', 'guards=["docs/history"]', 'guards=["a", "A"]'):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.fixture(root)
                path = root / "report.toml"
                path.write_bytes(path.read_bytes().replace(b'guards=["publication.toml"]', replacement.encode()))
                with self.assertRaises(ValueError):
                    check_report(root, "report.toml")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            root = root.resolve()
            # Model links on hosts without link privileges; real links are also
            # exercised by the common publication/path suite when supported.
            original = Path.is_symlink
            for name in ("publication.toml", "docs", "scratch"):
                with self.subTest(name=name), patch.object(Path, "is_symlink", lambda path: path == root / name or original(path)):
                    with self.assertRaisesRegex(ValueError, "link"):
                        check_report(root, "report.toml")

    def test_initialized_validation_remains_strict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            payload = serialize_comparison(compare_samples(Sample((("case", Estimate(10)),)), Sample((("case", Estimate(5)),))))
            retained = Evidence(
                payload,
                COMPARISON_SCHEMA,
                (("baseline", Provenance("a" * 40, context=(("release", "v1.0.0"),))), ("current", Provenance("b" * 40, context=(("release", "v1.1.0"),)))),
            )
            publish_evidence(retained, root / "scratch/run.json", root / "scratch/evidence.json")
            publish_publication(load_report_plan(root, "report.toml", payload="scratch/run.json", manifest="scratch/evidence.json"))
            before = snapshot(root)
            self.assertEqual(check_report(root, "report.toml").status, "current")
            self.assertEqual(snapshot(root), before)
            current = root / "docs/history/current.md"
            current.write_bytes(current.read_bytes() + b"stale\n")
            self.assertEqual(check_report(root, "report.toml").status, "stale")
            envelope = next((root / "docs/history").glob("*.evidence.json"))
            envelope.write_bytes(b"{}")
            before = snapshot(root)
            with self.assertRaises(ValueError):
                check_report(root, "report.toml")
            self.assertEqual(snapshot(root), before)

    def test_case_alias_of_an_absent_declared_directory_is_not_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.fixture(root)
            (root / "Scratch").mkdir()
            (root / "Scratch/run.json").write_bytes(b"orphan")
            with self.assertRaisesRegex(ValueError, "alias"):
                check_report(root, "report.toml")


if __name__ == "__main__":
    unittest.main()
