# Declaring complete performance workflows

## Availability, progress and resume (schema 5)

These interfaces are intended for v0.2.5. Implementation, merge, PyPI publication
and consumer adoption are separate states. Verify the supplying exact-version
registry installation before changing a consumer pin.

The `suite-measurement.toml` template declares `schema = 5` and ordered
`[[suites]]` tables. Each has a unique lowercase slug `name`, a consumer-owned
semantic `contract`, and an inline `measurement` following schema 4. Both phase
commands, discovery policies, gates, complete sampling requirements, supplemental
raw files and series retain their existing rules. All suites share a harness
mode, statistics, confidence and units. Declare every scientific input, feature,
flag, fixture/seed, tool and dependency used by the native commands. Shared build
manifests belong in the harness even when this conservatively suppresses ratios.

Optional `since = "v1.1.0"` is inclusive; `before = "v2.0.0"` is exclusive.
Bounds are canonical stable tags; no bounds means available on both revisions.
Resolution uses independently captured tag/revision/source receipts before any
harness overlay or native command. Prospective working-tree labels must match
the original captured package version. Compilation, discovery, missing required
IDs, failed gates and incomplete samples remain errors for available suites.
Advancing the baseline automatically enables newly supported suites. Consumers
must split Cargo targets/features so an available command never indirectly
compiles an unavailable API. Runtime case filtering cannot supply this boundary.

Optional `compilation = { baseline = [...], current = [...] }` commands run after
source preparation and before discovery, only on available sides. Without them,
native discovery may include compilation and is reported as discovery. Optional
positive `nominal-seconds-per-case` declares expected warmup plus sampling time.
Its product with discovered case counts is a budget, never an upper bound or
timeout. One unknown suite budget makes the overall nominal budget unknown.

Schema 5 requires `backend = "export"` in each nested measurement. Both common
and independent harnesses are supported. Worktrees are rejected before execution;
schemas 1–4 preserve their existing behavior and authorization boundary. Exports
provide stored source bytes without Git metadata or checkout filters. Native
commands requiring those facilities cannot use schema 5 yet.

Complete-run v4 records every suite decision and available side. Entirely absent
phases have no fabricated sample. Reports expose the union, absence reasons,
original source identities, sessions and collection times. Series names become
`SUITE/SERIES`, such as `core/current`. Renamed IDs remain distinct. Represent
changed semantics with separate suites and nonoverlapping release intervals;
no implicit identity mapping is inferred. Duplicate ownership within a phase
is rejected, including duplicate native IDs across available suites.

Ratios compare individual observations in the same accepted unit. They require
matching workload identities, applicable harness bytes/modes, inputs, environment,
tools/dependencies, sample counts, statistics, confidence, units and known host
observations. An unrelated one-sided suite does not suppress shared ratios.
Cross-phase measurement and optional compilation argument vectors must also
match exactly. Differing phase names, paths, seeds, sizes, features or flags
retain absolute results; no command-equivalence mapping is inferred.
Cross-unit ratios, including same-phase peers, remain unavailable. Keep compatible
peer and library cases in one suite. Release promotion validates shared comparisons
while retaining declared one-sided results; disjoint runs remain absolute-only.
Schemas 1–4 retain their original comparison policies.
Derived ratios must remain finite and positive; report rendering rejects
arithmetic overflow or underflow without rewriting retained absolute samples.

### Checkpoint and recovery

`performance measure` and `performance release` accept `--checkpoint PATH` for
a new checkpoint and `--checkpoint PATH --resume` for explicit reuse. CLI paths
are root-relative, have an existing parent, do not overlap inputs/outputs, and
must be ignored during working-tree capture. Python callers pass a `Path` to
`measurement.measure_pair`. There is no automatic fresh/resume fallback.

The acceptance unit is a whole suite: both available phases, or its one available
side. Gates pass before timing. Completed units are committed atomically; an
incomplete pair is recollected in full. Resume repeats preparation, discovery and
gates, validates every retained unit before missing work, and preserves original
raw bytes, host, session and collection times. Final evidence attributes retained
and new units. Validated phase progress never makes a half-pair resumable.

The header binds the declaration, release/source receipts, availability and a
digest of the inherited environment. Each accepted unit binds effective source,
harness/input identities, resolved discovery/sampling, tools/dependencies, host,
gate status and exact raw files. Current independent observations must match
before reuse. Source, declaration, environment, tool, host or raw-data drift
rejects resume with a fresh-checkpoint path as the recovery option. The inherited
environment is represented only by its digest; explicitly configured commands
and environment values remain in the retained declaration. Digests establish integrity, not external
authenticity; these local records assume trusted, exclusive ownership.

An exclusive `.writer` directory rejects concurrent writers. A hard-killed owner
can leave it behind. Verify that all owners/native processes have stopped before
manually removing only the stale lock; never steal a lock based on PID or age.
Each immutable `unit-NAME.json` contains its content digest and complete base64
raw observations. Known unpublished temporary files are not accepted. A truncated
or corrupt final unit is an error, not missing work. File data is fsynced before
atomic replacement; directory-entry persistence after power loss depends on the
native filesystem. Rejected resume preserves accepted units and published outputs.

### Progress contract

`--progress human` is the default; `json` emits JSON Lines; `none` disables status.
Status uses stderr without changing native stdout/data output. The finite
`--progress-cadence SECONDS` defaults to 10 and must be at least 1. Transitions
are immediate; ordinary heartbeat work is bounded by the suite inventory, without
scanning directories, parsing native logs or triggering measurements.
JSON status shares stderr with inherited native diagnostics; filter records by
their schema. Use the Python observer or a dedicated stream for an isolated
machine-readable channel.

Events use schema `research-repo-tools/acquisition-progress/v1` with fields
`event`, `state`, `suite`, `phase`, `completed`, `remaining`, `total`,
`elapsed_seconds`, `nominal_seconds`, `eta_seconds`, `compatible_eta_seconds`,
`unknown_cases`, `eta_reason`, `unseen_work_classes`, and `inventory`. Each schema-5
inventory row names its suite/phase and total, completed and remaining case counts;
unavailable sides have zero cases. Counts/budgets can be
null before discovery. States distinguish availability, optional compilation,
discovery, gate, acquisition, checkpoint, retention and publication. Events are
started, heartbeat, finished, budget, validated, retained, acquisition-complete,
failed and interrupted. Counts advance after complete phase results pass raw-file,
sampling and freshness validation. Partial files/directories never count; final
validation and durable unit acceptance are independent safeguards.

Rates come from observed compatible contracts, harness/input identities,
commands, compilation flags, tools, host, environment and sampling policies.
Retained units do not train rates. Unseen
work keeps overall ETA null; `compatible_eta_seconds` estimates only the known
remainder alongside `unknown_cases`. Stalls and extended native sampling can
exceed estimates. Compilation, gates and publication are not extrapolated from
sampling. Failure/interruption preserves phase and remaining counts in its event.
A new attempt revalidates the checkpoint and starts fresh progress counts.
Schemas 2–4 also advance completion after each validated phase. Their separate
phase work classes keep cross-phase ETA unknown without a suite-level contract.

### Bounded la-stack migration

Keep scientific operations, benchmark inputs, API introduction versions, peer
selection and gates in la-stack. Split shared and newly introduced APIs into
independently compilable suites, then replace permanent baseline exclusion lists
with release intervals. Keep peers with their library suite when ratios are needed.
Verify the supplying PyPI version before adoption. Upstream publication does not
wait for the consumer release. Preparation, acquisition/promotion and README
retry remain separate; existing running acquisitions are not migrated.

The v0.2.1 interfaces connect installed commands to consumer TOML and native
Just/Cargo commands. Consumers own benchmark definitions, scientific gates,
dimensions, report selections, peer mappings, sampling and compatibility policy.
The package owns discovery, validation, isolated execution, retention and
transactional publication. See [release history](../CHANGELOG.md) for published
versions; downstream adoption also requires the release and native checks.

## Measurement and eligibility

Export `complete-measurement.toml` with the installed template command. Maintain
named configurations such as `measurement-full.toml` and
`measurement-non-exact.toml`. Declare each suite's native list command in
`phases.PHASE.discovery.suites`, with an independent `required` list. Complete
inventories are discovered on both prepared sources before either gate or timer
runs. Extra diagnostic cases remain in the sample and must satisfy its policy,
even when absent from the report. Explicitly excluded IDs must be omitted by the
consumer's timing command; required IDs cannot also be excluded.

Existing schemas 2/3 already express exact rows, separate phase commands and
environments, required cases, exclusions, full versus non-exact suites, and
baseline-phase peers. Explicit declarations suffice for ordinary report views.
Only an open-ended all-benchmarks view needs the new bounded selector:

```toml
[[series]]
name = "All library current"
phase = "current"
select = { include = ["d*/library_*"], exclude = ["*/diagnostic_*"] }
```

A series has exactly one of `rows` or `select`. Include/exclude use case-sensitive
shell globs over the entire semantic ID; `/` has no special meaning. They do not
change discovery, sampling or timing commands. Selected IDs become their own row
keys. Empty selections fail before gates. Explicit mapped IDs missing from their
phase also fail. Peer mappings remain explicit consumer `rows`, with the original
`phase = "baseline"` even when the library series uses current evidence.

`common_measurement.DiscoveredSeries(name, phase, include, exclude=())` is the
public selector type; `resolve(expected)` returns a `RunSeries`.
`CommonHarnessPlan.series` accepts both types.

`common_measurement.validate_run_policy(evidence, plan, *, release=False)` takes
the independently loaded common measurement declaration and returns a validated
`CompleteRun`. It checks:

- Complete sampling, statistic, confidence, unit and exact required ID coverage.
- Selected series and their originating phases.
- Exact compatibility fields, declared context, commands, gates and environments.
- Retained discovery suite names, exclusions and independently required IDs.
- Recorded acquisition settings: source/harness patterns, probes, dependency
  declarations, output/sample selection and discovery commands.

New measurements bind acquisition settings into the reserved
`context.measurement-declaration` in each phase's evidence envelope. Recorded
discovery inventories are in the same source provenance, and their selected IDs
must equal the complete payload policy. The declaration is integrity/provenance
evidence, not a signature authenticating arbitrary input supplied by another
party. Independent policy comes from the consumer configuration.

The CLI accepts `performance verify PAYLOAD MANIFEST --measurement CONFIG`.
Schema-2 report configuration accepts `measurement = "..."` and `release = true`;
this performs the same check before promotion and offline rerender. `release`
defaults to false for compatibility and requires `measurement` when true.
Existing history remains readable offline without recapturing the host. Runs
predating acquisition-declaration recording remain renderable using existing
interfaces, but cannot pass the new strict eligibility gate. Preserve their bytes
and links; do not backfill declarations that were never recorded.

## Source selection

Measurement schemas 1, 2 and 3 accept an optional stable release floor:

```toml
minimum-release = "v1.0.0"
```

Both selected labels must meet the numeric major/minor/patch boundary. The
package rejects an older current or baseline label before resolving checkouts,
creating worktrees, discovering benchmarks, running gates or collecting samples.
The boundary is inclusive; an omitted floor preserves unrestricted stable-label
selection. The Python API exposes the keyword-only
`MeasurementConfig.minimum_release` and `validate_release(tag)` with the same
contract. This is a new-measurement policy: it does not change retained evidence
or bypass its independent eligibility checks. Install a published release
containing this setting before adding it to a consumer configuration.

`measure --mode current-vs-latest` compares the current working tree with a
published baseline, including when their release labels match. Equal-label runs
require different effective source fingerprints and cannot become release
documentation or pass release promotion. `explicit` requires distinct tags.

The common snapshot contract includes tracked changes and all nonignored
untracked regular files. It captures exact bytes, a binary HEAD patch and
ordinary permission bits, then rechecks that same set after measurement.
Changes, additions or removals reject the result before evidence publication.
Keep scratch outputs ignored. Ignored files are not snapshot inputs; explicitly
include every performance-relevant source and harness file in the declarations.
Symlinks, junctions and submodules are rejected by the existing source boundary.

Temporary Git worktrees require `--allow-git-mutations`. Tags must already exist
locally: fetch them explicitly in the consumer's preparation step. Output
directories start absent, discovery uses private Criterion homes, and both gates
must finish without timing output. Cleanup failures identify retained recovery
checkouts; the engine does not erase them. Commands are trusted argument vectors,
run without a shell by the package, and may invoke consumer Just recipes.

## Saved sample verification

`performance verify-saved CONFIG --saved-sample TAG --criterion-dir PATH` selects
the actual saved name and root-relative Criterion home. Both overrides are
optional when configured. The saved name must be a safe single ASCII component;
reserved names such as `new`, `base`, `change`, and `report` are rejected
case-insensitively. `load_saved_sample(root, configuration, *, saved_sample=None,
criterion_dir=None)` applies the same validation.

Schema 1/2 saved configuration accepts:

```toml
schema = 2
inventory = "target/inventory.json"
required = ["d2/library_det", "flat semantic ID"]
sample-count = 100
statistics = ["mean", "median"]
confidence-level = 0.95
unit = "ns"
raw-files = ["benchmark.json", "estimates.json", "sample.json", "tukey.json"]

[storage-paths]
"d2/library_det" = "d2/library_det"
"flat semantic ID" = "sanitized_id"
```

Exactly one of `expected` or `inventory` is required. `required` is an independent,
unique subset; a shortened retained discovery fails. `storage-paths` maps any
subset of expected semantic IDs to exact portable relative storage paths.
Duplicate/case/Unicode path aliases fail. Undeclared paths retain Criterion's
ordinary sanitized-layout support. Both fresh and saved samples must satisfy
each declared path, even when they agree with each other at the wrong location.

`SavedSamplePlan` and `validate_saved_sample` expose keyword-only `storage_paths`
as immutable `(ID, path)` pairs. Complete samples and configured raw JSON files
are validated and compared byte for byte, including line endings. Verification
does not repair, move or delete anything.

## Reusable publication

Schema-2 publication accepts `measurement = "..."`. Document publication then
requires retained `archive` selection, validates release eligibility, and uses
the measurement's source/harness patterns when explicit current inventories are
absent. Membership and hashes must match; all selected bytes, configurations and
the archive selection are rechecked before the multi-file commit.

Leave `release` and `revision` out of the two `provenance` tables to reuse a
configuration across releases. Supply `--baseline-release`, `--baseline-revision`,
`--current-release`, and `--current-revision` from independent workflow inputs.
Each release/revision pair is required together. Supplied values must agree with
static assertions when present, and must match the retained evidence. Never
extract these assertions from the evidence being checked.

The public API is `load_publication(root, configuration, *, identities=None,
artifacts_only=False)`. `identities` maps `baseline` and/or `current` to
`(release, full_commit_revision)` tuples. Existing calls and static provenance
tables keep their behavior.

A series can append its recorded version using `label-version = "release"` or
`label-version = "dependency.NAME"`. The original series phase determines the
source: current library labels come from current evidence; reused peer labels
come from baseline evidence, never today's lockfile. Missing recorded values fail.

Keep named publication configurations for metric, mean/median and linear/log
views. `--artifacts-only` writes only configured SVG/CSV outputs and does not
require a document or marker/reference fields. Identity and optional measurement
checks still apply; source freshness checks only apply when explicitly configured
in this mode. It supports offline rendering after scratch cleanup. At least one
artifact is required. Explicit payload/manifest selection always means those
files; missing, partial or corrupt scratch never falls back to history.

`formatter = ".rumdl.toml"` formats and checks the candidate document through the
existing rumdl facility before any output is replaced. The formatter configuration
is a snapshotted input, document markers must survive, and references are checked
on formatted candidates. `publication.plan_publication(..., formatter=None)`
offers the same operation; a supplied root-relative formatter configuration must
be included in `inputs`. Configured formatting requires UTF-8 Markdown. Without
formatting, existing byte preservation outside the replaced section is unchanged.
Formatter errors, stale inputs and validation failures leave all outputs intact.
Caught replacement failures use the existing rollback/recovery contract.

The shared complete-run link and envelope replace a redundant new plot-provenance
sidecar: retained evidence identifies the run, phase, statistic, unit, source and
harness, while SVG/CSV preserve originating series and run identity. Historical
files do not need normalization or regeneration.

## Consumer migration

Install uv and the package's user-level Just setup as described in the
[task guide](../README.md#install-with-uv). A consumer can add the following recipes
to its existing Just module. The packaged `performance-prepare`,
`performance-release`, and `performance-readme` recipes share one declarative
workflow and independent retry boundaries; see the
[task guide](RECORDING_PERFORMANCE.md#prepared-performance-releases) and
[API contract](performance-release-api.md). Tag fetching requires explicit
`--fetch-tags --allow-git-mutations`; preparation otherwise uses reviewed local
refs. Lower-level acquisition, promotion and publication remain composable:

```just
[positional-arguments]
performance-measure current baseline:
    uv run --locked research-repo-tools performance measure tooling/measurement-full.toml "$1" "$2" --allow-git-mutations \
        --payload target/run.json --manifest target/evidence.json

performance-promote:
    uv run --locked research-repo-tools performance promote tooling/report.toml --payload target/run.json --manifest target/evidence.json

[positional-arguments]
performance-publish current current_commit baseline baseline_commit:
    uv run --locked research-repo-tools performance publish tooling/publication.toml \
        --current-release "$1" --current-revision "$2" --baseline-release "$3" --baseline-revision "$4"

[positional-arguments]
performance-render current current_commit baseline baseline_commit:
    uv run --locked research-repo-tools performance publish tooling/publication.toml --artifacts-only \
        --current-release "$1" --current-revision "$2" --baseline-release "$3" --baseline-revision "$4"

performance-retained:
    uv run --locked research-repo-tools performance promote tooling/report.toml

[positional-arguments]
performance-saved tag criterion_home:
    uv run --locked research-repo-tools performance verify-saved tooling/saved.toml --saved-sample "$1" --criterion-dir "$2"
```

Run preparation, measurement, promotion, then publication in that order.
`just performance-render ...` and `just performance-retained` work after scratch
cleanup. Native release recipes run discovery and their saved-name benchmark
command before `just performance-saved TAG PATH`.

For la-stack, retire execution compatibility before v0.5.0 and the metadata-free
historical-asset recipe downstream. Declare its full/non-exact native commands,
scientific sample values, required IDs, explicit peer rows, and named plot
configurations there. After v0.2.1 is published and its exact unyanked wheel/sdist
are verified, pin that release and remove the two production orchestration
modules. Retain scientific Rust gates and focused tests of actual recipe/config
wiring. Publication and downstream cleanup are separate handoff boundaries.

## Complete acquisition contract (schema 4)

New workflows use measurement schema 4 and `complete-run/v3` evidence. Declare
`harness-mode = "independent"` or `"common"` and `backend = "export"` or
`"worktree"` explicitly. Independent mode preserves both original harnesses,
including baseline-only files. Common mode still installs the captured current
harness into both sources and requires its declared tool/dependency identities
to agree before timing. These are experimental choices, not compatibility flags.

The `CompleteMeasurementPlan` Python constructor requires `harness_mode` and
`backend` keyword arguments. `load_measurement` reads the same contract from
TOML; `measure_pair` owns acquisition, cleanup, and retained evidence. Prepared
export callers supply both receipts from `source_exports.capture_source` and
materialize them through `source_exports.export_source` before invoking
`measure_prepared_pair(..., captured_sources=...)`. Arbitrary revision labels
cannot substitute for capture receipts. Neither API authenticates evidence
received from an untrusted party; independently supplied declarations and source
assertions remain necessary.

Each phase independently discovers its inventory, runs its gate, and measures
its declared counts. Schema 4 also accepts a phase-local `sample-count`, or an
exhaustive `sample-counts` map. Commands, environments, discovery, complete
policies, series assignments, source/harness/workload patterns, probes, backend,
mode and saved-file policy are bound into the acquisition declaration. Both gates
finish before timing. Inputs and observations are rechecked around execution;
changes invalidate acquisition. Commands are trusted shell-free argument vectors,
not a sandbox: they must not mutate the original repository themselves.

Core benchmark, estimate and sample JSON bytes are always retained. `raw-files`
lists additional JSON filenames and defaults to `["tukey.json"]`; missing or
malformed files fail. `saved-sample = "retained"` requires the native command to
produce that saved copy as well as the fresh `sample` (normally `new`). Both
inventories, all counts, statistics and confidence intervals must validate, and
all configured raw files must match byte for byte. The retained sample preserves
those exact bytes and a recorded successful saved-verification name. Raw outputs
are rechecked before acquisition completes. Nothing is padded or repaired.

Schema 4 requires a nonempty `workloads` list of file patterns for benchmark
fixtures/input definitions; each pattern must match files in each measured source.
Their actual bytes are captured separately from changing library sources.
Independent acquisition does not require equal identities across phases.
`ratio_incompatibilities(evidence)` derives diagnostic reasons from recorded
harness, host, tools/dependencies, workload bytes, environment and complete
sampling policies. Unknown required host/workload identity is ineligible even
when both values are unknown. Different or disjoint inventories remain valid
absolute evidence; the complete report exposes their union. The conservative
cross-phase ratio policy suppresses ratios when complete policies differ.
Tables retain absolute cells with unavailable ratio cells; an explicitly requested
ineligible ratio-only figure fails clearly. Coordinate plots and timing CSV
remain available. Same-phase series comparisons retain their original phase.

`validate_run_policy` checks acquisition against an independent declaration;
`release=True` additionally requires ratio eligibility and distinct release
labels. Schema-2 report configuration already exposes this `release` setting.
Schema-2 document publication now accepts `release = false` with `measurement`
to publish policy-checked absolute results from retained evidence. Its default
remains strict for document publication. Explicit source/release assertions,
freshness checks, immutable retention and transactional writes still apply.

### Export semantics and limits

`capture_source(repository, reference, working_tree=False)` resolves an existing
canonical stable tag, full commit ID, or HEAD. It reads stored Git tree/blob data,
verifies object hashes, and captures ordinary regular files only. Export-ignore,
export-subst, clean/smudge filters and checkout newline conversion are deliberately
not applied. Working-tree capture uses HEAD plus the actual tracked/nonignored
inventory, including edits, deletions and new files. The recorded dirty flag
compares captured bytes and supported executable modes with the stored commit;
checkout filters or newline conversion can therefore make it differ from Git's
filtered status view. Ignored files are excluded;
keep scratch/build outputs ignored. No index, refs, configuration or worktree
registry is changed. A missing ref or unsupported source fails without fallback.

Private export destinations start absent. Portable paths, parent/file aliases,
links, junctions, special files and submodules are rejected. POSIX regular
permission bits are copied exactly; committed files use Git's 0644/0755 modes.
Windows verifies bytes and its supported writable/read-only mode; it cannot
represent POSIX executable permissions. Original logical modes remain in the
receipt. Use the explicitly authorized worktree backend when builds require Git
metadata or checkout filtering; the
export backend never manufactures history or silently creates a worktree.

Capture repeats identity/inventory/byte/mode observations to detect ordinary
races, then independently verifies materialized files. Source receipts can recheck
the original repository during acquisition. The CLI also snapshots declared
controller source, harness and workload inventories, bytes and modes, the
declaration and controller host/tools. It rechecks these inputs and source refs
after output staging before publication; unrelated output files are not inputs.
Keep exclusive writer control: these checks are not atomic
filesystem snapshots or protection against adversarial change-and-restore.
Cleanup failures report retained recovery directories and prevent accepted
publication. Retained evidence remains usable after successful source cleanup.

Historical files remain untouched. There is no migration that relabels earlier
evidence as v3, fills missing raw files, or retroactively captures host/source
proofs. Older readers currently available are not a constraint on this new
contract. Use the producing release to inspect unsupported historical formats.
