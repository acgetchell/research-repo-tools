# Recording and retaining performance evidence

Start after [setup](../README.md#install-with-uv), with the consumer
benchmark toolchain available and independent correctness gates passing.
The consumer declares scientific cases, sample counts and workloads. Shared
tooling verifies the complete inventory and retains the original raw bytes.
A *harness* is the code/configuration that drives measurement; *provenance* is
the recorded source, harness, host and tool identity accompanying those bytes.

For a new complete workflow, define the [measurement and publication policy](DECLARING_PERFORMANCE.md),
then use the three prepared-release stages below. Older comparison workflows
remain supported. [Presentation](PUBLISHING_REPORTS.md) and [migration](performance-migration.md)
have separate homes.

- [Prepared performance releases](#prepared-performance-releases)
- [Common harness and complete runs](#common-harness-and-complete-runs)
- [Criterion inventory and saved baselines](#criterion-inventory-and-saved-baselines)
- [Parallel saved-baseline suites](#parallel-saved-baseline-suites)
- [Performance evidence](#performance-evidence)
- [Configured benchmark workflows](#configured-benchmark-workflows)
- [Host and profiling metadata](#host-and-profiling-metadata)

## Prepared performance releases

Merge the packaged `performance-prepare`, `performance-release`, and
`performance-readme` recipes and declare one `tooling/performance-release.toml`.
Export `performance-release.toml` with `just templates`, set the repository and
measurement/report/publication paths, and ignore the declared scratch directory
in Git. Use matching installed configuration/templates from a verified published
supplying release before replacing a working consumer adapter.

```sh
just performance-prepare tooling/performance-release.toml
just performance-release tooling/performance-release.toml
just performance-readme tooling/performance-release.toml --preview
just performance-readme tooling/performance-release.toml
```

Preparation prints and saves independently selected current/baseline tags and
full commits. The default uses reviewed local tags. An authorized refresh uses
`just performance-prepare tooling/performance-release.toml --fetch-tags --allow-git-mutations`.
The export backend needs no Git-mutation permission; a configured worktree
backend requires `--allow-git-mutations` on the measurement step separately.

Successful measurements survive README errors. Repair the README/view or source
assertions and retry only `performance-readme`. A failed report promotion can be
retried using `just performance promote REPORT --payload PAYLOAD --manifest MANIFEST`
with the declared retained raw paths. README publication never measures, promotes,
fetches tags or changes Git refs. All four independent overrides remain supported:

```sh
just performance-readme tooling/performance-release.toml \
  --current-release v1.2.0 --current-revision FULL_CURRENT_COMMIT \
  --baseline-release v1.1.0 --baseline-revision FULL_BASELINE_COMMIT
```

For an honest first-use report state, add this table to either report schema:

```toml
[initialization]
allow-empty = true
directories = ["scratch/performance", "docs/assets/performance"]
guards = ["tooling/performance-publication.toml"]
```

`just performance promote tooling/performance-report.toml --check` then accepts
only a completely empty declared inventory when the current report is missing.
The archive is included automatically; extra directories must be disjoint and
companion guards must be outside them. Any payload, manifest, CSV, figure,
archived report, pointer, companion plan or partial directory rejects first use.
Initialized reports retain strict evidence/history/render checks. The check
creates nothing. See the [prepared performance API](performance-release-api.md)
for schema, freshness and retry details.

## Common harness and complete runs

For installed measurement, independent eligibility, saved-sample verification and
publication through static consumer declarations, see
[Declaring complete performance workflows](DECLARING_PERFORMANCE.md).
The v0.2.1 additions include `complete-measurement.toml`, runtime saved names,
required storage layouts, reusable publication identities and recorded version
labels. Named configurations preserve consumer scientific selections.

For new independent revision measurements, use the packaged
`complete-measurement.toml` (schema 4). Set `harness-mode = "independent"` and
`backend = "export"`, then declare your own gates, discovery, exhaustive counts,
workload files and raw/saved-file requirements. Each revision keeps its original
harness. Exports require no Git mutation permission; builds needing Git metadata
can explicitly select `backend = "worktree"` with `--allow-git-mutations`.

```sh
just performance measure complete-measurement.toml v1.2.0 v1.1.0 \
  --payload target/run.json --manifest target/evidence.json
just performance verify target/run.json target/evidence.json --measurement complete-measurement.toml
just performance promote complete-report.toml --payload target/run.json --manifest target/evidence.json
just performance promote complete-report.toml
```

The last command uses retained evidence after scratch cleanup. Keep the report
archive outside `target`. Different harnesses, workloads, hosts or tools retain
valid absolute results and the complete union of cases, with incompatible ratios
suppressed. Strict release eligibility is separate. Document publication can
explicitly select `release = false` with a measurement declaration for absolute
results. See the [complete acquisition contract](DECLARING_PERFORMANCE.md#complete-acquisition-contract-schema-4).
Pin a verified published release supplying this contract before retiring consumer
adapters; implementation alone does not make it available on PyPI.

For the supported common-harness measurement contracts, use the packaged `common-benchmark.toml`,
`complete-report.toml`, and `profiling.toml` as scaffolds, editing their inventories
and commands to match the consumer. See the [contracts](complete-run-api.md)
and [adoption map](performance-migration.md#common-harness-and-host-adoption).

Schema-2/3 measurements capture the selected current harness once, including
declared lockfiles and toolchain files, and apply it to both isolated revisions.
Every phase declares a command, an independent preflight gate, its expected
Criterion `full_id` inventory, and optional environment overrides. Both gates
must pass before either phase is timed. The defaults are 100 samples, both mean
and median, and 0.95 confidence; different policies must be explicitly declared.
Schema 3 also supports exhaustive per-case sample counts, such as
10, 15, 20, 25 and 100 in one phase. Edit each phase's `sample-counts` map in the
packaged template to match every full ID, or omit both maps and set a shared
top-level `sample-count` for uniform sampling. Schema 2 retains its shared
uniform count and v1 evidence contract. Direct Python plan/run constructors also
retain those defaults; select `CommonHarnessPlan(..., schema=3)` or
`CompleteRun(..., schema=RUN_SCHEMA_V2)` explicitly for v2. Commands must actually configure Criterion
to emit those samples and intervals. The shared reader verifies the output.

Named report series select rows from a recorded phase. For example, a reference
measured with the baseline is declared with `phase = "baseline"` even when
displayed alongside the current library. Its provenance always names that phase.
All measured cases, original raw Criterion bytes and required statistics remain
in retained evidence, including cases omitted from report tables.

After configuring the templates, run these steps in order:

```sh
just performance measure common-benchmark.toml v1.2.0 v1.1.0 --allow-git-mutations \
  --payload target/complete-run.json --manifest target/complete-evidence.json
just performance promote complete-report.toml \
  --payload target/complete-run.json --manifest target/complete-evidence.json
just performance promote complete-report.toml --check
```

Keep the configured archive outside scratch directories such as `target`.
Promotion writes immutable content-derived run directories, a validated latest
pointer, an index and the current report together. Distinct measurements of the
same release pair get distinct identities; identical retries reuse the existing
run. Same-label local comparisons can be retained. Omitting both input paths
rerenders the validated latest run after scratch cleanup. Explicit missing or
partial inputs fail; they never select a different run as a fallback.

The standard complete report includes recorded per-phase source/harness
fingerprints, host and toolchain observations, dependency versions, measurement
commands and independent gate results. Current and newly retained reports link
to their exact run payload and evidence envelope using paths relative to each
report. Missing optional metadata is explicit; offline rerendering uses the
measurement's recorded environment. Environment overrides show names only, and
credential-shaped arguments are redacted from the summary. Keep credentials out
of declarations because retained evidence preserves original values.

Existing complete-run histories remain readable: promotion upgrades the mutable
index and current report while preserving all immutable historical bytes. Old
retained reports keep their original presentation. See the
[renderer-version contract](complete-run-api.md#retention-and-publication)
before upgrading a publication or restoring an older package pin.

## Criterion inventory and saved baselines

Use the installed inventory and saved-sample interfaces before removing
consumer discovery or baseline-copy helpers. Start with the
packaged `criterion-inventory.toml` and `criterion-saved.toml` templates.
Discovery configuration declares native list commands, suite names, working
directories, environments and bounded timeouts. It can require exact IDs and
exclude explicitly named compatibility cases. IDs stay separate from Criterion's
sanitized filesystem paths. Empty, malformed and duplicate inventories fail.

```toml
schema = 1
required = ["workload/library/2"]
excluded = []

[[suites]]
name = "workload"
command = ["cargo", "bench", "--locked", "--bench", "workload", "--", "--list"]
cwd = "."
timeout = 7200
```

Use the template's `just performance` wrapper to save the discovered inventory:

```sh
just performance discover criterion-inventory.toml --output target/criterion-inventory.json
```

Discovery may compile. Every suite gets a private `CRITERION_HOME`; commands
must honor it. Existing inventory outputs are preserved; select a fresh path
for another discovery. Schema-2 common-harness phases can replace `expected`
with a `discovery` table containing the same suite declarations. The shared
measurement then discovers both inventories after installing the current harness,
validates series coverage, runs both correctness gates, and measures. It retains
the discovered suite inventories with the complete run. See the
[configuration and API reference](criterion-api.md).

For saved release baselines, declare a verification plan:

```toml
schema = 1
criterion-dir = "target/criterion"
fresh-sample = "new"
saved-sample = "v1.2.0"
inventory = "target/criterion-inventory.json"
raw-files = ["benchmark.json", "estimates.json", "sample.json", "tukey.json"]
sample-count = 100
statistics = ["mean", "median"]
confidence-level = 0.95
unit = "ns"
```

Run inventory discovery first, then the consumer's correctness gates and native
measurement with its saved-baseline option. Next validate the saved copy:

```sh
just performance verify-saved criterion-saved.toml
```

Package and publish only after validation succeeds. Criterion can report a copy
error while returning success: verification requires complete fresh and saved
inventories and exact binary equality for every configured raw JSON file.
`tukey.json` is required by the scaffold above; configure additional JSON files
when the consumer requires them. Rejection leaves evidence unchanged.
Required cases, exclusions, suite/phase assignments, statistical settings and
release eligibility remain consumer declarations. The
[consumer deletion map](performance-migration.md#criterion-discovery-and-saved-baseline-adoption)
identifies the generic helpers these contracts replace.

## Parallel saved-baseline suites

The suite transport commands target v0.2.4. Publish the supplying package before
updating a consumer's exact dependency pin. Consumers keep native discovery,
correctness gates, benchmark commands, job budgets and release upload policy.

Each parallel job uses a fresh Criterion output directory and the same complete
inventory and saved-sample configuration. Capture host metadata on that runner,
record the native measurement's start/end times, then package its included suite:

```sh
just performance host --output target/host.json
# Run the consumer's correctness gate and full native measurement here.
# REVISION is the measured full commit, SESSION identifies this collection attempt,
# and STARTED/FINISHED are the measurement's integer UTC Unix timestamps.
just performance verify-saved criterion-saved.toml --suite public-default --saved-sample v1.2.0
just performance pack-suite criterion-saved.toml public-default \
  --saved-sample v1.2.0 --output target/public-default.tar.gz \
  --revision "$REVISION" --session "$SESSION" --host target/host.json \
  --started-at "$STARTED" --finished-at "$FINISHED" \
  --context 'command=just bench-save-baseline v1.2.0 public-default'
```

Use the matching suite name, command context and archive path in each job.
`--criterion-dir` overrides the configured output directory when needed. Pack
already performs suite verification; the separate verify command is useful as
a diagnostic. Each archive retains the full inventory/policy, exact fresh and
saved JSON, their digests, measurement interval and original runner metadata.

After all jobs succeed, download the exact artifacts for that collection attempt.
Use the independently expected configuration, full inventory, revision and
session to create the combined release archive:

```sh
just performance assemble-suites criterion-saved.toml \
  target/public-default.tar.gz target/public-exact.tar.gz \
  --saved-sample v1.2.0 --output target/criterion-v1.2.0.tar.gz \
  --revision "$REVISION" --session "$SESSION"
```

Assembly requires exactly one archive for each suite with included cases. It
checks the full union, sampling requirements, raw digests, source/session, and
portable paths before publishing any output. Existing outputs are preserved;
choose a new path for a retry. The archive extracts directly to a Criterion
root and retains original suite manifests under `release-suites/`, alongside
`criterion-inventory.json`. The assembly host is never substituted for a runner.
Different runner hosts support archived absolute timings; comparative claims
still require the consumer's compatibility policy. See the
[suite API contract](criterion-api.md#parallel-suite-transport).

## Performance evidence

The [adoption matrix](performance-migration.md#release-capability-adoption)
lists minimum versions, extras and compatibility boundaries for older consumers.

Merge the packaged `performance` recipe into the consumer justfile, then compare
existing Criterion trees without executing benchmarks:

```sh
just performance compare target/baseline/criterion target/current/criterion --output target/comparison.json
```

Both samples default to `new`, the statistic to `median`, and the unit to `ns`.
Use `--baseline-sample NAME`, `--current-sample NAME`, `--statistic mean`, or
`--unit` when the measurement harness requires them. The JSON retains both full
samples, including added and missing benchmarks. The CLI rejects comparisons
with no common benchmarks. It does not infer statistical significance.
Place `--output` outside both Criterion input roots, including path aliases.

Use Python to attach provenance captured by the consumer at measurement time:

```python
from pathlib import Path

from research_repo_tools.criterion import COMPARISON_SCHEMA, parse_comparison, render_comparison
from research_repo_tools.evidence import Evidence, Provenance, compare_provenance, publish_evidence

def retain_comparison(payload: bytes, baseline: Provenance, current: Provenance) -> None:
    comparison = parse_comparison(payload)
    compatibility = compare_provenance(
        baseline, current, fields=("harness_sha256", "context.cpu", "context.rustc"),
    )
    if not compatibility.compatible:
        raise ValueError(f"Measurement context differs or is unknown: {compatibility.differences}")
    retained = Evidence(payload, COMPARISON_SCHEMA, (("baseline", baseline), ("current", current)))
    publish_evidence(
        retained, Path("evidence/comparison.json"), Path("evidence/manifest.json"),
        reports={Path("PERFORMANCE.md"): render_comparison(comparison).encode("utf-8")},
        immutable=(Path("evidence/comparison.json"), Path("evidence/manifest.json")),
    )
```

Choose compatibility fields and scientific acceptance rules in the consumer.
Provenance records the full source commit, optional exact source and harness
digests, and explicit context. Unknown values fail a requested compatibility
check. Render or verify retained evidence independently of measurement:

```sh
just performance verify evidence/comparison.json evidence/manifest.json
just performance render evidence/comparison.json evidence/manifest.json --output PERFORMANCE.md
```

`verify` checks envelope structure and the exact payload digest; consumers still
validate their own payload schemas. `render` additionally parses the shared
comparison schema. Domain reports can keep their existing renderer and use the
shared comparison results and publication transaction.

For published assets, use `just performance fetch HTTPS_URL ASSET --sha256 DIGEST`
with an independently recorded digest, then
`just performance extract ASSET ABSENT_DIRECTORY --sha256 DIGEST`.
The extraction parent must exist. See the [performance API](performance-api.md)
for limits and [retained-evidence migration](performance-migration.md) before
replacing consumer helpers or changing artifact formats.

## Configured benchmark workflows

Configured workflows around the retained-evidence APIs are available from v0.1.4.
Pin an exact published version containing the capabilities your consumer uses.
Start with the packaged `benchmark.toml`, `examples.toml`, and
`performance-report.toml` templates, selecting your actual workloads and paths.
For example, a measurement configuration contains:

```toml
schema = 1
command = ["cargo", "bench", "--locked", "--bench", "timings"]
sources = ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "src/**/*.rs", "benches/timings.rs"]
harness = ["benches/timings.rs"]
sample = "new"
statistic = "median"
unit = "ns"
compatible = ["context.os", "context.architecture", "context.cpu"]

[probes]
rustc = ["rustc", "--version", "--verbose"]

[dependencies]
criterion = "Cargo.lock"
```

Use the template's `just performance` wrapper. These are alternative operations:

```sh
just performance compare target/criterion target/criterion --baseline-sample last --format markdown
just performance measure benchmark.toml --mode current-vs-latest --allow-git-mutations --payload target/local.json --manifest target/local.evidence.json
just performance measure benchmark.toml v1.2.0 v1.1.0 --allow-git-mutations --payload target/release.json --manifest target/release.evidence.json
just performance assets --repository owner/project --asset-template 'project-{tag}-baseline.tar.gz' \
  --payload target/releases.json --manifest target/releases.evidence.json
```

Measurement explicitly permits temporary Git worktrees and runs trusted consumer
code. It needs existing local tags and a verified benchmark toolchain. Use a
consumer recipe with `toolchain run -- research-repo-tools performance measure`
when tools live under managed paths. Omitting tags infers the package release:
prospective releases use working files, and published versions use their tagged
source and predecessor. Publication chronology is the default; `--order version`
selects numeric order. Asset comparison performs no measurement. Add
`--legacy-configuration legacy-baseline.toml` only for declared historical layouts.

A report configuration contains:

```toml
schema = 1
current = "docs/PERFORMANCE.md"
archive = "docs/archive/performance"
title = "Benchmark timings"
prose-file = "docs/performance-interpretation.md"
```

After measurement, promote, rerender offline, then check or publish the configured
README section:

```sh
just performance promote performance-report.toml --payload target/release.json --manifest target/release.evidence.json
just performance promote performance-report.toml --check
just performance publish performance-publication.toml --check
just performance publish performance-publication.toml
```

Promotion retains full JSON evidence and CSV, archives the prior report, rebases
evidence links, and refreshes the archive index. `--preview` shows planned diffs.
Future-release README preparation requires explicit `tag-policy="prepare"`,
`repository`, both current inventories and matching working-tree fingerprints.
Any existing tag must still contain exact referenced and generated bytes.
Historical conversion writes new paths and preserves original files and hashes;
see [migration](performance-migration.md) and the
[configuration/API contracts](workflow-api.md).

## Host and profiling metadata

These commands write explicit UTF-8 JSON outputs under the consumer root:

```sh
just performance host --output target/host.json
just performance profile profiling.toml --output profiling-results/environment.json
```

Host metadata includes available CPU model, physical cores, logical threads,
total memory in bytes, OS and architecture. Profiling adds bounded configured
version probes, explicit context and structured native Cargo/Rust TOML
declarations. Unavailable observations are `null`; declarations are distinct
from measured tool versions and resolved build settings. The default profiling
probes capture Cargo and verbose rustc output in the selected root. Use the
same managed-tool environment as the benchmark. Source provenance callers use
`measurement.capture_provenance`, which includes this host record alongside
source/harness fingerprints and tool/dependency identity. To include source
identity directly in a profiling artifact, set `measurement = "benchmark.toml"`
and the explicit `release = "v1.2.0"` at the top level of its configuration.

Keep compatibility thresholds, warning text, historical baseline parsing and
profiling-mode selection in the consumer. Read retained metadata directly when
interpreting historical evidence; do not attach observations from today's host.
