# Performance data and evidence APIs

The supported names below are available starting with the release containing
them, after `0.1.2`. Private names and the CLI implementation module are not public
APIs. The [README](../README.md#performance-evidence) contains consumer commands
and examples. All paths are `Path` objects; relative paths use the calling process's
directory in Python and the selected consumer root in the CLI.

## Criterion timings

Import from `research_repo_tools.criterion`:

| Name | Contract |
| --- | --- |
| `COMPARISON_SCHEMA` | `research-repo-tools/criterion-comparison/v1` |
| `Comparison(benchmark, baseline, current)` | Validated paired estimates with `speedup` and `percent_reduction` properties |
| `ComparisonSet(baseline, current)` | Immutable samples with derived `comparisons`, `missing_baseline`, and `missing_current` tuples |
| `Estimate(point, lower=None, upper=None, confidence_level=None)` | Finite positive timing values; intervals require both ordered bounds; optional confidence lies strictly between zero and one |
| `Sample(estimates, statistic="median", unit="ns")` | Immutable sorted `(benchmark, Estimate)` pairs; duplicate or invalid names fail |
| `Statistic`, `Unit` | Literal aliases: `mean`/`median` and `ns`/`us`/`ms`/`s` |
| `collect_sample(criterion_dir, sample, *, statistic="median", unit="ns")` | Read relative benchmark paths whose immediate parent directory is the selected sample |
| `compare_samples(baseline, current)` | Pair common benchmarks and retain both incomplete inventories |
| `parse_comparison(payload)` | Parse and validate shared comparison JSON bytes |
| `parse_estimate(payload, *, statistic="median")` | Read a selected Criterion statistic from JSON bytes |
| `read_estimate(path, *, statistic="median")` | Parse one Criterion file with path-qualified errors |
| `render_comparison(comparison)` | Deterministic Markdown derived entirely from retained data |
| `serialize_comparison(comparison)` | Deterministic JSON bytes containing full samples and explicit statistic/unit |
| `serialize_comparison_csv(comparison)` | Full inventories, coverage, unit/statistic, points and optional marginal bounds/confidence as UTF-8/LF CSV |
| `serialize_sample(sample)`, `parse_sample(payload)` | Validated retained sample bytes using `SAMPLE_SCHEMA`, `research-repo-tools/criterion-sample/v1` |

Criterion's default wall-time measurement records nanoseconds, but custom
measurements choose their own numeric scale. Units cannot be inferred from
`estimates.json`; the caller must supply the harness's timing unit. Non-timing
measurements require a consumer adapter and are not accepted as durations.
See [Criterion's measurement contract](https://bheisler.github.io/criterion.rs/book/user_guide/custom_measurements.html).
Comparisons require identical units and statistics; no implicit conversion or
pooling occurs. The point ratio is baseline/current. Time reduction is
`100 * (baseline - current) / baseline`, positive for less current time.
Derived values that overflow or underflow out of the positive finite ratio range
fail before returning a comparison.

Intervals may be absent. A bootstrap interval need not contain the point estimate.
No ratio interval, significance test, workload eligibility, or scientific
acceptance is inferred. Consumers own those policies and their report prose.
An existing empty sample is valid in Python; a missing root or unreadable tree
fails. The CLI additionally rejects comparisons without a common benchmark.
Sample names are single directory components. Benchmark IDs use relative POSIX
paths; symlinked entries are rejected. Names must be nonempty with no surrounding
whitespace or ASCII control characters. The collector includes every matching
benchmark; consumers select eligible cases before comparison when necessary.

Criterion parser reads permit extra Criterion fields but reject duplicate JSON
keys, malformed UTF-8, and non-finite JSON numbers. Shared comparison v1 requires
exact document and row field sets, rejects duplicate rows and unsupported schema
versions, and preserves confidence levels. It stores measurements, not redundant
derived coverage or ratios. Reading accepts arbitrary row order; writing sorts.

## Evidence and provenance

Import from `research_repo_tools.evidence`:

| Name | Contract |
| --- | --- |
| `Compatibility` | Requested `fields`, `differences`, and a `compatible` property |
| `Difference` | A requested `field` with `baseline` and `current` values; `None` means unknown |
| `Evidence(payload, payload_schema, sources)` | Immutable bytes, schema ID, and named provenance pairs |
| `Provenance(revision, source_sha256=None, harness_sha256=None, context=())` | Full lowercase Git commit ID, optional source/harness SHA-256 digests, sorted unique string context pairs |
| `compare_provenance(baseline, current, *, fields)` | Compare a nonempty explicit field selection and report missing or unequal values |
| `deterministic_json(value)` | UTF-8, sorted keys, two-space indentation, LF and final newline; finite JSON only |
| `fingerprint_files(root, paths)` | Exact-byte fingerprint of a nonempty explicit relative regular-file inventory, including filenames |
| `load_evidence(payload_path, manifest_path)` | Load exact bytes and verify envelope/hash |
| `parse_evidence(payload, manifest)` | Validate envelope structure and the original payload's SHA-256 |
| `publish_evidence(evidence, payload_path, manifest_path, *, reports=None, immutable=())` | Transactionally publish verified evidence and pre-rendered reports |
| `serialize_evidence(evidence)` | Return `(payload_bytes, manifest_bytes)`, preserving both loaded inputs exactly |
| `sha256(payload)` | Hash bytes without transformations |
| `verify_sha256(payload, expected)` | Raise on malformed digest or an exact-byte mismatch |

The envelope schema is `research-repo-tools/evidence/v1`. Its exact root fields
are `schema`, `payload_schema`, `payload_sha256`, and `sources`. Each named source
contains `revision`, `source_sha256`, `harness_sha256`, and `context`.
Unknown/missing fields and duplicate keys fail. At least one named source is
required. Digests are lowercase 64-digit hexadecimal values. A revision is a
full 40- or 64-digit lowercase Git commit ID. Payloads remain opaque: consumers
must call the schema's parser before using measurements. A hash establishes
integrity relative to the supplied sidecar, not authenticity or provenance truth.

Compatibility fields are `revision`, `source_sha256`, `harness_sha256`, and
`context.KEY`. Unknown values differ even when both sides lack the value. Callers
select required machine/tool/harness context for compatibility and compare recorded
versus freshly captured source identity to detect stale evidence. Different
revisions alone do not make benchmark comparisons ineligible. The API never
silently overrides an incompatibility or invents missing historical metadata.

`fingerprint_files` uses SHA-256 seeded with `research-repo-tools/files/v1` plus
a NUL byte, then sorted UTF-8 relative POSIX names and file bytes, each framed
with its eight-byte big-endian length. It rejects traversal, duplicate paths,
and symlinks, and preserves LF/CRLF/binary distinctions. It excludes permissions,
Git attributes, and files not in the supplied inventory. It is not a Git tree
hash. Consumers own inventory discovery, additions/deletions, executable-mode
evidence where relevant, and exclusion of concurrent changes. Capture source and
harness separately, including applied uncommitted source and any substituted
harness. Use the [byte process API](api.md#python-process-api) for Git filters
and binary patches. Existing digest schemes remain distinct and unchanged.

New envelopes use `deterministic_json`; it is a package convention, not RFC 8785.
Loaded payload and sidecar bytes survive promotion unchanged, including whitespace,
newlines, and key order. Verification always precedes decoding or reserialization.
Both arguments to `parse_evidence` must be immutable `bytes`; mutable buffers raise
`TypeError` before any manifest is retained.
Use distinct new filenames when migrating to a different payload schema.

Publication validates the envelope and every target before replacement. The payload,
sidecar, and all report paths must be distinct and non-overlapping, including aliases.
Case and Unicode normalization collisions are rejected on every platform, including
for missing files or parents. Rendering uses the same rules to protect retained inputs.
Every immutable path must be a publication target and may be absent or contain
identical bytes. This allows repeat promotion but refuses conflicting archive
content before any writes. Consumers pre-validate payload semantics, compatibility,
and all report/index bytes, then publish the whole group in one call.
The operation shares [file-publication rollback](api.md#python-file-publication-api),
including `RecoveryError` backups when restoration fails. It requires exclusive
writer control and is not crash-atomic across files. No Git state is changed.

## Published archives

For marked document sections, selected tables, and generated figures, use the
[publication API](publication-api.md). It adds reference and tagged-blob checks,
preview/check modes, and byte-preserving document replacement on top of these
retained-evidence and file-transaction primitives.

Import from `research_repo_tools.archives`:

| Name | Contract |
| --- | --- |
| `ArchiveLimits(archive_bytes=268435456, members=100000, content_bytes=1073741824)` | Positive compressed-input, member-count, and expanded-file-byte limits |
| `download_asset(url, destination, *, expected_sha256, limits=ArchiveLimits(), timeout=60)` | Bounded HTTPS retrieval; verify the required digest before replacing a file |
| `extract_archive(archive_path, destination, *, limits=ArchiveLimits(), expected_sha256=None)` | Preflight and privately stage tar/ZIP contents, then publish into an absent directory |

Retrieval permits HTTPS redirects only, rejects embedded credentials, and uses a
positive finite per-socket timeout rather than a total transfer deadline. It
reads at most the archive byte limit plus one before rejecting oversize input.
For `download_asset`, callers supply the URL and an independent trusted digest.
The [workflow APIs](workflow-api.md#release-assets) additionally provide release
discovery and authenticated GitHub asset retrieval, including historical assets
without an independent digest. Downloaded bytes use the common verifier and extractor.

Extraction rejects absolute/traversing paths, links, special files, sparse tar
members, encrypted ZIP files, duplicate members, file/directory overlaps, and
names that collide or are unsafe on supported platforms. This includes Windows
drive/UNC/alternate-stream paths, reserved device names, case aliases, and Unicode
normalization aliases. Common leading `./` components and root directory entries
are accepted. Only ordinary files/directories are written; no archive ownership,
permissions, or timestamps are restored. The parent directory must exist and the
destination must be absent. Failures leave the destination absent and attempt to
remove staging files; cleanup failures log the retained staging path without
masking the original error. Callers must exclude concurrent destination/parent writers.

Extraction uses a private sibling directory rather than merging into an existing
tree. Python's extraction filters alone do not address every resource or overwrite
risk; see the [standard library guidance](https://docs.python.org/3/library/tarfile.html#extraction-filters).
Limits bound input bytes, declared expanded file bytes, and accepted members;
they do not provide CPU deadlines or an operating-system memory sandbox.

XZ and Zstandard decoder failures after a valid first header raise `ValueError`.
Compression support follows the available standard-library
decoders; Python builds without a decoder still support unrelated operations.

Invalid data normally raises `ValueError`; wrong Python argument types may raise
`TypeError`. Filesystem/transport errors propagate as `OSError`, and publication
may raise an exception group with recovery information. CLI errors use the
package's usual stderr/exit-status contract. No heavy optional dependencies,
or network operations on import are introduced. Explicit configured measurement,
worktree and release-asset operations are documented separately in the
[workflow API](workflow-api.md).
