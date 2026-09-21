"""Publication contracts exercised against the checkout, installed wheel, and sdist."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cli import main
from research_repo_tools.criterion import COMPARISON_SCHEMA, Estimate, Sample, compare_samples, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance, serialize_evidence
from research_repo_tools.publication import (
    GitCheck,
    MarkerPair,
    TableLayout,
    plan_publication,
    preview_publication,
    publish_publication,
    render_svg,
    render_table,
    replace_section,
    verify_tagged_files,
)
from research_repo_tools.publication_config import load_publication
from research_repo_tools.releases import ReleaseRule

MARKERS = MarkerPair("<!-- PERFORMANCE:BEGIN -->", "<!-- PERFORMANCE:END -->")
DOCUMENT = b"\xef\xbb\xbf# Results\r\n[Historical](old/v0.9/report.md)\r\n" + MARKERS.begin.encode() + b"\r\nstale\r\n" + MARKERS.end.encode() + b"\r\nTail\r\n"
LAYOUT = TableLayout((("step/2", "Step | <two>"), ("tiny", "Tiny")), "v1.0.0", "v1.1.0", "ns")


def comparison():
    return compare_samples(
        Sample((("step/2", Estimate(100, 90, 110, 0.95)), ("tiny", Estimate(3)), ("gone", Estimate(2)))),
        Sample((("step/2", Estimate(80)), ("tiny", Estimate(6)), ("added", Estimate(4)))),
    )


def make_fixture(root: Path) -> str:
    root.joinpath("README.md").write_bytes(DOCUMENT)
    root.joinpath("Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.1.0"\n')
    root.joinpath("report.md").write_bytes(b"Baseline: v1.0.0\r\nCurrent: v1.1.0\r\n")
    retained = Evidence(
        serialize_comparison(comparison()),
        COMPARISON_SCHEMA,
        (
            ("baseline", Provenance("a" * 40, harness_sha256="c" * 64, context=(("release", "v1.0.0"),))),
            ("current", Provenance("b" * 40, harness_sha256="c" * 64, context=(("release", "v1.1.0"),))),
        ),
    )
    payload, manifest = serialize_evidence(retained)
    root.joinpath("evidence.json").write_bytes(payload)
    root.joinpath("manifest.json").write_bytes(manifest)
    configuration = f'''schema = 1
document = "README.md"
payload = "evidence.json"
manifest = "manifest.json"
begin = "{MARKERS.begin}"
end = "{MARKERS.end}"
unit = "ns"
svg = "figures/timing.svg"
rows = [{{benchmark = "step/2", label = "Step | <two>"}}, {{benchmark = "tiny", label = "Tiny"}}]
links = [{{label = "Report", path = "report.md"}}, {{label = "Evidence", path = "evidence.json"}}]

[provenance.baseline]
revision = "{"a" * 40}"
release = "v1.0.0"
harness-sha256 = "{"c" * 64}"

[provenance.current]
revision = "{"b" * 40}"
release = "v1.1.0"
harness-sha256 = "{"c" * 64}"

[[references]]
path = "Cargo.toml"
pattern = '^version="(?P<value>[^"]+)"\\r?$'
source = "version"

[[references]]
path = "report.md"
pattern = '^Baseline: (?P<value>[^\\r\\n]+)'
source = "previous-tag"

[[references]]
path = "report.md"
pattern = '^Current: (?P<value>[^\\r\\n]+)'
source = "tag"
'''
    root.joinpath("publication.toml").write_text(configuration, encoding="utf-8", newline="\n")
    return configuration


def fixture_git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    """Run fixture Git without inheriting commit or tag signing preferences."""
    return subprocess.run(
        ["git", "--no-pager", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        input=input,
        cwd=root,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout.strip()


class TestPublicationConsumer(unittest.TestCase):
    def test_fixture_git_overrides_inherited_signing(self) -> None:
        # Reading effective configuration needs no repository or Git mutation.
        inherited = {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "commit.gpgsign",
            "GIT_CONFIG_VALUE_0": "true",
            "GIT_CONFIG_KEY_1": "tag.gpgsign",
            "GIT_CONFIG_VALUE_1": "true",
        }
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, inherited):
            root = Path(temporary)
            for key in ("commit.gpgsign", "tag.gpgsign"):
                original = subprocess.run(["git", "--no-pager", "config", "--get", key], cwd=root, check=True, capture_output=True, timeout=30)
                self.assertEqual(original.stdout.strip(), b"true")
                self.assertEqual(fixture_git(root, "config", "--get", key), b"false")

    def test_preview_preserves_utf8_and_newlines_on_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configuration = make_fixture(root).replace('label = "Tiny"', 'label = "café 分析"')
            (root / "publication.toml").write_text(configuration, encoding="utf-8", newline="\n")
            expected = preview_publication(load_publication(root, "publication.toml")).encode("utf-8")
            self.assertIn("café 分析".encode(), expected)
            self.assertIn(b"\xef\xbb\xbf# Results\r\n", expected)
            command = ["--root", str(root), "performance", "publish", "publication.toml", "--preview"]
            for encoding in ("utf-8", "cp1252"):
                for newline in ("\n", "\r\n"):
                    with self.subTest(encoding=encoding, newline=newline):
                        with io.BytesIO() as buffer, io.TextIOWrapper(buffer, encoding=encoding, newline=newline) as stdout:
                            with patch("sys.stdout", stdout):
                                self.assertEqual(main(command), 0)
                            stdout.flush()
                            self.assertEqual(buffer.getvalue(), expected)
            with io.StringIO() as stdout, patch("sys.stdout", stdout):
                self.assertEqual(main(command), 0)
                self.assertEqual(stdout.getvalue(), expected.decode("utf-8"))
            self.assertEqual((root / "README.md").read_bytes(), DOCUMENT)
            self.assertFalse((root / "figures").exists())

    def test_reference_selector_accepts_lf_and_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_fixture(root)
            for newline in (b"\n", b"\r\n"):
                with self.subTest(newline=newline):
                    cargo = b'[package]\nversion="1.1.0"\n'.replace(b"\n", newline)
                    (root / "Cargo.toml").write_bytes(cargo)
                    self.assertTrue(load_publication(root, "publication.toml").changed_paths)
                    self.assertEqual((root / "Cargo.toml").read_bytes(), cargo)

    def test_exact_surrounding_bytes_and_deterministic_rendering(self) -> None:
        table = render_table(comparison(), LAYOUT)
        self.assertIn("| Step &#124; &lt;two&gt; | 100 ns [90, 110] (0.95 confidence) | 80 ns | 1.25 | 20 |", table)
        self.assertIn("| Tiny | 3 ns | 6 ns | 0.5 | -100 |", table)
        self.assertIn("2 selected of 2 comparable; 1 current-only; 1 baseline-only", table)
        updated = replace_section(DOCUMENT, MARKERS, table)
        prefix = DOCUMENT.split(MARKERS.begin.encode())[0] + MARKERS.begin.encode()
        suffix = MARKERS.end.encode() + DOCUMENT.split(MARKERS.end.encode())[1]
        self.assertTrue(updated.startswith(prefix + b"\n\n"))
        self.assertTrue(updated.endswith(b"\n\n" + suffix))
        self.assertEqual(replace_section(updated, MARKERS, table), updated)
        svg = render_svg(comparison(), LAYOUT)
        self.assertEqual(svg, render_svg(comparison(), LAYOUT))
        parsed = ET.fromstring(svg)
        labels = [node.text for node in parsed.iter("{http://www.w3.org/2000/svg}text")]
        self.assertIn("Step | <two>", labels)
        widths = [float(node.attrib["width"]) for node in parsed.iter("{http://www.w3.org/2000/svg}rect")]
        self.assertEqual(widths, [360, 144])

    def test_legacy_adapter_and_snapshot_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_bytes(DOCUMENT)
            # A consumer can retain its CSV/provenance parser and custom renderer.
            payload = b"benchmark,time\r\nstep,80\r\n"
            (root / "legacy.csv").write_bytes(payload)
            plan = plan_publication(
                root,
                "README.md",
                MARKERS,
                "Consumer narrative\n" + render_table(comparison(), LAYOUT),
                inputs={"legacy.csv": payload},
                figures={"figures/timing.svg": render_svg(comparison(), LAYOUT)},
                references=(ReleaseRule("README.md", r"(?P<value>old/v[^/]+/report.md)", value="old/v0.9/report.md"),),
            )
            self.assertIn("Consumer narrative", preview_publication(plan))
            self.assertFalse((root / "figures").exists())
            self.assertEqual((root / "README.md").read_bytes(), DOCUMENT)
            (root / "legacy.csv").write_bytes(payload.replace(b"\r\n", b"\n"))
            with self.assertRaisesRegex(ValueError, "changed after planning"):
                publish_publication(plan)
            self.assertEqual((root / "README.md").read_bytes(), DOCUMENT)
            self.assertFalse((root / "figures").exists())
            (root / "legacy.csv").write_bytes(payload)
            self.assertEqual(len(publish_publication(plan)), 2)
            self.assertEqual((root / "legacy.csv").read_bytes(), payload)

    def test_configuration_cli_check_preview_publish_and_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_fixture(root)
            plan = load_publication(root, "publication.toml")
            self.assertEqual(plan.changed_paths, (root.resolve() / "README.md", root.resolve() / "figures/timing.svg"))
            command = [sys.executable, "-I", "-m", "research_repo_tools", "--root", str(root), "performance", "publish", "publication.toml"]

            def run(*options):
                return subprocess.run([*command, *options], cwd=root, capture_output=True, timeout=30)

            stale = run("--check")
            self.assertEqual(stale.returncode, 1, stale.stderr)
            self.assertIn(b"Stale publication", stale.stdout)
            preview = run("--preview")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(preview.stdout, preview_publication(plan).encode("utf-8"))
            self.assertEqual((root / "README.md").read_bytes(), DOCUMENT)
            self.assertFalse((root / "figures").exists())
            published = run()
            self.assertEqual(published.returncode, 0, published.stderr)
            for name, data in plan.outputs:
                self.assertEqual((root / name).read_bytes(), data)
            self.assertEqual(run("--check").returncode, 0)
            self.assertIn(b"already current", run().stdout)
            (root / "report.md").write_bytes(b"Baseline: v1.0.0\nCurrent: v9.0.0\n")
            before = (root / "README.md").read_bytes()
            failure = run()
            self.assertEqual(failure.returncode, 1)
            self.assertIn(b"publication reference", failure.stderr)
            self.assertNotIn(b"Traceback", failure.stderr)
            self.assertEqual((root / "README.md").read_bytes(), before)


@unittest.skipIf(
    os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1",
    "Git mutations disabled by RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS=1",
)
class TestGitPublicationConsumer(unittest.TestCase):
    def test_git_verifies_stored_bytes_without_filters_or_text_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*args, input=None):
                return fixture_git(root, *args, input=input)

            # Mutating Git is confined to a disposable fixture, never a checkout.
            git("init", "--quiet")
            git("config", "user.name", "Publication fixture")
            git("config", "user.email", "fixture@example.test")
            git("config", "core.autocrlf", "false")
            payload = b"\xff\x00\r\nexact retained bytes\r\n"
            (root / "asset.bin").write_bytes(payload)
            (root / "README.md").write_bytes(DOCUMENT)
            git("add", "asset.bin", "README.md")
            git("commit", "--quiet", "-m", "fixture")
            git("tag", "v1.1.0")
            revision = git("rev-parse", "HEAD").decode("ascii")
            # A clean filter must not be applied while verifying stored objects.
            (root / ".gitattributes").write_bytes(b"asset.bin text eol=lf\n")
            self.assertEqual(verify_tagged_files(root, "v1.1.0", {"asset.bin": payload}, revision=revision), revision)
            with self.assertRaisesRegex(ValueError, "exact publication artifact"):
                verify_tagged_files(root, "v1.1.0", {"asset.bin": payload.replace(b"\r\n", b"\n")})
            with self.assertRaisesRegex(ValueError, "source revision"):
                verify_tagged_files(root, "v1.1.0", {}, revision="a" * 40)
            with self.assertRaisesRegex(ValueError, "existing local tag"):
                verify_tagged_files(root, "v9.9.9", {"asset.bin": payload})
            with self.assertRaisesRegex(ValueError, "invalid publication tag"):
                verify_tagged_files(root, "v1.1.0~1", {"asset.bin": payload})
            # Model a stored symlink without requiring native symlink privileges.
            link_blob = git("hash-object", "-w", "--stdin", input=b"asset.bin").decode("ascii")
            git("update-index", "--add", "--cacheinfo", f"120000,{link_blob},link.bin")
            git("commit", "--quiet", "-m", "link fixture")
            git("tag", "v1.2.0")
            with self.assertRaisesRegex(ValueError, "regular publication artifact"):
                verify_tagged_files(root, "v1.2.0", {"link.bin": b"asset.bin"})
            (root / "asset.bin").write_bytes(b"substitute content\n")
            git("add", "asset.bin")
            git("commit", "--quiet", "-m", "substitute object fixture")
            substitute = git("rev-parse", "HEAD").decode("ascii")
            git("replace", revision, substitute)
            self.assertEqual(verify_tagged_files(root, "v1.1.0", {"asset.bin": payload}, revision=revision), revision)
            with self.assertRaisesRegex(ValueError, "exact publication artifact"):
                verify_tagged_files(root, "v1.1.0", {"asset.bin": b"substitute content\n"})
            (root / "asset.bin").write_bytes(payload)
            plan = plan_publication(
                root, "README.md", MARKERS, "verified", inputs={"asset.bin": payload}, git_checks=(GitCheck("v1.1.0", ("asset.bin",), revision),)
            )
            git("tag", "--delete", "v1.1.0")
            with self.assertRaisesRegex(ValueError, "existing local tag"):
                publish_publication(plan)
            self.assertEqual((root / "README.md").read_bytes(), DOCUMENT)

    def test_tagged_links_require_the_generated_artifacts_at_the_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configuration = make_fixture(root)
            local = load_publication(root, "publication.toml")
            publish_publication(local)

            def git(*args):
                return fixture_git(root, *args)

            git("init", "--quiet")
            git("config", "user.name", "Publication fixture")
            git("config", "user.email", "fixture@example.test")
            git("config", "core.autocrlf", "false")
            git("add", "Cargo.toml", "report.md", "evidence.json", "manifest.json", "figures/timing.svg")
            git("commit", "--quiet", "-m", "retained artifacts")
            tagged_config = 'repository = "example/project"\n' + configuration
            (root / "publication.toml").write_text(tagged_config, encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "existing local tag"):
                load_publication(root, "publication.toml")
            git("tag", "v1.1.0")
            tagged = load_publication(root, "publication.toml")
            contents = dict(tagged.outputs)["README.md"]
            self.assertIn(b"https://github.com/example/project/blob/v1.1.0/report.md", contents)
            self.assertIn(b"https://raw.githubusercontent.com/example/project/v1.1.0/figures/timing.svg", contents)
            publish_publication(tagged)
            self.assertFalse(load_publication(root, "publication.toml").changed_paths)
            original = (root / "README.md").read_bytes()
            # A rendering change is a different artifact even with identical timings.
            (root / "publication.toml").write_text(tagged_config.replace('label = "Tiny"', 'label = "New label"'), encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "exact publication artifact figures/timing.svg"):
                load_publication(root, "publication.toml")
            self.assertEqual((root / "README.md").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
