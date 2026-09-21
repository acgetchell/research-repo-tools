"""Public-only consumer checks, also run from isolated installed distributions."""

import importlib
import io
import json
import random
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.criterion import (
    COMPARISON_SCHEMA,
    Estimate,
    Sample,
    collect_sample,
    compare_samples,
    parse_comparison,
    parse_estimate,
    render_comparison,
    serialize_comparison,
)
from research_repo_tools.evidence import (
    Evidence,
    Provenance,
    compare_provenance,
    deterministic_json,
    fingerprint_files,
    load_evidence,
    parse_evidence,
    publish_evidence,
    serialize_evidence,
    sha256,
    verify_sha256,
)


class TestPerformanceConsumer(unittest.TestCase):
    def test_comparison_and_retained_rendering(self) -> None:
        # Reduced Criterion shapes used by MCMC and la-stack: median or mean,
        # complete intervals, optional standard error, plus incomplete coverage.
        baseline = parse_estimate(b'{"median":{"point_estimate":100,"confidence_interval":{"lower_bound":90,"upper_bound":110,"confidence_level":0.95}}}')
        current = parse_estimate(b'{"mean":{"point_estimate":80,"standard_error":2}}', statistic="mean")
        result = compare_samples(Sample((("gone", Estimate(1)), ("step/2", baseline))), Sample((("step/2", current), ("added", Estimate(3)))))
        (row,) = result.comparisons
        self.assertEqual((row.speedup, row.percent_reduction), (1.25, 20.0))
        self.assertEqual(result.missing_baseline, ("added",))
        self.assertEqual(result.missing_current, ("gone",))
        encoded = serialize_comparison(result)
        self.assertEqual(parse_comparison(encoded), result)
        self.assertEqual(serialize_comparison(parse_comparison(encoded)), encoded)
        rendered = render_comparison(parse_comparison(encoded))
        self.assertIn("| step/2 | common |", rendered)
        self.assertIn("| 1.25 | 20 |", rendered)
        self.assertIn("| added | added |", rendered)
        self.assertIn("| gone | missing |", rendered)

    def test_exact_byte_envelope_and_promotion(self) -> None:
        # Consumer schemas remain opaque, including legacy CSV and CRLF.
        payload = b"benchmark,point_ns\r\nstep,100\r\n"
        source = Provenance("a" * 40, "b" * 64, "c" * 64, (("os", "Linux"), ("scope", "release-signal")))
        artifact = Evidence(payload, "consumer/retained-csv/v1", (("current", source),))
        data, sidecar = serialize_evidence(artifact)
        self.assertEqual(data, payload)
        self.assertEqual(json.loads(sidecar)["payload_sha256"], sha256(payload))
        self.assertEqual(parse_evidence(data, sidecar), artifact)
        # A readable, noncanonical manifest must survive promotion byte for byte.
        retained_sidecar = json.dumps(json.loads(sidecar), indent=4).replace("\n", "\r\n").encode()
        retained = parse_evidence(payload, retained_sidecar)
        self.assertEqual(serialize_evidence(retained), (payload, retained_sidecar))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_path, manifest_path = root / "archive.csv", root / "archive.json"
            report = root / "PERFORMANCE.md"
            publish_evidence(retained, data_path, manifest_path, reports={report: b"retained report\n"}, immutable=(data_path, manifest_path))
            self.assertEqual(load_evidence(data_path, manifest_path), retained)
            self.assertEqual(manifest_path.read_bytes(), retained_sidecar)
            publish_evidence(retained, data_path, manifest_path, immutable=(data_path, manifest_path))
            with self.assertRaisesRegex(ValueError, "immutable"):
                publish_evidence(
                    Evidence(b"changed", retained.payload_schema, retained.sources), data_path, manifest_path, immutable=(data_path, manifest_path)
                )
            self.assertEqual(data_path.read_bytes(), payload)
            self.assertEqual(report.read_bytes(), b"retained report\n")

    def test_provenance_is_explicit_and_missing_is_not_compatible(self) -> None:
        baseline = Provenance("a" * 40, harness_sha256="b" * 64, context=(("cpu", "test CPU"),))
        current = Provenance("c" * 40, harness_sha256="b" * 64, context=(("cpu", "test CPU"),))
        self.assertTrue(compare_provenance(baseline, current, fields=("harness_sha256", "context.cpu")).compatible)
        self.assertFalse(compare_provenance(baseline, current, fields=("revision",)).compatible)
        missing = compare_provenance(baseline, current, fields=("context.rustc", "source_sha256"))
        self.assertEqual(tuple(row.field for row in missing.differences), ("context.rustc", "source_sha256"))
        self.assertFalse(missing.compatible)

    def test_publication_aliases_cannot_replace_immutable_evidence(self) -> None:
        artifact = Evidence(b"retained\r\n\xff", "consumer/v1", (("current", Provenance("a" * 40)),))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload, manifest = root / "data", root / "manifest"
            publish_evidence(artifact, payload, manifest)
            before = {path: path.read_bytes() for path in root.iterdir()}
            for sidecar, reports in ((root / "DATA", {}), (manifest, {root / "DATA": b"report"})):
                with self.subTest(sidecar=sidecar, reports=reports), self.assertRaisesRegex(ValueError, "duplicate"):
                    publish_evidence(artifact, payload, sidecar, reports=reports, immutable=(payload,))
                self.assertEqual({path: path.read_bytes() for path in root.iterdir()}, before)
            # The protection itself can name a case alias, including on hosts
            # where that spelling would otherwise refer to a missing file.
            publish_evidence(artifact, payload, manifest, immutable=(root / "DATA",))
            with self.assertRaisesRegex(ValueError, "immutable"):
                publish_evidence(Evidence(b"changed", artifact.payload_schema, artifact.sources), payload, manifest, immutable=(root / "DATA",))
            self.assertEqual({path: path.read_bytes() for path in root.iterdir()}, before)

    def test_mutable_envelope_buffers_are_rejected(self) -> None:
        artifact = Evidence(b"retained", "consumer/v1", (("current", Provenance("a" * 40)),))
        payload, manifest = serialize_evidence(artifact)
        with self.assertRaisesRegex(TypeError, "manifest must be bytes"):
            parse_evidence(payload, bytearray(manifest))  # ty: ignore[invalid-argument-type]
        with self.assertRaisesRegex(TypeError, "payload must be bytes"):
            parse_evidence(bytearray(payload), manifest)  # ty: ignore[invalid-argument-type]

    def test_hashing_and_fingerprints_preserve_source_bytes(self) -> None:
        self.assertEqual(sha256(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(deterministic_json({"z": 1, "a": "é"}), b'{\n  "a": "\xc3\xa9",\n  "z": 1\n}\n')
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            verify_sha256(b"a\r\n", sha256(b"a\n"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.rs").write_bytes(b"a\r\n\x00\xff")
            (root / "harness.rs").write_bytes(b"harness")
            first = fingerprint_files(root, (Path("source.rs"), Path("harness.rs")))
            self.assertEqual(first, fingerprint_files(root, (Path("harness.rs"), Path("source.rs"))))
            (root / "source.rs").write_bytes(b"a\n\x00\xff")
            self.assertNotEqual(first, fingerprint_files(root, (Path("source.rs"), Path("harness.rs"))))

    def test_archive_to_sample_to_artifact(self) -> None:
        estimates = b'{"median":{"point_estimate":42}}'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for extension in ("tar.gz", "zip"):
                asset = root / f"release.{extension}"
                name = "./criterion/step/2/new/estimates.json"
                if extension == "tar.gz":
                    with tarfile.open(asset, "w:gz") as archive:
                        member = tarfile.TarInfo(name)
                        member.size = len(estimates)
                        archive.addfile(member, io.BytesIO(estimates))
                else:
                    with zipfile.ZipFile(asset, "w") as archive:
                        archive.writestr(name, estimates)
                output = root / f"extracted-{extension}"
                extract_archive(asset, output, limits=ArchiveLimits(content_bytes=1024), expected_sha256=sha256(asset.read_bytes()))
                sample = collect_sample(output / "criterion", "new")
                self.assertEqual(sample.estimates, (("step/2", Estimate(42)),))
                comparison = compare_samples(sample, sample)
                artifact = Evidence(serialize_comparison(comparison), COMPARISON_SCHEMA, (("current", Provenance("a" * 40)),))
                self.assertEqual(parse_comparison(parse_evidence(*serialize_evidence(artifact)).payload), comparison)

    def _assert_corrupt_compressed_tar(self, module_name: str) -> None:
        try:
            codec = importlib.import_module(module_name)
        except ImportError:
            self.skipTest(f"Python was built without {module_name}")
        stream = io.BytesIO()
        generator = random.Random(28)
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for index in range(3):
                data = generator.randbytes(100_000)
                member = tarfile.TarInfo(f"file{index}")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        options = {} if module_name == "lzma" else {"options": {codec.CompressionParameter.checksum_flag: 1}}
        corrupted = bytearray(codec.compress(stream.getvalue(), **options))
        corrupted[40_000] ^= 128
        # The first header is valid; only subsequent decoding detects corruption.
        with tarfile.open(fileobj=io.BytesIO(corrupted), mode="r:*") as archive:
            first = archive.next()
            assert first is not None
            self.assertEqual(first.name, "file0")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "asset"
            asset.write_bytes(corrupted)
            with self.assertRaisesRegex(ValueError, "invalid release archive"):
                extract_archive(asset, root / "output")
            result = subprocess.run(
                [sys.executable, "-I", "-m", "research_repo_tools", "--root", str(root), "performance", "extract", "asset", "output"],
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"invalid release archive", result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(list(root.iterdir()), [asset])

    def test_corrupt_xz_has_a_normal_archive_error(self) -> None:
        self._assert_corrupt_compressed_tar("lzma")

    def test_corrupt_zstd_has_a_normal_archive_error(self) -> None:
        self._assert_corrupt_compressed_tar("compression.zstd")


if __name__ == "__main__":
    unittest.main()
