# Evidence-backed document publication

Available in the release containing these APIs, after `0.1.2`. Consumer commands
and complete configuration examples live in the
[README](../README.md#document-publication). Historical-schema adoption is covered
in [publication migration](publication-migration.md).

## Python API

Import from `research_repo_tools.publication`:

| Name | Contract |
| --- | --- |
| `GitCheck(tag, paths=(), revision=None)` | Required local tag, ordered root-relative blob paths, optional full lowercase source commit ID; requires paths or a revision |
| `MarkerPair(begin, end)` | Nonempty single-line UTF-8 delimiters, distinct and not containing one another |
| `PublicationPlan` | Planner result with `root`, immutable `outputs` and `originals` path/byte tuples, `git_checks`, and absolute `changed_paths` |
| `TableLayout(rows, baseline_label, current_label, unit)` | Nonempty ordered `(benchmark, display_label)` pairs with unique benchmark IDs and an explicit recorded timing unit |
| `plan_publication(root, document, markers, content, *, inputs, figures=None, references=(), git_checks=())` | Validate exact retained inputs, document markers, all candidates, reference captures, and required Git identities without writing |
| `preview_publication(plan)` | Unified diffs for UTF-8 candidates; SHA-256 summaries for other bytes; does not publish or revalidate a saved plan |
| `publish_publication(plan)` | Recheck snapshots and Git identities, transactionally publish changed outputs, return their absolute paths |
| `render_svg(comparison, layout)` | Deterministic UTF-8 SVG bytes with XML-escaped labels, point-ratio bars and a reference at one; standard library only |
| `render_table(comparison, layout)` | Deterministic Markdown string with selected paired estimates, recorded confidence bounds, point ratios, reductions, and full coverage counts |
| `replace_section(document, markers, content)` | Require exactly one ordered pair and replace only its interior; document is bytes, content is text |
| `verify_tagged_files(root, tag, files, *, revision=None)` | Compare raw regular-file blobs at a required local tag with supplied bytes; optionally require its commit ID; return the resolved commit |

Import `load_publication(root, configuration)` from
`research_repo_tools.publication_config` to parse a publication TOML file,
validate shared comparison evidence, and return a `PublicationPlan`.
`root` is a `Path`; configuration, document, figure, input, link, and reference
names are normalized root-relative POSIX strings. Paths cannot traverse, alias,
overlap, contain symlinks, enter `.git`, or use Windows-reserved spellings.
Case and Unicode normalization aliases are rejected on every platform, even when
the target does not yet exist. The root itself is resolved once.

Use the planner to construct plans; the public result record is not an
independent evidence authenticator. Supply every validated input's original bytes
in `inputs`, including historical payloads, provenance, reports, and any source
files used by a custom adapter. At least one retained input is required. The
planner checks those bytes still match disk. Outputs cannot alias inputs.
Additional reference files are snapshotted automatically. Figure values must be
bytes and can come from consumer renderers. The built-in renderer creates SVG;
the generic planner also accepts other figure formats.

`references` uses the existing public
[`ReleaseRule`](api.md#python-release-api) selector type, with fixed `value`
assertions only. Each rule must find exactly its configured number of nonempty
named `value` captures and every capture must equal the expected value. Rules
selecting outputs inspect candidate bytes. This supports release/report identity
and active-link checks without rewriting historical references.

Marker replacement searches literal UTF-8 delimiter bytes and rejects missing,
duplicate, reversed, or overlapping markers. Content cannot introduce a delimiter.
Only content newlines are normalized to LF; two LF bytes separate each delimiter
from the body. Every byte outside the delimiters' interior survives unchanged,
including a BOM, mixed line endings, historical links, and non-UTF-8 bytes.
References require their selected files to decode as UTF-8.

Renderers require every selected benchmark in both retained samples and require
`layout.unit` to equal the samples' unit. They preserve explicit selection order,
permit repeated display labels without merging bars, and perform no conversion,
measurement, eligibility selection, significance testing, or scientific inference.
The table shows recorded intervals and their confidence level when available;
it never invents an interval for a ratio. SVG is deterministic across runs and
platforms because it serializes geometry directly, with no timestamps or font
outlining. Its visual text metrics depend on the viewer's sans-serif font.

Publication rechecks every input, reference, and output against its snapshot,
including expected absence, immediately before calling `replace_many` once.
It also repeats configured Git checks. Caught failures roll back earlier writes;
incomplete rollback exposes `RecoveryError` backups through the existing
[file-publication contract](api.md#python-file-publication-api).
No-op outputs are not rewritten. Concurrent writers must be excluded by the
caller: snapshot comparisons provide neither locking nor crash atomicity.

## Publication configuration v1

The TOML adapter is deliberately specific to
`research-repo-tools/criterion-comparison/v1` payloads and the shared evidence
sidecar. Both `baseline` and `current` sources must be present. It validates the
original payload hash before parsing measurements, then compares independently
configured provenance expectations. Unknown fields, absent required fields,
non-integer schema/count values, and empty required arrays fail before writes.

| Field | Meaning |
| --- | --- |
| `baseline-label`, `current-label` | Optional display labels; default to the expected release tags |
| `begin`, `end` | Required literal marker strings |
| `document` | Existing document to update |
| `links` | Optional nonempty array of `{label, path}` tables; paths must exist or be candidates |
| `manifest`, `payload` | Existing retained sidecar and comparison payload |
| `provenance.baseline`, `provenance.current` | Required independently maintained source expectations |
| `references` | Nonempty selector array covering `previous-tag`, `tag`, and `version` |
| `repository` | Optional GitHub `owner/repository`; selects verified links at the current release tag |
| `rows` | Nonempty array of `{benchmark, label}` tables in the desired publication order |
| `schema` | Integer `1` |
| `svg` | Optional generated SVG path; also inserts its image link |
| `unit` | Explicit evidence timing unit: `ns`, `us`, `ms`, or `s` |

Each provenance table requires `revision` (full Git commit ID) and `release`
(`v`-prefixed SemVer). The latter must match the retained source's `context.release`.
Optional `source-sha256`, `harness-sha256`, and `context` pins add required equality
checks. Missing retained values never establish equality. The `context` table
cannot repeat `release`. Pins compare retained evidence to expectations; they do
not recapture today's source tree or authenticate the person who supplied it.

Set `verify-tag = true` in a source's provenance table only when the evidence
promises measurement of that exact tagged commit. This resolves its local release
tag and requires the recorded commit ID. A measurement with applied working-tree
changes or a substituted harness needs the consumer's corresponding source/harness
validation; a matching commit alone does not prove those bytes. Use a consumer
adapter and `fingerprint_files` or the original digest scheme for that policy.

Reference tables require `path`, `pattern`, and `source`; optional `count` defaults
to one and `exclude` filters complete matches. Patterns use Python MULTILINE
regex syntax and a named `(?P<value>...)` capture, as in `ReleaseRule`.
`previous-tag` resolves to the baseline release, `tag` to the current release,
and `version` to the current release without its leading `v`. All three selectors
must be represented. Consumers choose precise package and report patterns;
this adapter does not infer report layouts or benchmark eligibility.

Without `repository`, links are URL-encoded relative paths from the document.
With `repository`, the adapter creates GitHub blob links and a raw SVG link at
the current release tag, and requires a locally present tag containing exact
payload, manifest, reference, linked-file, and figure bytes. Generated figures
are checked as candidates, before writes. There is no missing-tag fallback.
A linked document or reference to the publication document must match its
candidate at that tag too. Existing-release repairs that differ from stored blobs
remain local or require a new release.

Tag verification uses read-only Git operations at the repository root, checks
regular file modes, and compares raw blobs without newline decoding, Git
clean/smudge filters, or replacement objects. It never fetches. Local tag checks do not verify the remote
repository, a GitHub release asset, or a remote URL's availability. Network asset
retrieval and digest verification remain explicit separate `performance fetch`
operations. Independently establish the intended repository and trusted local tags.

The command supports mutually exclusive `--check` and `--preview` modes. Both
perform the same preflight as publication, including configured Git checks.
Check exits `0` when outputs match and `1` when stale or invalid; preview exits
`0` for valid candidates and prints diffs. Neither creates directories or files.
Default mode publishes the document and figures together. Invalid data raises
`ValueError`, wrong Python types may raise `TypeError`, and I/O/subprocess failures
retain the package's standard diagnostics. CLI failures use stderr without a
traceback; a successful check is silent.
