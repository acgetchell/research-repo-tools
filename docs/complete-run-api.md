# Complete measurements and host metadata

## Availability-aware suite APIs (v0.2.5)

`suite_measurement.AcquisitionSuite(name, contract, measurement, since=None,
before=None, nominal_seconds_per_case=None, compilation=())` wraps a schema-4
export plan. `SuiteMeasurementPlan(suites)` is loaded by
`measurement.load_measurement` from schema 5. Its `document()` returns the
canonical declaration. `measurement.measure_pair` adds keyword arguments
`checkpoint: Path | None`, `resume=False` and `progress: ProgressReporter | None`.
The direct/prepared CLI and `performance_release.measure_release` expose the same
checkpoint/resume options; retention accepts a root-relative checkpoint string.
Lower-level `measurement_checkpoints.open_checkpoint` handles are usable only
inside their owning context; reads and writes reject expired handles.

`suite_runs.parse_suite_run(evidence)` and `complete_runs.validate_run_evidence`
return a `SuiteRun` for complete-run v4. Its authoritative `units` retain absence
decisions, session/collection interval, complete samples and original provenance.
Its `phases` and `series` are derived presentation views: an absent phase is a
`PhaseView` with zero cases, not a `CompleteSample`. Historical evidence still
returns `CompleteRun`. `serialize_run` remains the v1–v3 serializer; retain v4
through the ordinary exact-byte evidence envelope.

`suite_runs.comparison_incompatibilities(evidence, numerator, denominator)` takes
two `(phase, native_full_id)` tuples and returns diagnostic strings. Unknown
observations raise `ValueError`. Tables and ratio figures use individual checks;
the legacy global `ratio_incompatibilities` deliberately rejects blanket v4
eligibility. `validate_run_policy` checks independently supplied suite policy;
`release=True` additionally validates shared release comparisons. Offline reports,
immutable history and transactional README publication use the same public APIs.
Current document-input checks derive from each suite's recorded declarations.

`performance_progress.ProgressReporter(*, mode="human", cadence=10, stream=None,
observer=None, clock=time.monotonic)` exposes the documented event contract.
The default stream is stderr. An observer receives event dictionaries; heartbeats
invoke it on a background thread. Keep custom observers fast and nonblocking.
The [schema-5 guide](DECLARING_PERFORMANCE.md#availability-progress-and-resume-schema-5)
defines availability, comparison, checkpoint, cadence and recovery semantics.

Complete runs retain original Criterion files, phase-owned series and recorded
host/source identities. Consumer commands and editable scaffolds are described
in the [task guide](RECORDING_PERFORMANCE.md#common-harness-and-complete-runs).
Existing comparison, baseline asset and report schema-1 contracts remain supported.
For source selection, independent eligibility and the schema-4 acquisition
contract, see [Declaring complete performance workflows](DECLARING_PERFORMANCE.md).

## Configuration and measurement

`measurement.load_measurement(root, configuration)` accepts schema-1
`MeasurementConfig`, schema-2/3 `common_measurement.CommonHarnessPlan`, or
schema-4 `common_measurement.CompleteMeasurementPlan`. Schema 4 explicitly
selects common/independent harnesses and export/worktree sources; its
[acquisition contract](DECLARING_PERFORMANCE.md#complete-acquisition-contract-schema-4)
defines complete-run v3 and union-inventory reporting. The schema-2/3 contract
below preserves historical common-harness behavior.

The complete plans contain a shared `measurement` configuration, `baseline` and
`current` `MeasurementPhase` objects, and a tuple of `RunSeries` views or
`DiscoveredSeries` selectors.
`MeasurementPhase(command, gate, policy, environment=())` uses immutable argument
vectors, a completeness policy and string environment pairs. They also accept
`DiscoveryPolicy`; see the [Criterion contracts](criterion-api.md).
`CommonHarnessPlan(..., *, schema=2)` preserves the original uniform measurement
contract. Pass `schema=3` explicitly for phase-owned sampling policies and v2
evidence; loading a plan preserves its declared schema.

Schemas 2 and 3 require `sources`, `harness`, `phases.baseline`, `phases.current` and
`series`. Each phase requires `command`, `gate`, and either nonempty exact
`expected` semantic IDs or a configured `discovery` table, with optional
`environment`. Each series table requires `name`,
`phase` and either `rows` mapping display labels to semantic IDs from that phase,
or the [bounded `select` declaration](DECLARING_PERFORMANCE.md#measurement-and-eligibility).
Series order controls table column order; labels are sorted. Unselected cases
remain part of the run. A reused reference is a view of its original phase,
never an invented second measurement. At least one toolchain version probe is
required. Required compatibility always includes the harness and all declared
tools and dependencies, plus consumer-selected fields.

Shared options are `criterion-dir`, `sample`, `timeout`, `unit`, `probes`,
`dependencies`, `context` and `compatible` from schema 1. Completeness options
are `sample-count` (integer at least 2, default 100), `statistics` (unique mean
and/or median, default both), and `confidence-level` (strictly between 0 and 1,
default 0.95). Both phases share statistics, confidence and unit. Schema 2
requires one uniform count shared by both phases and emits complete-run v1.
Schema 3 emits complete-run v2 and also accepts each phase's `sample-counts`
table mapping exact semantic IDs to integer counts of at least two. A phase map
must cover every expected or discovered ID exactly once; there are no prefix,
glob, storage-path or fallback assignments. Missing, unexpected and duplicate
assignments fail. Phase maps exclude the top-level `sample-count`; phases
without a map use the uniform default of 100. Counts may differ between phases
when the consumer declares that policy. Discovery resolves and checks the full
count map before either gate or measurement runs. Consumers retain all workload
selection and sample-count decisions, including explicit Criterion defaults.
The schema is strict: unknown fields, malformed scalars and duplicate TOML keys
fail before commands run. Every source/harness glob must match regular files in
the current checkout. Include gate programs, native toolchain declarations,
build manifests and complete dependency resolution files in the harness.

Schemas 2 and 3 use `measure_pair`'s opt-in isolated worktree lifecycle.
`common_measurement.measure_prepared_pair(baseline, current, plan,
pair, *, working_tree=False)` exposes the same common-harness operation for two
distinct, nonoverlapping disposable checkouts supplied by a caller. It changes
files in those checkouts; the caller owns cleanup. It does not create Git refs.
The harness is captured from current, including ordinary permission bits;
obsolete files matched by its patterns are removed and captured bytes are
installed into both sources. Library source patterns are captured independently
before substitution and again afterward. The shared `source_sha256` identifies
the effective measured input; `context.original-source-sha256` and its inventory
identify the source before substitution. Both use the documented shared file
framing, never a legacy digest relabeled as shared. Schema-3 measurement records
the entire resolved policy in each phase's reserved `context.sampling-policy`.
The reader requires it to match the policy in that phase's v2 payload. It is
checked before timing when selected in `compatible`; phases with different
inventories or policies should not select equality for that field.

Each phase records revision, release, command, declared environment, gate command
and passed status, host observations, source and harness inventories, probes
and dependencies. The original and effective fingerprints remain distinct when
build manifests belong to both inventories. Source/harness fingerprints and
tool/host observations are checked around gates and measurements. Compatibility
is checked before timing and when reading retained evidence. Missing required
values fail. Gates must leave no timing output, and the Criterion root must be
absent initially; partial or stale scratch is rejected. A failed gate prevents
all timing. Configured commands inherit live streams and have positive bounded
timeouts using the existing process API.

Environment pairs overlay the inherited process environment, rejecting keys
that differ only by case. Only explicitly declared pairs are retained; inherited
secrets are not enumerated. Declare performance-relevant overrides and select
required provenance fields deliberately. Workload selection, API compatibility
adapters and release eligibility remain consumer configuration or code. Commands
are trusted code and are not sandboxed; the process timeout covers the direct
child. Callers must exclude concurrent writers and manage descendant processes.

## Complete Criterion data

The supported names in `complete_runs` are:

| API | Contract |
| --- | --- |
| `LEGACY_RUN_SCHEMA` | Alias of `RUN_SCHEMA` |
| `RUN_SCHEMA` | `research-repo-tools/complete-run/v1`; original uniform policy and provenance rules |
| `RUN_SCHEMAS` | All four supported schemas, in version order |
| `RUN_SCHEMA_V2` | `research-repo-tools/complete-run/v2`; explicit phase-owned policy and provenance rules |
| `RUN_SCHEMA_V3` | `research-repo-tools/complete-run/v3`; common or independent acquisition and supplemental raw JSON files |
| `RUN_SCHEMA_V4` | `research-repo-tools/complete-run/v4`; suite availability, complete acquisition units and observation-level comparisons |
| `CompleteCase(storage_path, benchmark, estimates, sample, *, raw_files=())` | Portable relative path plus original immutable bytes for Criterion's three core JSON files and optional `(filename, bytes)` pairs |
| `CompletePolicy(expected, sample_count=100, statistics=("mean", "median"), confidence_level=0.95, unit="ns", *, sample_counts=())` | Exact unique semantic IDs; select uniform counts or exhaustive immutable ID/count pairs with `sample_count=None` |
| `CompleteRun(phases, series, compatible=("harness_sha256",), *, schema=RUN_SCHEMA, harness_mode="common")` | Exactly baseline/current complete samples, named views and required provenance selection; defaults to v1, with v2 for independent phase counts and v3 for independent acquisition |
| `CompleteSample(policy, cases)` | Immutable sorted cases validated against the complete policy |
| `RunSeries(name, phase, rows)` | Unique display labels mapped to full IDs in one recorded phase |
| `collect_complete_sample(criterion_dir, sample, policy, *, raw_files=())` | Reject unsafe trees, incomplete rows, duplicate semantic IDs and missing/unexpected inventory; optional supplemental JSON filenames are validated before tree access and all selected files must be regular before reading |
| `parse_policy(payload)`, `serialize_policy(policy)` | Strict v2 policy JSON for manually constructed phase provenance |
| `parse_run(payload)`, `serialize_run(run)` | Versioned strict shared JSON; original Criterion bytes stored as base64 |
| `ratio_incompatibilities(evidence)` | For v3, derive ratio-ineligibility reasons from recorded host, harness, workload and inventory differences; v1/v2 return `()` without assessing ratio eligibility |
| `render_run(evidence, *, title="Benchmark timings", report_path=None, payload_path=None, manifest_path=None)` | Offline named-series timings and recorded per-phase provenance; supply all three root-relative publication paths for evidence links |
| `run_identity(evidence)` | SHA-256 of exact payload and envelope with versioned framing |
| `validate_run_evidence(evidence)` | Parse complete measurements and verify phase/source/gate/compatibility contracts |

`CompleteCase.full_id` comes from `benchmark.json`, never from its sanitized
storage path. `sample_count` counts original raw `iters`/`times` pairs;
iterations must be whole positive finite numbers, timings and per-iteration
timings positive finite, and arrays equally sized. Every selected statistic
must have a complete ordered interval at the required confidence. Extra native
Criterion fields are retained byte for byte. `estimate(statistic)` returns the
shared validated `Estimate`. No consistency between raw samples and estimated
bootstrap statistics, significance test, ratio interval or scientific eligibility
is inferred. Consumers own those scientific assertions.

For example, a consumer's exact policy can preserve 10 and 100 original samples:

```python
from research_repo_tools.complete_runs import CompletePolicy

policy = CompletePolicy(
    ("geometry / expensive", "geometry / ordinary"),
    sample_count=None,
    sample_counts=(("geometry / expensive", 10), ("geometry / ordinary", 100)),
)
assert policy.count_for("geometry / expensive") == 10
```

`count_for(full_id)` rejects IDs outside the policy. Inputs are defensively
copied and sorted. Overlapping assignments fail even when their counts agree.
No arrays are padded, truncated or modified to satisfy a policy.

Existing uniform `CompleteRun(phases, series)` and
`Evidence(serialize_run(run), RUN_SCHEMA, sources)` calls retain v1 behavior.
For per-case policies or different uniform counts between phases, construct
`CompleteRun(..., schema=RUN_SCHEMA_V2)` and use `run.schema` in the evidence
envelope. Schema-3 measurement plans select v2 automatically.

The v2 run retains the existing top-level `schema`, `compatible`, `series` and
`phases` fields, with exactly `baseline` and `current`. Each phase still has
`policy` and base64-encoded original `cases`. Its policy requires `expected`,
`sample_count`, `sample_counts`, `statistics`, `confidence_level` and `unit`.
Uniform mode requires an integer `sample_count` and an empty `sample_counts`
object. Per-case mode requires `sample_count: null` and a nonempty
`sample_counts` object covering `expected` exactly. Unknown fields, missing
fields and duplicate JSON keys fail. `serialize_policy(policy).decode("utf-8")`
is compact JSON without a trailing newline, suitable for that phase's
`sampling-policy` provenance entry when manually constructing `Evidence`.
Payload and envelope schema names must agree. The exact payload and envelope,
including both policies, participate in `run_identity`; equal raw measurements
with differently declared policies remain different evidence.

The v1 parser requires its original five policy fields, a non-null uniform count
and the same count in both phases. Parsing and serialization preserve v1;
its historical provenance does not gain a `sampling-policy` field. Changing a
schema label is not a migration: v2 requires its own fields and recorded policy,
and legacy schema-3 consumer evidence remains a separate unsupported format.

The standard report includes original and measured source fingerprints, the
common harness fingerprint, revision/release/mode, recorded host observations,
named tool/toolchain probes, dependency versions, measurement argv and independent
gate status/argv for each phase. Reference series keep their originating phase;
displaying them beside current timings does not claim another measurement.
Missing optional observations say `not recorded`. Required provenance and gates
still fail validation when absent; the renderer does not invent replacements.

Environment overrides show names only. Arbitrary consumer context and gate
stdout are omitted. Credential-shaped command options/assignments, URLs, recognized
inline script bodies and sensitive recorded environment values are redacted from the
summary. Keep credentials out of measurement declarations: evidence files retain
their original values, and arbitrary positional secrets cannot be identified
reliably. Text is escaped, multiline tool stdout stays within its table cell, and
publication-relative URLs are percent-encoded using POSIX separators on every
platform. Without publication paths, the pure renderer explicitly reports that
evidence links were not supplied. It never guesses the evidence location or
captures the machine performing an offline rerender.

The CLI renderer supplies evidence links when `--output` and both evidence paths
share a portable relative-path context, including absolute paths outside the
consumer root. Stdout, paths on different Windows drives, and filenames outside
the portable publication contract remain explicitly unlinked.

## Retention and publication

Schema-2 report TOML requires `schema`, `current`, `archive`, and `title`.
`performance_reports.load_report_plan` dispatches to
`run_reports.load_run_report_plan(root, configuration, *, payload=None,
manifest=None)`. It returns the existing `PublicationPlan` for preview, check
or transactional publication. `performance_reports.render_report` and the CLI
renderer also recognize complete runs. The schema-1 comparison CSV exporter and
document configuration keep their existing contracts. Schema-2 document
publication selects complete runs directly; see
[publication configuration](publication-api.md#publication-configuration-v2).

Run IDs seed SHA-256 with `research-repo-tools/run-id/v1` plus NUL, followed by
each of the exact payload and exact envelope bytes framed with an eight-byte
big-endian length. They include all phases, raw data, policies, views and
provenance. Identical bytes deduplicate; changed measurements or provenance
create another run, including for identical release labels. There is no timestamp
or random salt to turn an identical retry into a new measurement.

Each `<archive>/runs/<id>/` retains immutable `run.json`, `evidence.json` and
canonical `report.md`. Mutable `index.json` lists all run identities and labels;
`latest.json` selects one indexed complete run. `README.md` links the full
history, and `current` renders the selected run with the configured title.
All move in one publication transaction. Existing run contents cannot be
replaced; differing bytes, partial directories, corrupt pointers and mismatching
indexes fail. Empty directories left by a caught rollback are harmless.
Do not share these new paths with legacy reports or schema-1 archives.

New reports use `research-repo-tools/run-report/v2`. New indexes use
`research-repo-tools/run-index/v2`, with an integer `report_renderer` (1 or 2)
in every run entry. The latest-pointer schema and content-derived run identity
remain version 1. Renderer versions describe presentation; they do not alter the
run payload, evidence envelope or evidence hashes. Current and new retained
reports link directly to that run's immutable `run.json` and `evidence.json`,
relative to each report's own directory. Measurement CLI reports link to their
explicit output evidence paths.

Version-1 indexes remain readable and select the frozen renderer-1 presentation
for exact-byte report validation. Promotion or offline rerender upgrades the
mutable index to version 2 and renders the current report with provenance and
links. Every existing immutable report, payload and envelope stays byte for byte,
including when an old run is selected or promoted again. Old reports therefore
retain their original presentation without new links. Mixed histories record
renderer 1 for those entries and renderer 2 for newly retained runs; unknown
renderer versions or report bytes inconsistent with the recorded version fail.
Older package versions cannot read version-2 indexes; keep the upgraded package
pin with the migrated publication configuration.

`run_reports.load_latest_run(root, archive)` validates the whole indexed history,
exact envelope hashes, content-derived IDs, phase provenance, stored reports,
labels and latest selection. It performs no measurement or host recapture.
Omitting both scratch paths uses this validated selection; supplying either
requires both and never falls back on missing or partial input. Same-label local
runs can be promoted. Store the archive outside disposable scratch.

Plans detect stale inputs/outputs and use the shared rollback/recovery contract.
They require exclusive writer control and are not crash-atomic across files.
Custom figures, document sections and indexes can be combined with run evidence
through `publication.plan_outputs` before `publish_publication`; render all
candidates before that one transaction. No version-control state is changed by
promotion. Archive downloading and extraction retain the existing bounded
release-asset contracts.

## Configured presentation

Import from `research_repo_tools.run_presentation`:

| API | Contract |
| --- | --- |
| `CoordinatePlot(title, x_label, y_label, x_scale="linear", y_scale="linear")` | Nonempty labels and `linear`/`log` scales |
| `RunLayout(series, rows, statistic, unit, missing="error", comparisons=())` | Explicit series/row order, recorded mean/median and timing unit; comparisons are `(numerator_name, denominator_name)` tuples |
| `RunRow(key, label, coordinate=None)` | Unique selection key and display label; optional finite numeric coordinate |
| `RunTableStyle(*, row_heading="Case", intervals="inline", timing_legend=False, comparison_columns=("ratio", "reduction"), ratio_headings=(), reduction_headings=(), ratio_suffix="")` | Frozen Markdown-only style |
| `SeriesSelection(name, label, rows)` | Existing retained view name, unique display label and unique `(row_key, semantic_full_id)` mappings |
| `render_coordinate_svg(evidence, layout, plot)` | Deterministic UTF-8/LF accessible SVG with point estimates and recorded marginal timing intervals |
| `render_run_ratios(evidence, layout)` | Exactly one configured comparison, rendered as point-ratio bars with original phase labels and run identity |
| `render_run_table(evidence, layout, *, style=None)` | Markdown timings, marginal intervals, selected ratios/reductions and explicit selection coverage; omitted style preserves existing output |
| `serialize_run_csv(evidence, layout)` | Deterministic UTF-8/LF long-form CSV with each cell's run/series/phase/revision/release, fingerprints, row/coordinate/ID, statistic/unit, status and estimate/bounds/confidence |

Every renderer validates the entire complete evidence, including phase gates and
the selected schema's acquisition contracts, before selecting presentation data.
Independent v3 acquisition retains valid measurements with differing hosts,
workloads or inventories; it does not establish comparability. When
`ratio_incompatibilities(evidence)` reports differences, tables mark cross-phase
ratio and reduction cells unavailable; same-phase comparisons remain available.
`render_run_ratios` raises `ValueError` for an incompatible cross-phase SVG,
so a publication configured with that figure fails before writing any output.
For strict release eligibility, call `common_measurement.validate_run_policy(evidence, plan,
release=True)`. The [v3 acquisition contract](DECLARING_PERFORMANCE.md#complete-acquisition-contract-schema-4)
defines the common and independent modes. A series mapping must select
IDs belonging to that original named view; changing a label cannot change its
phase. Unknown views, duplicate row keys or benchmark mappings, incompatible
units and unrecorded statistics fail. No conversion or scientific eligibility
selection occurs. Unselected raw cases and original evidence bytes are untouched.

Tables follow configured row order and columns follow series order. CSV follows
series then row order. Plots sort rows by numeric coordinate and require a finite,
unique coordinate for every row. Linear coordinates can be zero or negative;
log coordinates must be strictly positive. The timing axis includes point values
and both recorded bounds, even when a bootstrap interval does not contain its
point. Constant domains are centered; geometry is normalized to avoid overflow.
Lines connect adjacent available points and break at unavailable rows. Legends,
tooltips and accessible descriptions preserve originating phase/release/revision;
the root SVG carries the complete-run identity. XML text is escaped and invalid
XML characters fail. No timestamps, host recapture, random IDs or plotting
dependencies are used. Visual font metrics depend on the viewer's sans-serif font.

Default `missing="error"` requires every selected series to map every row.
Explicit `missing="unavailable"` permits unmapped cells. Tables and CSV record
them, SVG legends count them, and ratio figures omit unavailable pairs with
accessible coverage. A mapped ID that is outside its retained view still fails.
Each series requires at least one selected benchmark. Coverage distinguishes
unavailable presentation rows from omitted cases in the originating phase.

Comparisons can include reused baseline-phase peers without calling them new
current-phase measurements. The ratio is numerator time / denominator time.
Time reduction is `100 × (numerator − denominator) / numerator`; positive means
less denominator time, negative means more. These are point comparisons: neither
ratio intervals, paired uncertainty, significance nor acceptance is inferred.
All derived output remains bound to the original complete-run identity.

`RunTableStyle` controls Markdown without changing the layout's selection,
estimates, arithmetic, CSV or figures. Existing positional and keyword calls to
`render_run_table(evidence, layout)` retain their exact default output. Style
controls and their matching schema-2 `table` keys are:

| Style field / TOML key | Contract |
| --- | --- |
| `comparison_columns` / `comparison-columns` | Ordered unique selection of `ratio` and/or `reduction`, emitted for each comparison; empty means timing columns only |
| `intervals` / `intervals` | `inline` (default) or `below`, which inserts `<br>` between the point and both recorded bounds |
| `ratio_headings` / `ratio-headings` | Empty uses numerator/denominator series labels; otherwise one nonempty heading per comparison, in layout order |
| `ratio_suffix` / `ratio-suffix` | Empty by default; `×` appends the multiplication sign to each available point ratio |
| `reduction_headings` / `reduction-headings` | Empty uses existing time-reduction headings; otherwise one heading per comparison |
| `row_heading` / `row-heading` | Nonempty first-column heading, default `Case`; timing headings still come from `SeriesSelection.label` |
| `timing_legend` / `timing-legend` | Boolean, default false; true omits cell units/confidence labels and states the recorded unit and confidence percentage in the caption with the selected statistic |

Custom comparison headings also render a key identifying each numerator and
denominator series. Labels and suffixes are escaped as Markdown text; only the
renderer inserts the interval line break. Unavailable cells have no fabricated
intervals or suffixes. Heading arrays, when nonempty, must match the number of
comparisons even if their column type is currently hidden. Unsupported style
values or heading counts raise `ValueError`; a non-style object raises `TypeError`.

Confidence text comes from recorded estimates, including levels other than
95%. Complete-run validation requires a complete interval at the recorded policy
level for every selected statistic and case, and matching levels across phases.
Differing levels are rejected before any table is produced, in every style;
presentation never weakens that evidence contract or substitutes 95%. Compact
tables retain both marginal bounds and the explanation that ratios are point
estimates without ratio intervals or significance claims. These controls are
available from v0.2.0; older consumers need a compatible published pin.
See the [task guide](PUBLISHING_REPORTS.md#complete-run-document-sections-and-coordinate-plots)
for the six-column ratio-only configuration and Python usage.

Pure renderers do no I/O. Use `publication.plan_outputs` to transactionally
publish standalone figures/CSV with validated payload/envelope bytes as inputs,
or use [schema-2 document configuration](publication-api.md#publication-configuration-v2)
to publish a document section, figures, CSV and evidence links together.

## Host and profiling metadata

`host_metadata.HostMetadata(os, architecture, cpu, physical_cores,
logical_threads, memory_bytes, tools=())` is a frozen typed observation. Text
fields and positive integer counts are nullable. Tools are unique named pairs
whose values are version stdout or `None`. `HOST_SCHEMA`, `serialize_host` and
`parse_host` provide the strict `research-repo-tools/host/v1` format. Parsing
retained bytes never probes a machine. Boolean counts and coerced numeric strings
are rejected in retained records.

`capture_host(root, *, probes=(), env=None, timeout=30)` uses
`process.cpu_description` and the common bounded process runner. It observes
Linux physical/core IDs and memory from `/proc`, macOS counts/memory via sysctl,
and Windows processor totals and memory via bounded CIM PowerShell commands.
Logical-thread fallback uses the standard library. Counts describe the host,
not affinity, container quotas or free memory. Missing facilities, denied access,
malformed data, missing tools and failed/timed-out version probes stay unknown.
Configured commands run with UTF-8 decoding in the selected root, using a C locale.
Probes replace the provided environment (or inherit the caller's if absent).

`measurement.capture_provenance(..., env=None)` builds on that host record;
it requires successful configured version probes for measured evidence.
`context.host` contains the complete versioned JSON record; available scalar
observations additionally expose `context.os`, `context.architecture`,
`context.cpu`, and `context.host.physical_cores`, `context.host.logical_threads`,
`context.host.memory_bytes`. Select individual fields for required compatibility
so unknown observations remain unknown.

`capture_profile(root, configuration)` returns
`research-repo-tools/profile/v1` JSON bytes. Strict schema-1 profiling TOML accepts
`context` (explicit string pairs), `probes` (argument arrays), `cargo-manifest`
and `rust-toolchain` (optional root-relative files). Omitted probes default to
Cargo version and verbose rustc; an explicit empty table captures no tools.
Selected declarations must exist and parse as native TOML. Entire structured
documents are retained, including workspace inheritance and profile tables.
Each declaration retains its exact UTF-8 source in `text` and a JSON projection
in `toml`. Dates, times and datetimes use `{ "toml_type": "date|time|datetime",
"value": "ISO 8601 text" }`; nonfinite floats use `toml_type: "float"` with
`"nan"`, `"inf"` or `"-inf"` as the value. Other scalars, arrays and tables keep
their JSON shape. Source text preserves original spelling and distinguishes
these scalar projections from native tables with the same keys. Captured
declarations are not claimed to be Cargo's resolved effective settings. Context
selection, dynamic CI labels and legacy human-readable formats remain consumer
responsibilities. CLI paths are explicit and cannot overwrite selected inputs.
Optional `measurement` and `release` must be supplied together to add a `source`
record captured through `measurement.capture_provenance` in profiling mode,
using that configuration's source/harness inventories and required probes.
This source record describes profiling inputs, without claiming that a timing
command or gate ran. Without it, `source` is null. No second Git/provenance engine
is introduced for profiling.
