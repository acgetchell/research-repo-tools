# Configured workflow contracts

These additions belong to the coordinated v0.1.4 extraction. Availability in the
working tree does not mean that release has been published. Consumer commands
and configuration examples are in the [README](../README.md#configured-benchmark-workflows).
The [MCMC ownership map](mcmc-extraction.md) defines the first adoption gate.

## Measurement and pair selection

`release_pairs.ReleasePair(current, baseline)` normalizes stable tags and exposes
`stem`. `resolve_pair(mode, *, package_tag, releases=(), order="published",
current=None, baseline=None)` accepts four modes:

| Mode | Selection |
| --- | --- |
| `current-vs-latest` | Current package working files versus the latest selected published release; identical labels are permitted only here |
| `explicit` | Both supplied, distinct stable tags; no discovery |
| `infer-release` | Published package versus its predecessor in the selected order, or a newer prospective package versus the latest release |
| `published-latest` | Two latest stable published releases in the selected order |

`order="published"` sorts timezone-aware publication timestamps, then version for
ties. `order="version"` sorts numeric versions. Drafts and prereleases are removed
by discovery; missing predecessors, duplicate tags, or invalid identities fail.
`releases.published_releases(root, *, repository=None)` supports an explicit
GitHub `owner/name`. The inherited discovery bound is 1,000 releases.

`measurement.load_measurement(root, configuration)` reads standalone schema-1
TOML into frozen `MeasurementConfig`. Required fields are `command`, `sources`,
and `harness`. Commands are argument arrays without a shell. Source/harness
entries are root-relative globs; each must match regular files. Traversal,
symlinks, portable aliases, and unmatched patterns fail. Inventories are sorted
and deduplicated. Optional fields follow the packaged benchmark template:

- `criterion-dir`, `sample`, `statistic`, `unit`, and positive integer `timeout`
  default to `target/criterion`, `new`, `median`, `ns`, and 7,200 seconds.
- `probes` maps names to version command arrays. Commands must succeed within
  30 seconds with nonempty stdout. Multiline results are stored as JSON strings.
- `dependencies` maps Cargo package names to lockfiles; each must contain exactly
  one matching version. Ambiguous versions fail rather than being guessed.
- `context` supplies string metadata without replacing captured keys.
- `compatible` selects provenance fields using `compare_provenance` syntax.
  Required unknown values fail. The consumer chooses scientific compatibility.

`capture_provenance(root, config, tag, *, mode)` captures HEAD, versioned source
and harness fingerprints, complete inventories, host OS/architecture/available
CPU, exact command, probes, dependencies, mode, and release. Missing CPU identity
stays unknown. `measure_checkout` additionally executes the trusted command with
live inherited streams, collects the configured Criterion sample, and rejects
changes to captured inputs or metadata. The sample must be absent before the
run; use a fresh checkout to avoid mixing stale benchmark rows.

`measure_pair(root, config, pair, *, working_tree=False,
allow_git_mutations=False)` compares separate fresh checkouts and returns
`Evidence`. It requires explicit mutation opt-in. Tagged sources use locally
available stable tags; it never fetches. Working files use HEAD plus the exact
binary patch and tracked/nonignored untracked inputs. Parent checkout changes
during measurement reject the result. No evidence is published on failure.
Inputs and benchmark programs must be trusted; execution is not sandboxed.

`worktrees.TreeSnapshot(revision, patch, untracked)` stores a full commit ID,
patch bytes, and `(relative_name, bytes, ordinary_permission_bits)` entries.
`capture_snapshot(root)` is read-only and captures twice to detect edits.
It rejects links/submodules; ignored files are intentionally absent.
`apply_snapshot(checkout, snapshot)` is for a new checkout at that revision only.
It transports binary stdin unchanged and rejects file collisions before applying.
`temporary_worktree(root, destination, revision, *, allow_git_mutations=False)`
creates/removes one detached checkout. Cleanup errors retain a recovery path;
combined operation/cleanup failures preserve both exceptions. Failures during
application may partially modify only the disposable checkout. There is no
concurrent-writer lock or promise of cleanup after process termination.

## Release assets

`release_assets.lookup_release(root, repository, tag)` uses authenticated `gh`
and returns `GitHubRelease` with identity, draft/immutable/prerelease state and
asset names, IDs, sizes, and optional provider digests. Draft-aware
[`gh release view`](https://cli.github.com/manual/gh_release_view) supplies the
database ID; the REST lookup revalidates its identity and state. Malformed responses fail.
`require_draft(release)` requires a mutable stable draft.

`download_release_asset(root, repository, tag, name, destination, *, limits,
expected_sha256=None, allow_draft=False)` selects the exact asset ID and checks
declared size, configured bounds, and supplied/provider hashes. Downloads have
a deadline and bounded stdout; partial failed output is rejected. Historical
assets need no fabricated trusted digest. Authentication and GitHub HTTPS provide
the repository access boundary; hashes establish integrity, not authorship.

`package_baseline(sample, provenance, destination)` writes a deterministic tar.gz
with shared `sample.json` and `provenance.json` entries. `read_baseline(archive,
*, limits, legacy_configuration=None, expected_tag=None)` performs bounded safe
extraction and verifies the shared schema and envelope. Explicit legacy layout
configuration is used only when neither shared entry exists. Broken shared
archives cannot fall back to a more permissive legacy reader.

`publish_release_asset(root, repository, tag, asset, *, publish=False)` stages
inert bytes, requires a mutable draft, uploads absent names without overwrite,
and downloads for exact comparison. Identical retries succeed; differing bytes
fail. Unrelated assets remain unchanged. Immediately before publication it
rechecks draft mutability and release identity, then clears the draft flag only
when `publish=True`. There is no cross-client lock across GitHub API calls.

Keep benchmark jobs read-only and credential-free. A separate writer job should
install the exact registry package, download the inert artifact, and invoke this
entry point without checking out or executing benchmark repository code. Cache
keys and action pins stay in consumer workflows. Cache managed tools by OS,
architecture, exact tool declarations, Rust toolchain and Python lock identity;
restoration still requires verification through `toolchain check` or `export`.

## Retained evidence, conversion, and promotion

`criterion.serialize_sample` / `parse_sample` use
`research-repo-tools/criterion-sample/v1`. `serialize_comparison_csv` includes
both full inventories with `common`, `added`, or `missing` coverage, unit,
statistic, points, marginal bounds and confidence levels. Empty values mean
unknown; no ratio interval or significance claim is inferred.

`legacy_evidence.convert_csv(payload, manifest, configuration)` verifies the
original payload hash before decoding. Schema-1 conversion TOML declares an
exact header, column mapping, statistic/unit, metadata selectors, supported
schemas, constants, optional coverage labels, literal assertions, and equal
identity selector pairs. `sources.baseline/current` each declare selectors for
`revision`, `release`, and opaque `record`. Selectors use dotted JSON keys or `$`
for the root. Missing or conflicting identities, unsupported schemas, malformed
numbers, duplicate rows and inconsistent coverage fail. Origin hashes and opaque
historical metadata are recorded under `legacy.*`; old fingerprint framing is
never promoted to the shared source/harness digest fields.

`read_legacy_baseline(archive, configuration, expected_tag, *, limits)` uses
declared metadata path, schemas, source selectors, Criterion directory, and
sample template with exactly one `{tag}`. It preserves unknown provenance and
records original archive/configuration hashes. These are migration readers;
there is no legacy writer. Retire configurations after verified companions and
shared release assets eliminate ordinary legacy reads.

`performance_reports.load_report_plan(root, configuration, *, payload=None,
manifest=None)` returns a `PublicationPlan`. Schema-1 report TOML requires
`current`, `archive`, and `title`, with optional `prose-file`. Both new evidence
paths mean promotion; omitting both selects retained evidence from the current
report's versioned first-line identity. Same-label local comparisons cannot be
promoted. Shared evidence and CSV use `<current>-vs-<baseline>` stems. Promotion
archives the previous Markdown, rebases its generated evidence links, refreshes
the index, and replaces all outputs in one rollback-capable plan. Once archived,
a pair's evidence is immutable; active evidence may change before archival.
The current report may share the archive directory; it is excluded from the
historical inventory and archive index.
Old reports keep their paths; start shared reports at new paths during migration.

`evidence_pair`, `parse_report_pair`, `retained_paths`, and `render_report` expose
the same identity/path/rendering rules without filesystem changes. Report prose,
table selection, and interpretations remain consumer content.

Publication TOML additionally accepts `prose-file`, `current-sources`,
`current-harness`, and `tag-policy="existing"|"prepare"`. Default tagged links
require an existing tag. Explicit `prepare` requires `repository`, newer
working-tree evidence, and both current inventories with shared fingerprints.
Those inputs must still match the measurement. Saved plans re-expand both glob
inventories before publication, rejecting matching additions, removals, or renames
as well as changes to existing bytes. A missing future tag is allowed;
if it exists when planning or publishing, exact retained/reference/generated
blob checks apply. This does not bypass an existing tag mismatch. `verify-tag`
on a provenance pin additionally checks its recorded revision.

## File selection, validation, and CI export

`selection.select_files(root, *, include=(), exclude=())` returns sorted regular
tracked/nonignored untracked paths, omitting deleted files. Includes are Git
pathspecs; excludes are POSIX glob patterns. Git discovery is read-only and
NUL-delimited. Links, traversal and portable aliases fail. `argument_batches`
validates complete filename input before making bounded argument tuples; paths
receive `./` to prevent option interpretation. Bounds account for quoted Windows
UTF-16 command size and POSIX byte size. `run_selected` inherits live streams
and stops at the first failed batch; a genuinely empty selection is a no-op.

`validation.require_executables(root, names)` resolves prerequisites without
launching them. `run_checks(root, configuration, names=())` validates all schema-1
TOML checks before executing the selected names in configuration order. Each
check has `name`, argument-array `command`, optional literal stdout `expect`
markers and positive integer `timeout` (default 300). Optional `prerequisites`
are resolved first. A failed command or missing marker fails the workflow.
`check_cargo_metadata(root, *, package=None)` validates one Cargo package's
description, keywords and category counts; native Cargo packaging remains the
complete publication preflight.

`ci.export_environment(destination, names, *, environment=None)` validates every
requested name and nonempty value before appending UTF-8/LF `NAME=value` lines.
NUL/CR/LF, duplicate or invalid names, reserved GitHub/runner variables and
`NODE_OPTIONS` are rejected. Spaces and Windows paths are preserved. The leaf
must not be a symlink. An OS write failure can partially append; validation
failure writes nothing. `ci export` defaults to `GITHUB_ENV`; `toolchain export`
first verifies all managed tools, then exports their paths and Rust settings.

All workflows use the common path, process, deadline and rollback contracts.
Configuration errors raise `ValueError`; process/I/O exceptions retain their
normal types. Exclusive writer control is required. Native platform evidence
and installed-wheel/sdist checks are separate from deterministic boundary models.
