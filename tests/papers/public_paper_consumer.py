"""Synthetic date and real PDF contracts for source, wheel and sdist."""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from research_repo_tools import config, files
from research_repo_tools.cli import main
from research_repo_tools.paper_dates import PaperDate, parse_source_date, read_source_date
from research_repo_tools.paper_pdf import PdfPolicy, check_pdf, compare_structure, inspect_pdf, normalize_pdf, normalize_pdf_bytes


def pdf_bytes(
    *,
    text: str = "Example title REFERENCES",
    width: int = 612,
    crop: int = 612,
    rotation: int = 0,
    volatile: str = "2026-01-02T03:04:05",
    info_date: str | None = None,
    compressed: bool = False,
) -> bytes:
    """Build a complete synthetic PDF; no scientific consumer data is copied."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, ByteStringObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, RectangleObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=792)
    page.cropbox = RectangleObject((0, 0, crop, 792))
    page[NameObject("/Rotate")] = NumberObject(rotation)
    font = DictionaryObject(
        {NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 10 700 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    xmp = DecodedStreamObject()
    xmp[NameObject("/Type")] = NameObject("/Metadata")
    xmp[NameObject("/Subtype")] = NameObject("/XML")
    xmp.set_data(
        (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/">'
            f"<dc:date><rdf:Seq><rdf:li>{volatile}Z</rdf:li></rdf:Seq></dc:date>"
            f"<xmp:CreateDate>{volatile}</xmp:CreateDate><xmp:ModifyDate>{volatile}Z</xmp:ModifyDate><xmp:MetadataDate>{volatile}Z</xmp:MetadataDate>"
            "<xmpMM:DocumentID>uuid:20a82bb6-7f7f-4200-a390-ae4182b6cef7</xmpMM:DocumentID>"
            "<xmpMM:InstanceID>uuid:ef7a3ef2-67f7-4a39-85f7-5954c90aef20</xmpMM:InstanceID>"
            "</rdf:Description></rdf:RDF></x:xmpmeta>"
        ).encode("ascii")
    )
    writer.root_object[NameObject("/Metadata")] = writer._add_object(xmp.flate_encode() if compressed else xmp)
    if info_date is not None:
        writer.add_metadata({"/CreationDate": info_date})
    writer._ID = ArrayObject([ByteStringObject(b"a" * 16), ByteStringObject(b"b" * 16)])
    output = io.BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


class TestDates(unittest.TestCase):
    def test_explicit_locale_independent_date_comments_and_epoch(self) -> None:
        date = parse_source_date("% \\date{January 1, 1999}\n" + r"\date{July 6, 2026} % comment")
        self.assertEqual(date.source_date_epoch, 1783296000)
        self.assertEqual(date.instant, datetime(2026, 7, 6, tzinfo=UTC))
        self.assertEqual(PaperDate.from_raw("February 29, 2024").instant.day, 29)
        self.assertEqual(PaperDate.from_raw("January 1, 1900").source_date_epoch, -2208988800)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source café.tex"
            source.write_text(r"\date{July 6, 2026}", encoding="utf-8", newline="\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["papers", "source-date", str(source)]), 0)
            self.assertEqual(out.getvalue(), "1783296000\n")
            self.assertEqual(read_source_date(source), date)

    def test_invalid_dates_duplicate_commands_and_direct_construction(self) -> None:
        for raw in ("July 06, 2026", " July 6, 2026", "Juillet 6, 2026", "April 31, 2026", "February 29, 2025", "2026-07-06", "July 6, 0000"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                PaperDate.from_raw(raw)
        for source in ("", r"\date{\today}", r"\date{July 6, 2026}\date{}", r"\date{July 6, 2026}\date{nested{date}}", r"\\date{July 6, 2026}"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_source_date(source)
        for instant in (
            datetime(2026, 7, 6),
            datetime(2026, 7, 6, tzinfo=timezone(timedelta(hours=1))),
            datetime(2026, 7, 6, 1, tzinfo=UTC),
            datetime(2026, 7, 7, tzinfo=UTC),
        ):
            with self.subTest(instant=instant), self.assertRaises(ValueError):
                PaperDate("July 6, 2026", instant)

    def test_tex_escape_parity_and_read_failure(self) -> None:
        self.assertEqual(parse_source_date(r"\% not a comment \date{July 6, 2026}").source_date_epoch, 1783296000)
        with self.assertRaises(ValueError):
            parse_source_date(r"\\% comment \date{July 6, 2026}")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.tex"
            with self.assertRaisesRegex(ValueError, "source.tex"):
                read_source_date(source)
            source.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "source.tex"):
                read_source_date(source)


@unittest.skipIf(importlib.util.find_spec("pypdf") is not None, "only checks maintenance-only installation")
class TestMissingExtra(unittest.TestCase):
    def test_clear_optional_dependency_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(main(["papers", "check", str(pdf)]), 1)
            self.assertIn("research-repo-tools[papers]", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())


class TestPdf(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="paper café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tex = self.root / "source.tex"
        self.tex.write_text(r"\date{July 6, 2026}", encoding="utf-8", newline="\n")
        self.pdf = self.root / "rebuilt.pdf"
        self.original = pdf_bytes()
        self.pdf.write_bytes(self.original)

    def test_real_pdf_text_structure_and_native_byte_equivalence(self) -> None:
        reference = self.root / "retained.pdf"
        reference.write_bytes(pdf_bytes(volatile="2025-03-04T05:06:07"))
        self.assertNotEqual(reference.read_bytes(), self.original)
        inspection = check_pdf(self.pdf, policy=PdfPolicy(1, ("Example title", "REFERENCES"), (r"\today",)), reference=reference)
        self.assertEqual(inspection.page_count, 1)
        self.assertEqual(compare_structure(inspection, inspect_pdf(reference)), ())
        for payload, label in (
            (pdf_bytes(text="Other"), "text"),
            (pdf_bytes(width=600), "media box"),
            (pdf_bytes(crop=600), "crop box"),
            (pdf_bytes(rotation=90), "rotation"),
        ):
            reference.write_bytes(payload)
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, label):
                check_pdf(self.pdf, reference=reference)
        for policy in (PdfPolicy(2), PdfPolicy(required_text=("Missing",)), PdfPolicy(forbidden_text=("REFERENCES",))):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                check_pdf(self.pdf, policy=policy)

    def test_normalization_is_deterministic_idempotent_and_preserves_offsets(self) -> None:
        date = read_source_date(self.tex)
        normalized = normalize_pdf_bytes(self.original, date=date, identity="example/reviewer")
        self.assertEqual(len(normalized), len(self.original))
        self.assertIn(b"2026-07-06T00:00:00", normalized)
        self.assertNotIn(b"2026-01-02T03:04:05", normalized)
        self.assertEqual(normalize_pdf_bytes(normalized, date=date, identity="example/reviewer"), normalized)
        self.assertNotEqual(normalize_pdf_bytes(self.original, date=date, identity="another/reviewer"), normalized)
        self.assertEqual(normalize_pdf(self.pdf, tex=self.tex, identity="example/reviewer").page_count, 1)
        self.assertEqual(self.pdf.read_bytes(), normalized)

    def test_output_refresh_and_policy_failures_preserve_all_artifacts(self) -> None:
        destination = self.root / "reviewer.pdf"
        destination.write_bytes(b"prior artifact")
        snapshot = self.tex.read_bytes(), self.pdf.read_bytes(), destination.read_bytes()
        for kwargs in ({"policy": PdfPolicy(required_text=("Missing",))}, {"reference": self.root / "missing.pdf"}, {"identity": ""}):
            options: dict[str, Any] = {"identity": "stable", **kwargs}
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError if "reference" not in kwargs else OSError):
                normalize_pdf(self.pdf, tex=self.tex, output=destination, **options)
            self.assertEqual((self.tex.read_bytes(), self.pdf.read_bytes(), destination.read_bytes()), snapshot)
        normalize_pdf(self.pdf, tex=self.tex, identity="stable", output=destination)
        self.assertEqual(self.pdf.read_bytes(), self.original)
        self.assertEqual(check_pdf(destination).page_count, 1)
        normalize_pdf(self.pdf, tex=self.tex, identity="stable", output=self.root / "new.pdf")
        self.assertEqual(check_pdf(self.root / "new.pdf").page_count, 1)

    def test_atomic_failure_and_invalid_source_preserve_input(self) -> None:
        with patch.object(files, "_replace_path", side_effect=OSError("publication failed")):
            with self.assertRaisesRegex(OSError, "publication failed"):
                normalize_pdf(self.pdf, tex=self.tex, identity="stable")
        self.assertEqual(self.pdf.read_bytes(), self.original)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["rebuilt.pdf", "source.tex"])
        for payload in (b"\\date{\\today}", b"bad\xff"):
            self.tex.write_bytes(payload)
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "source.tex.*failed to read paper source date"):
                normalize_pdf(self.pdf, tex=self.tex, identity="stable")
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(main(["papers", "normalize", str(self.pdf), "--tex", str(self.tex), "--identity", "stable"]), 1)
            self.assertIn(str(self.tex), error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())
            self.assertEqual((self.tex.read_bytes(), self.pdf.read_bytes()), (payload, self.original))
        with self.assertRaisesRegex(ValueError, "TeX source"):
            normalize_pdf(self.pdf, tex=self.tex, identity="stable", output=self.tex)

    def test_invalid_metadata_and_encryption_never_publish(self) -> None:
        from pypdf import PdfWriter

        for payload in (b"broken PDF", self.original.replace(b"xmp:CreateDate", b"xmp:BrokenDate")):
            self.pdf.write_bytes(payload)
            with self.subTest(payload=payload[:12]), self.assertRaises(ValueError):
                normalize_pdf(self.pdf, tex=self.tex, identity="stable")
            self.assertEqual(self.pdf.read_bytes(), payload)
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("password")
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        with self.assertRaisesRegex(ValueError, "encrypted"):
            check_pdf(self.pdf)

    def test_info_epoch_compressed_xmp_and_missing_fields_fail_closed(self) -> None:
        date = read_source_date(self.tex)
        stable = pdf_bytes(info_date="D:20260706000000-00'00'")
        self.assertEqual(len(normalize_pdf_bytes(stable, date=date, identity="stable")), len(stable))
        for payload, error in (
            (pdf_bytes(info_date="D:20260102030405Z"), "SOURCE_DATE_EPOCH"),
            (pdf_bytes(compressed=True), "compressed XMP"),
            (self.original.replace(b"xmp:ModifyDate", b"xmp:BrokenDate"), "metadata field"),
            (self.original.replace(b"</rdf:RDF>", b"</rdf:RDG>"), "invalid XMP XML"),
            (self.original.replace(b"/ID [", b"/XX ["), "trailer ID"),
        ):
            self.pdf.write_bytes(payload)
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                normalize_pdf(self.pdf, tex=self.tex, identity="stable")
            self.assertEqual(self.pdf.read_bytes(), payload)

    def test_zero_page_and_nonpositive_geometry_rejected(self) -> None:
        from pypdf import PdfWriter

        writer = PdfWriter()
        output = io.BytesIO()
        writer.write(output)
        self.pdf.write_bytes(output.getvalue())
        with self.assertRaisesRegex(ValueError, "at least 1"):
            check_pdf(self.pdf)
        self.pdf.write_bytes(pdf_bytes(width=0))
        with self.assertRaisesRegex(ValueError, "geometry"):
            check_pdf(self.pdf)

    def test_named_configuration_and_cli_override(self) -> None:
        declaration = {"tex": "source.tex", "pdf": "rebuilt.pdf", "identity": "example", "require-text": ["Example title"]}
        settings = config.parse({"papers": {"documents": {"example": declaration}}}, root=self.root)
        self.assertEqual(settings.papers["example"].policy.required_text, ("Example title",))
        with self.assertRaises(TypeError):
            cast(Any, settings.papers)["other"] = settings.papers["example"]
        (self.root / "pyproject.toml").write_text(
            '[tool.research-repo-tools.papers.documents.example]\ntex="source.tex"\npdf="rebuilt.pdf"\nidentity="example"\nrequire-text=["Example title"]\n',
            encoding="utf-8",
            newline="\n",
        )
        output = io.StringIO()
        error = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            for action in ("check", "normalize", "source-date"):
                self.assertEqual(main(["--root", str(self.root), "papers", action, "--paper", "example"]), 0)
            self.assertEqual(main(["--root", str(self.root), "papers", "check", "--paper", "example", "--require-text", "Missing"]), 1)
        self.assertIn("missing required text", error.getvalue())
        for override in ({"min-pages": 0}, {"require-text": "text"}, {"unknown": True}, {"identity": ""}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                config.parse({"papers": {"documents": {"example": {**declaration, **override}}}}, root=self.root)


if __name__ == "__main__":
    unittest.main()
