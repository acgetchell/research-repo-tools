# Criterion discovery and saved-sample contracts

Consumer setup and commands are in
the [task guide](RECORDING_PERFORMANCE.md#criterion-inventory-and-saved-baselines).

## Native inventory discovery

The supported `criterion_inventory` APIs are:

| API | Contract |
| --- | --- |
| `INVENTORY_SCHEMA` | `research-repo-tools/criterion-inventory/v1` |
| `CriterionInventory(suites, excluded=())` | Immutable sorted suite/ID pairs; IDs unique within and across suites; nonempty selected inventory |
| `DiscoveryCommand(name, command, cwd=".", environment=(), timeout=7200)` | Named native argument vector, root-relative POSIX cwd, environment overlay and positive integer timeout |
| `DiscoveryPlan(suites, required=(), excluded=())` | Unique suites and exact required/excluded IDs; a required ID cannot be excluded |
| `discover_inventory(root, plan, *, env=None)` | Bounded native listing, private Criterion output per suite, required coverage validation |
| `load_discovery(root, configuration)` | Strict schema-1 TOML; no commands executed while loading |
| `parse_benchmark_list(output)` | Sorted semantic IDs from native `: benchmark` records; rejects empty, malformed and duplicate records |
| `parse_inventory(payload)`, `serialize_inventory(inventory)` | Strict versioned UTF-8 JSON, deterministic bytes ending in LF |

`CriterionInventory.suites` preserves all discovered IDs, including exclusions;
`expected` selects the sorted IDs remaining after exact exclusions. Unknown
exclusions fail instead of concealing changed compatibility policy. `required`
is a minimum coverage assertion; unselected report rows still belong to the
complete inventory. It does not filter measurements. Commands must use the same
filters or compatibility options as the corresponding measurement.

Semantic IDs may be flat, contain spaces, Unicode or slashes, and need not be
portable paths. They must be nonempty without surrounding whitespace or control
characters. Listing parses stdout using strict UTF-8. LF and CRLF are equivalent
for discovery. Ordinary compiler and plotting noise is ignored; malformed records
containing a benchmark marker fail. A nonzero exit, missing executable, timeout,
invalid encoding or invalid inventory rejects the operation. Process exceptions
use the [shared subprocess contract](process-api.md#python-process-api).

Discovery TOML requires integer `schema=1` and `[[suites]]` tables. Each suite
requires `name` and `command`; optional fields are `cwd`, `environment` and
`timeout`. Optional top-level `required` and `excluded` arrays contain exact
semantic IDs. Unknown fields and invalid scalar types fail before any command.
All working directories are validated before the first command. Environments
overlay the caller's environment, inherited by default, with case-insensitive
key replacement. Ambiguous declared keys fail on every platform.
`Path` and `path` overlays become `PATH` for executable lookup and child processes;
multiple inherited PATH spellings fail unless replaced by a declared overlay.
`CRITERION_HOME` is always replaced, even if declared with another case, with a
fresh private directory removed after that suite's command. No inherited secrets
are saved. Commands are trusted code, may compile, and must honor this output
override; the process runner bounds the direct child, not its descendants.
Consumers own native descendant cleanup and must exclude concurrent writers.

The `performance discover` CLI refuses existing output files and configuration
aliases before execution; failed discovery never replaces existing evidence.

## Common-harness phases

`common_measurement.DiscoveryPolicy(discovery, sample_count=100,
statistics=("mean", "median"), confidence_level=0.95, unit="ns", *, sample_counts=())` carries validated
sample requirements without inventing expected IDs. `MeasurementPhase.policy`
accepts either this policy or `CompletePolicy`. A schema-2/3 phase requires exactly
one of `expected` or `discovery`; the latter uses the same table as standalone
discovery, omitting `schema`. Statistical settings remain at measurement top level.
Schema 3 also accepts an exhaustive phase `sample-counts` map. In the public API,
set `sample_count=None` and pass immutable full-ID/count pairs as `sample_counts`.
Assignments are validated immediately; exact discovered inventory coverage is
checked before either gate or timing. The resolved policy is retained alongside
the discovery inventory in v2 evidence. See [complete sampling](complete-run-api.md#complete-criterion-data)
for the schema, historical compatibility and phase-count contract.

For example, replace a phase's `expected` entry with:

```toml
[phases.baseline.discovery]
required = ["library/workload", "reference/workload"]

[[phases.baseline.discovery.suites]]
name = "timings"
command = ["cargo", "bench", "--locked", "--bench", "timings", "--", "--list"]
cwd = "."
timeout = 7200
```

The current harness is installed into both prepared sources first. Provenance
and required source/tool compatibility are checked, each phase's discovery executes in its
prepared source with the phase environment, and exact discovered policies are
created. Required IDs and report-series coverage must pass before either gate
or measurement. Source/harness and tool/host changes or any Criterion tree
appearing during discovery reject the run without deleting that output.
Required `context.discovery-inventory` compatibility is checked after both
inventories are captured, before either correctness gate or measurement.
Both correctness gates then pass before either phase is measured.

Run provenance retains a single-line versioned `context.discovery-inventory`
for each discovered phase, including suites and exclusions. Reading retained
evidence verifies that its selected IDs match that phase's exact `CompletePolicy`.
Original explicit inventories remain supported without this optional field.
This field records discovery results; preserve the discovery/measurement
configuration at an identifiable revision or as an immutable companion to retain
the exact commands, working directories, environments and selection declarations.
The completeness parser, retained run schema and scientific validation engine
remain the [existing complete-run contracts](complete-run-api.md).

## Saved-sample verification

The supported `criterion_baselines` APIs are:

| API | Contract |
| --- | --- |
| `SavedSamplePlan(criterion_dir, saved_sample, policy, fresh_sample="new", raw_files=("benchmark.json", "estimates.json", "sample.json"), *, storage_paths=())` | Portable root-relative tree path, safe sample names, complete policy, required raw filenames and independent storage assertions |
| `load_saved_sample(root, configuration, *, saved_sample=None, criterion_dir=None, suite=None)` | Strict schema-1/2 TOML using exactly one of explicit `expected` IDs or a retained `inventory` file, with validated runtime overrides and optional suite selection |
| `validate_saved_sample(criterion_dir, saved_sample, policy, *, fresh_sample="new", raw_files=("benchmark.json", "estimates.json", "sample.json"), storage_paths=())` | Read-only complete inventories, declared storage and exact raw-file equality; returns the saved `CompleteSample` |

Saved-sample TOML requires integer `schema=1` or `schema=2`, `saved-sample`
(unless supplied at runtime) and exactly one of
`expected` or `inventory`. Optional fields are `criterion-dir` (default
`target/criterion`), `fresh-sample` (default `new`), `raw-files`, `sample-count`,
`statistics`, `confidence-level`, `unit`, `required` and `storage-paths`.
See the [runtime selection and storage contract](DECLARING_PERFORMANCE.md#saved-sample-verification).
Statistical defaults match
`CompletePolicy`. The inventory path is relative to the consumer root. Raw files
must be unique portable single JSON filenames, including under case folding and
Unicode normalization, and include all three core files.
Additional required JSON, including `tukey.json`, must parse even when both copies
have identical bytes. Unknown native JSON fields remain accepted and preserved;
outlier interpretation remains consumer policy.

Schema 1 retains uniform sampling. Schema 2 additionally accepts
`sample-counts`, an exhaustive exact full-ID/count map, mutually exclusive with
`sample-count`. It uses the same `CompletePolicy` validation as common-harness
measurements, including when IDs come from a retained `inventory`. The public
saved-sample API accepts either policy. No historical files or raw arrays are
rewritten during validation.

Saved names use ASCII letters/digits followed by letters/digits/dots/underscores/
hyphens. `new`, `base`, `change`, `report`, Windows device names, traversal,
separators and trailing dots/spaces are rejected on every platform. Fresh and
saved names must be distinct even under case folding. Tree links/junctions and
nonportable storage paths and non-regular core files fail through the existing collector. Consumer-relative
CLI paths also reject linked ancestors inside the consumer root; the direct
Python API follows ordinary parent path resolution, rejecting links at the tree
root and within it.

Both samples pass `collect_complete_sample` with the same exact policy. Missing,
duplicate, stale/unexpected IDs, incomplete rows, invalid sample counts and
missing/wrong intervals fail through that engine. Cases pair by semantic ID;
their separate sanitized storage paths must match as well. Every configured raw
file is read in binary mode, parsed as UTF-8 JSON and compared exactly, including
line endings and formatting. Unconfigured extra raw filenames and unrelated
Criterion samples are not selected. No sample-count or interval requirements are
weakened, and no fresh data is reconstructed from saved output.

Execute discovery, correctness gates, measurement, saved-copy validation, then
packaging/publication in that order. A successful benchmark exit can accompany
a failed copy. Verification reads both output trees and writes nothing; rejected
evidence stays available for diagnosis. It verifies the supplied fresh/saved
relationship, not when a standalone tree was measured. Use fresh isolated output
and provenance checks to establish freshness. Consumers retain required benchmark
selections, compatibility exclusions, phase/suite assignments, scientific
assertions, and release acceptance policy.

## Parallel suite transport

These additions target v0.2.4. `load_saved_sample(..., suite=name)` and
`performance verify-saved --suite NAME` validate the entire saved-sample
configuration first, including required inventory coverage, storage assertions
and exhaustive per-case counts. They then select that suite's included IDs,
counts and storage paths. Unknown or entirely excluded suites fail. Explicit
`expected` configurations cannot select a suite because they declare no suite
membership. Without `suite`, verification remains complete-inventory validation.

The supported `criterion_suites` APIs are:

| API | Contract |
| --- | --- |
| `SUITE_SCHEMA` | `research-repo-tools/criterion-suite/v1` |
| `SuiteRun(provenance, session, host, started_at, finished_at)` | Immutable `Provenance`, attempt identity, original `HostMetadata`, ordered nonnegative integer UTC Unix seconds |
| `pack_saved_suite(root, configuration, suite, output, *, run, saved_sample=None, criterion_dir=None, limits=ArchiveLimits())` | Verify one suite, retain original raw bytes and metadata in an absent tar.gz output, return its `CompleteSample` |
| `assemble_saved_suites(root, configuration, archives, output, *, revision, session, saved_sample=None, limits=ArchiveLimits())` | Verify one archive per included suite against the independent complete contract and source/session, publish an absent combined tar.gz, return the complete `CompleteSample` |

Configuration, archive and output names are portable consumer-root-relative
paths. Inputs and output ancestors cannot traverse symlinks/junctions. Callers
exclude concurrent writers. Existing output paths fail before work begins; caught
failures publish nothing. Publication uses the shared recoverable file transaction.
`ArchiveLimits` bounds each input and the aggregate output; all extraction uses
the existing staged tar/ZIP reader and its portable path, link, special-file,
traversal, duplicate and implicit-directory alias checks.

Packing captures only the configured fresh and saved raw filenames. It verifies
the captured bytes again before publication and omits unrelated Criterion
reports. `suite.json` records the full inventory (including exclusions), full
sampling policy, saved/fresh names, raw filename requirements and independent
storage assertions. Its `files` map binds every retained raw file to SHA-256.
`run` retains the original `Provenance`, session, host and interval. The CLI reads
`--host` from a measurement-runner record; it never captures the packaging host.
Repeat `--context KEY=VALUE` to retain command, feature, dependency or runner
identity, and optionally supply `--source-sha256` and `--harness-sha256`.

Assembly rejects missing, duplicate, unknown or extra included suites; altered
full contracts; mismatched source revision/session; missing or extra files;
digest changes; invalid fresh/saved pairs; and cross-suite storage collisions.
An entirely excluded suite requires no artifact. The combined archive contains
the original Criterion files at its root, `criterion-inventory.json`, and exact
original manifests at `release-suites/<index>.json`, indexed by sorted included
suite name. Suite names are metadata, never interpolated into filesystem paths.
Reserved metadata paths also participate in collision checking.

Host differences are retained and allowed for parallel absolute measurements.
No comparison eligibility is inferred and no host is probed during replay.
The source/session/timestamps are caller assertions, not proofs of when native
code ran. Use fresh isolated output and trusted runner orchestration to establish
freshness. Manifest digests detect changed raw files; they are not signatures.
Consumers continue to own scientific inputs/gates, discovery commands, sampling
choices, matrix/job budgets, artifact selection and release publication.
