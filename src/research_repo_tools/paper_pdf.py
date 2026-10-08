"""Optional PDF inspection and validated deterministic Tectonic metadata."""

import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from research_repo_tools.files import replace_many
from research_repo_tools.paper_dates import PaperDate, parse_source_date

__all__ = ["PdfInspection", "PdfPage", "PdfPolicy", "check_pdf", "compare_structure", "inspect_pdf", "normalize_pdf", "normalize_pdf_bytes"]


@dataclass(frozen=True, slots=True)
class PdfPolicy:
    min_pages: int = 1
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.min_pages) is not int or self.min_pages < 1:
            raise ValueError("PDF minimum page count must be a positive integer")
        for name in ("required_text", "forbidden_text"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"PDF {name} must be a tuple of nonempty strings")


@dataclass(frozen=True, slots=True)
class PdfPage:
    text: str
    media_box: tuple[float, ...]
    crop_box: tuple[float, ...]
    rotation: int
    user_unit: float


@dataclass(frozen=True, slots=True)
class PdfInspection:
    pages: tuple[PdfPage, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)


def _reader(payload: bytes):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError as error:
        raise ValueError("PDF operations require research-repo-tools[papers]; install the optional extra in the consumer's locked group") from error
    try:
        reader = PdfReader(BytesIO(payload), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted PDFs are not supported")
        return reader
    except (PyPdfError, TypeError, ValueError, KeyError, IndexError) as error:
        raise ValueError(f"failed to read PDF: {error}") from error


def _inspect(payload: bytes) -> PdfInspection:
    reader = _reader(payload)
    from pypdf.errors import PyPdfError

    pages = []
    try:
        for number, page in enumerate(reader.pages, 1):
            boxes = []
            for rectangle in (page.mediabox, page.cropbox):
                coordinates = tuple(float(item) for item in rectangle)
                if len(coordinates) != 4 or not all(math.isfinite(item) for item in coordinates):
                    raise ValueError(f"page {number} has non-finite or malformed geometry")
                if coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
                    raise ValueError(f"page {number} has nonpositive geometry")
                boxes.append(coordinates)
            unit = float(page.get("/UserUnit", 1))
            if not math.isfinite(unit) or unit <= 0:
                raise ValueError(f"page {number} has invalid UserUnit")
            pages.append(PdfPage(page.extract_text() or "", boxes[0], boxes[1], page.rotation, unit))
    except (PyPdfError, TypeError, ValueError, KeyError, IndexError, OverflowError) as error:
        raise ValueError(f"failed to inspect PDF page: {error}") from error
    return PdfInspection(tuple(pages))


def inspect_pdf(path: Path) -> PdfInspection:
    """Extract exact per-page text, boxes, rotation and units; ignore native bytes."""
    try:
        return _inspect(path.read_bytes())
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: {error}") from error


def compare_structure(generated: PdfInspection, reference: PdfInspection) -> tuple[str, ...]:
    """Return structural differences; metadata/font/serialization bytes may differ."""
    if generated.page_count != reference.page_count:
        return (f"reference has {reference.page_count} page(s), rebuilt PDF has {generated.page_count}",)
    failures = []
    for number, (candidate, retained) in enumerate(zip(generated.pages, reference.pages, strict=True), 1):
        for field in ("text", "media_box", "crop_box", "rotation", "user_unit"):
            if getattr(candidate, field) != getattr(retained, field):
                failures.append(f"reference page {number} {field.replace('_', ' ')} differs from rebuilt PDF")
    return tuple(failures)


def _check(inspection: PdfInspection, policy: PdfPolicy, reference: PdfInspection | None = None) -> None:
    failures = []
    if inspection.page_count < policy.min_pages:
        failures.append(f"expected at least {policy.min_pages} page(s), found {inspection.page_count}")
    failures.extend(f"missing required text: {value!r}" for value in policy.required_text if value not in inspection.text)
    failures.extend(f"found forbidden text: {value!r}" for value in policy.forbidden_text if value in inspection.text)
    if reference is not None:
        failures.extend(compare_structure(inspection, reference))
    if failures:
        raise ValueError("; ".join(failures))


def check_pdf(path: Path, *, policy: PdfPolicy = PdfPolicy(), reference: Path | None = None) -> PdfInspection:
    """Read-only sanity/equivalence checks with explicit consumer text policy."""
    inspection = inspect_pdf(path)
    try:
        _check(inspection, policy, inspect_pdf(reference) if reference is not None else None)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    return inspection


def normalize_pdf_bytes(payload: bytes, *, date: PaperDate, identity: str) -> bytes:
    """Normalize uncompressed Tectonic XMP and 16-byte trailer identities.

    Replace same-width fields inside the actual metadata stream, preserving
    xref offsets and all other bytes. Require the seven known fields, reject
    unsupported/compressed profiles, and require any Info dates to already
    match SOURCE_DATE_EPOCH. Inspect both complete PDFs before returning. The identity is a consumer declaration,
    independent of source/output path spelling and host platform.
    """
    if not isinstance(date, PaperDate) or not isinstance(identity, str) or not identity or any(char in identity for char in "\0\r\n"):
        raise ValueError("normalization requires a PaperDate and a nonempty stable identity without NUL/CR/LF")
    before = _inspect(payload)
    _check(before, PdfPolicy())
    reader = _reader(payload)
    from pypdf.errors import PyPdfError
    from pypdf.generic import DecodedStreamObject

    try:
        information = reader.metadata
        if information:
            for key, parsed in (("/CreationDate", information.creation_date), ("/ModDate", information.modification_date)):
                if key in information and parsed != date.instant:
                    raise ValueError(f"Info {key} must match the explicit UTC date; build with SOURCE_DATE_EPOCH={date.source_date_epoch}")
        metadata = reader.trailer["/Root"].get("/Metadata")
        if metadata is None:
            raise ValueError("expected Tectonic XMP metadata stream was not found")
        stream = metadata.get_object()
        if not isinstance(stream, DecodedStreamObject) or stream.get("/Filter"):
            raise ValueError("unsupported or compressed XMP metadata for same-width normalization")
        original = stream.get_data()
    except (PyPdfError, TypeError, ValueError, KeyError, IndexError, AttributeError) as error:
        raise ValueError(f"failed to read PDF normalization metadata: {error}") from error
    if not original or payload.count(original) != 1:
        raise ValueError("XMP metadata stream must occur exactly once in the PDF")
    try:
        tree = ElementTree.fromstring(original)
    except ElementTree.ParseError as error:
        raise ValueError(f"invalid XMP XML: {error}") from error
    rdf = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    fields = ["{http://purl.org/dc/elements/1.1/}date/" + rdf + "Seq/" + rdf + "li"]
    fields += ["{http://ns.adobe.com/xap/1.0/}" + name for name in ("CreateDate", "ModifyDate", "MetadataDate")]
    fields += ["{http://ns.adobe.com/xap/1.0/mm/}" + name for name in ("DocumentID", "InstanceID")]
    if any(len(tree.findall(".//" + field)) != 1 for field in fields):
        raise ValueError("expected exactly one of each supported XMP metadata field in its namespace")
    day = f"{date.instant.year:04d}-{date.instant.month:02d}-{date.instant.day:02d}"
    timestamp = day + "T00:00:00"
    normalized = original
    for tag, pattern, replacement in (
        ("rdf:li", r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", timestamp + "Z"),
        ("xmp:CreateDate", r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", timestamp),
        ("xmp:ModifyDate", r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", timestamp + "Z"),
        ("xmp:MetadataDate", r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", timestamp + "Z"),
        ("xmpMM:DocumentID", r"uuid:[0-9A-Fa-f-]{36}", "uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:{date.raw}:document"))),
        ("xmpMM:InstanceID", r"uuid:[0-9A-Fa-f-]{36}", "uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:{date.raw}:instance"))),
    ):
        regex = re.compile(f"<{tag}>({pattern})</{tag}>".encode())
        matches = list(regex.finditer(normalized))
        if len(matches) != 1:
            raise ValueError(f"{tag}: expected exactly one supported metadata field")
        match = matches[0]
        value = replacement.encode("ascii")
        if len(value) != len(match[1]):
            raise ValueError(f"{tag}: metadata replacement length changed")
        normalized = normalized[: match.start(1)] + value + normalized[match.end(1) :]
    candidate = payload.replace(original, normalized, 1)
    trailer = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f]{32})>\s*<([0-9A-Fa-f]{32})>\s*\]")
    matches = list(trailer.finditer(candidate))
    if len(matches) != 1:
        raise ValueError("expected exactly one 16-byte PDF trailer ID pair")
    match = matches[0]
    stable_id = hashlib.sha256(f"{identity}:{date.raw}:trailer-id".encode("utf-8")).hexdigest()[:32].encode("ascii")
    for group in (2, 1):
        candidate = candidate[: match.start(group)] + stable_id + candidate[match.end(group) :]
    ids = _reader(candidate).trailer.get("/ID", ())
    if len(ids) != 2 or any(value.original_bytes.hex().encode("ascii") != stable_id for value in ids):
        raise ValueError("normalized trailer identities were not found in the parsed PDF trailer")
    differences = compare_structure(_inspect(candidate), before)
    if differences:
        raise ValueError("normalization changed PDF structure: " + "; ".join(differences))
    return candidate


def normalize_pdf(
    path: Path,
    *,
    tex: Path,
    identity: str,
    output: Path | None = None,
    policy: PdfPolicy = PdfPolicy(),
    reference: Path | None = None,
) -> PdfInspection:
    """Validate every input and candidate before recoverable file replacement.

    In-place by default; an explicit output can refresh a retained consumer
    artifact. TeX, input PDF and reference stay unchanged when output differs.
    Optimistic guards reject edits detected before publication. Concurrent
    writers must still be excluded by the consumer.
    """
    destination = path if output is None else output
    if destination.resolve() == tex.resolve():
        raise ValueError("PDF output must not replace its TeX source")
    originals = {path: path.read_bytes(), tex: tex.read_bytes()}
    try:
        date = parse_source_date(originals[tex].decode("utf-8"))
    except ValueError as error:
        raise ValueError(f"{tex}: failed to read paper source date: {error}") from error
    retained = None
    if reference is not None:
        originals[reference] = reference.read_bytes()
        retained = _inspect(originals[reference])
    if destination.exists() and destination not in originals:
        originals[destination] = destination.read_bytes()
    candidate = normalize_pdf_bytes(originals[path], date=date, identity=identity)
    inspection = _inspect(candidate)
    _check(inspection, policy, retained)
    for source, original in originals.items():
        if source.read_bytes() != original:
            raise ValueError(f"file changed before publication: {source}")
    replace_many({destination: candidate}, expected=originals if destination in originals else None)
    return inspection
