# Paper and native dependency APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Paper date, PDF and native discovery API](#paper-date-pdf-and-native-discovery-api)

## Paper date, PDF and native discovery API

`papers check`, `normalize` and `source-date` accept literal paths or a named
`--paper` from `papers.documents`. Configuration fields are `tex`, `pdf`,
`identity`, `min-pages`, `require-text`, `forbid-text` and optional `reference`.
CLI options replace configured policy. `normalize --output` is explicit consumer
refresh intent; configuration alone does not refresh retained PDFs. See
[usage](BUILDING_PAPERS.md#reproducible-papers-and-native-dependency-discovery).

Public imports from `research_repo_tools.paper_dates`:

- `PaperDate.from_raw(raw: str) -> PaperDate` validates canonical English
  `Month day, year` text. The immutable record has `raw`, UTC-midnight `instant`
  and integer `source_date_epoch`; direct construction enforces those invariants.
  Epoch arithmetic also supports dates before 1970 on every host.
- `parse_source_date(source: str)` and `read_source_date(path: Path)` require
  exactly one uncommented explicit TeX date. Percent comments honor escaped
  backslash parity. This restricted reader does not expand TeX macros;
  malformed/multiple/dynamic dates fail.

Public imports from `research_repo_tools.paper_pdf` require the `papers` extra
only when PDF operations execute:

- `PdfPolicy(min_pages=1, required_text=(), forbidden_text=())` requires a positive
  integer and tuples of nonempty strings. Text is literal and case-sensitive.
- `inspect_pdf(path: Path) -> PdfInspection` reads per-page text, finite positive
  media/crop boxes, rotation and positive user units. `.pages` contains immutable
  `PdfPage` records; `.page_count` and `.text` expose count and joined text.
- `compare_structure(generated, reference) -> tuple[str, ...]` returns exact
  per-page differences, ignoring metadata and native serialization bytes.
- `check_pdf(path, *, policy=PdfPolicy(), reference=None) -> PdfInspection`
  applies text/page thresholds and optional rebuilt-versus-retained equivalence.
- `normalize_pdf_bytes(payload: bytes, *, date: PaperDate, identity: str) -> bytes`
  validates same-width Tectonic metadata edits. A stable nonempty identity is
  consumer-declared; path spellings do not enter UUID/trailer derivation.
  Six supported uncompressed XMP fields and one 16-byte trailer-ID pair are
  required. Existing Info dates must match the declared UTC instant, including
  compressed objects; build with `SOURCE_DATE_EPOCH`. Other profiles fail closed.
- `normalize_pdf(path, *, tex: Path, identity: str, output=None,
  policy=PdfPolicy(), reference=None) -> PdfInspection` reads all inputs, parses
  the date, validates normalization/structure/policy, then publishes through
  `files.replace_many`. Default output is in-place; separate outputs preserve
  source bytes. Existing outputs use snapshot guards after staging; new outputs
  require caller serialization. Exclude concurrent writers in either case.

Malformed/encrypted PDFs, unsupported metadata, invalid policy and missing extras
raise `ValueError`; file/publication failures may raise `OSError`. Incomplete
rollback follows the shared `RecoveryError` exception-group contract. Validation
failures do not replace inputs. These checks do not certify visual or scientific
equivalence; fonts/rendering may differ across native builders.

`research_repo_tools.tectonic.discover_environment(*, environment=None,
system=None, pkg_config="pkg-config", prefixes=()) -> dict[str, str]` returns
validated assignments without mutating the supplied mapping or `os.environ`.
Native host is the default; explicit `Linux`, `Darwin`, `Windows` values support
focused models. Unix probes use 30-second timeouts, retain search/sysroot values
and discover existing prefix metadata; macOS also reads Homebrew/SDK locations.
Windows checks an explicitly provisioned triplet directory and retains optional
`VCPKGRS_DYNAMIC`. This is discovery, not an ABI/compiler check or installer.
Unknown platforms, missing/failed probes and unsafe values raise.

`tectonic discover` defaults to JSON and supports POSIX `--format shell`;
`tectonic export` uses `ci.export_environment` with explicit `--file` or
`GITHUB_ENV`. No imports perform discovery or require optional extras.
