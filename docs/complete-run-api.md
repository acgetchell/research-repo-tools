# Complete measurements and host metadata

These contracts target v0.1.8. Source availability, package publication, native
platform checks and consumer adoption are separate requirements. Consumer
commands and editable scaffolds are described in the [README](../README.md#common-harness-and-complete-runs).
Existing comparison, baseline asset and report schema-1 contracts remain supported.

## Configuration and measurement

`measurement.load_measurement(root, configuration)` accepts schema-1
`MeasurementConfig` or schema-2 `common_measurement.CommonHarnessPlan`.
The latter contains a shared `measurement` configuration, `baseline` and
`current` `MeasurementPhase` objects, and a tuple of `RunSeries` views.
`MeasurementPhase(command, gate, policy, environment=())` uses immutable argument
vectors, a completeness policy and string environment pairs.

Schema 2 requires `sources`, `harness`, `phases.baseline`, `phases.current` and
`series`. Each phase requires `command`, `gate`, and nonempty exact `expected`
semantic IDs, with optional `environment`. Each series table requires `name`,
`phase` and `rows` mapping display labels to semantic IDs from that phase.
Series order controls table column order; labels are sorted. Unselected cases
remain part of the run. A reused reference is a view of its original phase,
never an invented second measurement. At least one toolchain version probe is
required. Required compatibility always includes the harness and all declared
tools and dependencies, plus consumer-selected fields.

Shared options are `criterion-dir`, `sample`, `timeout`, `unit`, `probes`,
`dependencies`, `context` and `compatible` from schema 1. Completeness options
are `sample-count` (integer at least 2, default 100), `statistics` (unique mean
and/or median, default both), and `confidence-level` (strictly between 0 and 1,
default 0.95). Both phases share count, statistics, confidence and unit.
The schema is strict: unknown fields, malformed scalars and duplicate TOML keys
fail before commands run. Every source/harness glob must match regular files in
the current checkout. Include gate programs, native toolchain declarations,
build manifests and complete dependency resolution files in the harness.

`measure_pair` uses its existing opt-in isolated worktree lifecycle for both
schemas. `common_measurement.measure_prepared_pair(baseline, current, plan,
pair, *, working_tree=False)` exposes the same schema-2 operation for two
distinct, nonoverlapping disposable checkouts supplied by a caller. It changes
files in those checkouts; the caller owns cleanup. It does not create Git refs.
The harness is captured from current, including ordinary permission bits;
obsolete files matched by its patterns are removed and captured bytes are
installed into both sources. Library source patterns are captured independently
before substitution and again afterward. The shared `source_sha256` identifies
the effective measured input; `context.original-source-sha256` and its inventory
identify the source before substitution. Both use the documented shared file
framing, never a legacy digest relabeled as shared.

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
| `RUN_SCHEMA` | `research-repo-tools/complete-run/v1` |
| `CompleteCase(storage_path, benchmark, estimates, sample)` | Portable relative path plus original immutable bytes for Criterion's three JSON files |
| `CompletePolicy(expected, sample_count=100, statistics=("mean", "median"), confidence_level=0.95, unit="ns")` | Exact unique semantic IDs and measurement requirements |
| `CompleteRun(phases, series, compatible=("harness_sha256",))` | Exactly baseline/current complete samples, named views and required provenance selection |
| `CompleteSample(policy, cases)` | Immutable sorted cases validated against the complete policy |
| `RunSeries(name, phase, rows)` | Unique display labels mapped to full IDs in one recorded phase |
| `collect_complete_sample(criterion_dir, sample, policy)` | Reject unsafe trees, incomplete rows, duplicate semantic IDs and missing/unexpected inventory |
| `parse_run(payload)`, `serialize_run(run)` | Versioned strict shared JSON; original Criterion bytes stored as base64 |
| `render_run(evidence, *, title="Benchmark timings")` | Offline named-series tables for every required statistic, with originating phases |
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

## Retention and publication

Schema-2 report TOML requires `schema`, `current`, `archive`, and `title`.
`performance_reports.load_report_plan` dispatches to
`run_reports.load_run_report_plan(root, configuration, *, payload=None,
manifest=None)`. It returns the existing `PublicationPlan` for preview, check
or transactional publication. `performance_reports.render_report` and the CLI
renderer also recognize complete runs. The schema-1 comparison CSV exporter and
document configuration keep their existing contracts; complete-run custom
documents use the Python publication API over validated run data.

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
