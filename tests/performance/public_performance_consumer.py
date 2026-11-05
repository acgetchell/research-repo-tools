"""Public-only consumer checks, also run from isolated installed distributions."""

import importlib
import io
import json
import math
import os
import random
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path
from typing import cast
from unittest.mock import patch

from research_repo_tools.archives import ArchiveLimits, download_asset, extract_archive
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
from research_repo_tools.measurement import MeasurementConfig, measure_checkout
from research_repo_tools.publication import plan_outputs


def zip_asset(path: Path, entries: list[tuple[str, bytes]]) -> None:
    """Write a ZIP fixture and verify its original member names survived."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries:
            member = zipfile.ZipInfo(name)
            # ZipInfo normalizes Windows separators. Malformed fixtures need
            # the original spelling in both ZIP headers on every platform.
            member.filename = name
            archive.writestr(member, payload)
    with zipfile.ZipFile(path) as archive:
        assert [member.orig_filename for member in archive.infolist()] == [name for name, _ in entries], "ZIP fixture changed its member names"


class TestPerformanceConsumer(unittest.TestCase):
    def test_failed_download_closes_error_response_and_can_retry(self):
        url = "https://example.invalid/asset.zip"
        payload = b"verified asset\r\n"

        class Response(io.BytesIO):
            def geturl(self):
                return url

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "asset.zip"
            target.write_bytes(b"previous asset\r\n")
            body = io.BytesIO(b"service unavailable")
            error = urllib.error.HTTPError(url, 503, "unavailable", Message(), body)
            response = Response(payload)
            with patch("urllib.request.OpenerDirector.open", side_effect=[error, response]) as transport:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    download_asset(url, target, expected_sha256=sha256(payload))
                self.assertIs(caught.exception, error)
                self.assertTrue(body.closed)
                self.assertEqual(target.read_bytes(), b"previous asset\r\n")
                self.assertEqual(transport.call_count, 1)
                download_asset(url, target, expected_sha256=sha256(payload))
                self.assertEqual(transport.call_count, 2)
            self.assertTrue(response.closed)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_download_preserves_http_and_cleanup_failures(self):
        with tempfile.TemporaryDirectory() as directory, io.BytesIO(b"unavailable") as body:
            target = Path(directory) / "asset.zip"
            target.write_bytes(b"previous asset\r\n")
            error = urllib.error.HTTPError("https://example.invalid/asset.zip", 503, "unavailable", Message(), body)
            cleanup = OSError("response close failed")
            with (
                patch("urllib.request.OpenerDirector.open", side_effect=error),
                patch.object(error, "close", side_effect=cleanup) as close,
                self.assertRaisesRegex(ExceptionGroup, "HTTP request and response cleanup failed") as caught,
            ):
                download_asset(error.url, target, expected_sha256="0" * 64)
            self.assertEqual(caught.exception.exceptions, (error, cleanup))
            close.assert_called_once_with()
            self.assertEqual(target.read_bytes(), b"previous asset\r\n")
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_invalid_download_timeout_preserves_destination_without_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "asset.zip"
            target.write_bytes(b"previous asset\r\n")
            for timeout in (True, False, None, 0, -1, 10**1000, float("inf"), float("nan")):
                with (
                    self.subTest(timeout=timeout),
                    patch("urllib.request.build_opener", side_effect=AssertionError("invalid timeout reached transport")),
                    self.assertRaisesRegex(ValueError, "timeout must"),
                ):
                    download_asset("https://example.invalid/asset.zip", target, expected_sha256="a" * 64, timeout=timeout)  # ty: ignore[invalid-argument-type] -- malformed public boundary input
                self.assertEqual(target.read_bytes(), b"previous asset\r\n")
                self.assertEqual(list(target.parent.iterdir()), [target])

    def test_descriptive_comparisons_are_invariant_under_exact_unit_scaling(self) -> None:
        # Hand-derived times: 100 -> 80 is a 20% reduction and a 1.25 ratio;
        # reversing them is a 25% increase and a 0.8 ratio. Powers of two
        # preserve these inputs exactly, including subnormal timing values.
        for exponent in (-1070, -500, 0, 500, 1000):
            for baseline, current, ratio, reduction in ((100, 80, 1.25, 20.0), (80, 100, 0.8, -25.0), (100, 100, 1.0, 0.0)):
                with self.subTest(exponent=exponent, baseline=baseline, current=current):
                    samples = [Sample((("case", Estimate(math.ldexp(point, exponent))),)) for point in (baseline, current)]
                    comparison = compare_samples(*samples)
                    (row,) = comparison.comparisons
                    self.assertEqual((row.speedup, row.percent_reduction), (ratio, reduction))
                    self.assertEqual(parse_comparison(serialize_comparison(comparison)), comparison)

    def test_comparison_and_retained_rendering(self) -> None:
        # Reduced Criterion median shapes used by MCMC and la-stack:
        # complete intervals, optional standard error, plus incomplete coverage.
        baseline = parse_estimate(b'{"median":{"point_estimate":100,"confidence_interval":{"lower_bound":90,"upper_bound":110,"confidence_level":0.95}}}')
        current = parse_estimate(b'{"median":{"point_estimate":80,"standard_error":2}}')
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

    def test_directory_links_cannot_enter_source_and_criterion_inventories(self) -> None:
        # Model junction detection on every host, then exercise its native link
        # kind. Windows directory junctions need no symlink privilege.
        for native in (False, True):
            with self.subTest(native=native), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary).resolve()
                root, external = parent / "project", parent / "external"
                root.mkdir()
                external.mkdir()
                criterion = root / "criterion"
                criterion.mkdir()
                linked = criterion / "linked"
                if native:
                    target = external
                    if os.name == "nt":
                        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(linked), str(target)], check=True, capture_output=True, timeout=30)
                        self.assertTrue(linked.is_junction())
                        self.assertFalse(linked.is_symlink())
                    else:
                        linked.symlink_to(target, target_is_directory=True)
                        self.assertTrue(linked.is_symlink())
                else:
                    target = linked
                    target.mkdir()
                    self.assertFalse(linked.is_symlink())
                source = target / "source.rs"
                source.write_bytes(b"source\r\n")
                estimates = target / "benchmark/new/estimates.json"
                estimates.parent.mkdir(parents=True)
                estimates.write_bytes(b'{"median":{"point_estimate":42}}')
                (root / "source.rs").write_bytes(b"source\r\n")
                config = MeasurementConfig(
                    (sys.executable, "-c", "from pathlib import Path; Path('launched').write_bytes(b'launched')"),
                    ("source.rs",),
                    ("source.rs",),
                    criterion_dir="criterion/linked/measurement",
                )
                original_junction = Path.is_junction
                with (
                    patch.object(Path, "is_junction", lambda path: (not native and path == linked) or original_junction(path)),
                    patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
                    patch("research_repo_tools.criterion.read_estimate", side_effect=AssertionError("linked estimates must not be read")),
                ):
                    for name, inspect in (
                        ("fingerprint", lambda: fingerprint_files(root, (Path("criterion/linked/source.rs"),))),
                        ("Criterion", lambda: collect_sample(criterion, "new")),
                        ("Criterion root", lambda: collect_sample(linked, "new")),
                        ("publication", lambda: plan_outputs(root, {"report.md": b"report"}, inputs={"criterion/linked/source.rs": b"source\r\n"})),
                        ("measurement", lambda: measure_checkout(root, config, "v1.0.0", mode="tag")),
                    ):
                        with self.subTest(api=name), self.assertRaisesRegex(ValueError, "symlink|junction"):
                            inspect()
                self.assertEqual(source.read_bytes(), b"source\r\n")
                self.assertFalse((root / "report.md").exists())
                self.assertFalse((root / "launched").exists())

    def test_zip_rejects_original_names_under_windows_normalization(self) -> None:
        # Model only zipfile's Windows separator conversion, without changing
        # the host filesystem or claiming a native Windows run.
        normalize = getattr(zipfile, "_sanitize_filename")

        def windows_name(name: str) -> str:
            return cast(str, normalize(name.replace("\\", "/")))

        with tempfile.TemporaryDirectory() as temporary, patch.object(zipfile, "_sanitize_filename", windows_name):
            root = Path(temporary)
            asset = root / "asset.zip"
            zip_asset(asset, [("valid/first", b"valid"), ("folder\\escape", b"unsafe")])
            with zipfile.ZipFile(asset) as archive:
                member = archive.infolist()[1]
                self.assertEqual(member.orig_filename, "folder\\escape")
                self.assertEqual(member.filename, "folder/escape")
            with self.assertRaisesRegex(ValueError, "path"):
                extract_archive(asset, root / "output")
            self.assertEqual(list(root.iterdir()), [asset])

    def test_tar_rejects_surrogateescaped_member_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "asset.tar"
            names = ["valid/first", "unsafe-\udcff/file"]
            # USTAR can retain non-UTF-8 name bytes. Verify the reader returns
            # the intended surrogate rather than letting fixture encoding hide it.
            with tarfile.open(asset, "w", format=tarfile.USTAR_FORMAT, encoding="utf-8", errors="surrogateescape") as archive:
                for name in names:
                    member = tarfile.TarInfo(name)
                    member.size = 1
                    archive.addfile(member, io.BytesIO(b"x"))
            with tarfile.open(asset, "r", encoding="utf-8") as archive:
                self.assertEqual(archive.getnames(), names)
            with self.assertRaisesRegex(ValueError, "unsafe.*path"):
                extract_archive(asset, root / "output")
            self.assertEqual(list(root.iterdir()), [asset])

    def test_compare_outputs_cannot_change_either_input_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, point in (("baseline", 100), ("currént", 80)):
                estimates = root / label / "bench/new/estimates.json"
                estimates.parent.mkdir(parents=True)
                estimates.write_bytes(deterministic_json({"median": {"point_estimate": point}}))
            before = {path: path.read_bytes() if path.is_file() else None for path in root.rglob("*")}
            command = [sys.executable, "-I", "-m", "research_repo_tools", "--root", str(root), "performance", "compare", "baseline", "currént"]
            for output in (
                "baseline",
                "currént",
                "baseline/bench/new/estimates.json",
                "currént/bench/new/estimates.json",
                "BASELINE/new/report.json",
                "curre\u0301nt/new/report.json",
                "baseline/../currént/new/report.json",
            ):
                with self.subTest(output=output):
                    result = subprocess.run([*command, "--output", output], capture_output=True, check=False, timeout=30)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(b"outside both Criterion input roots", result.stderr)
                    self.assertEqual({path: path.read_bytes() if path.is_file() else None for path in root.rglob("*")}, before)
            result = subprocess.run([*command, "--output", "baseline-report/comparison.json"], capture_output=True, check=False, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(parse_comparison((root / "baseline-report/comparison.json").read_bytes()).comparisons[0].speedup, 1.25)
            result = subprocess.run(command, capture_output=True, check=False, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(parse_comparison(result.stdout).comparisons[0].speedup, 1.25)

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
