# Reusing benchmark acquisitions

This procedure is the v0.2.6 contract for consumer release preparation. Consumers
own workloads, scientific acceptance, output declarations, and thin recipe/workflow
wiring. The shared package owns acquisition, trial receipts, validation, archive
conversion, and evidence mechanics. See the [API and trust contract](candidate-reuse-api.md).

## Step 1: prepare and acquire

Before acquisition, set the prospective package version and finish source,
dependency, build, harness, fixture, command/feature, and tool changes. Declare
measurement schema 6, the exact report/document output exceptions, and a schedule:

```toml
schema = 6
outputs = ["README.md", "docs/performance.md"]

[schedule]
order = "alternating"
trials = 4
```

Keep the existing `[[suites]]` declarations from the suite-measurement template.
For reproducible randomized order use `order = "randomized"`, an even `trials`
count, and an explicit unsigned 64-bit `seed`. `sequential` accepts one or more
trials and has no seed; it does not claim counterbalancing. These controls belong
to the measurement declaration read by `performance prepare` and `performance
release`. `PerformanceRelease.order = "published"` continues to choose release
history; it does not choose acquisition order. Editing the declaration after
preparation invalidates the saved preparation.

Using the consumer's configured recipes:

```sh
just performance-prepare performance-release.toml
just performance-release performance-release.toml --checkpoint target/release-trials
just performance-readme performance-release.toml
```

This is one acquisition session containing the declared independently executed
trials. Each trial uses fresh exports/output trees. For each suite, the declared
phase order is executed before advancing to the next suite. Alternating order
starts baseline/current, then current/baseline. Randomization ranks zero-based
trial indices by SHA-256 of the ASCII string `rrt-schedule-v1:SEED:INDEX`, with
index as the tie-breaker; the first half run current/baseline. The declaration
retains the resulting entire schedule. No consumer chooses a fresh seed later.

The acquisition owner freezes the independent Step 1 assertion digest and original
payload/envelope through the trusted handoff before report edits or final merge.
Use `CandidatePolicy` and `acquisition_assertion_digest` under that owner's
control. Keep original raw evidence in ignored scratch or immutable artifact
storage; report paths declared as exceptions may be committed in the release PR.
After report/README generation, review and merge the release-preparation PR.

Complete-run v5 retains original v4 payload/envelope bytes per trial, unique trial
and suite sessions, observed phase start/end timestamps, and exact execution
order. Reports show each trial separately. A table/CSV/plot consumer must explicitly
select a trial with `select_trial`; schema-2 README configuration requires
`trial = 1` (or another declared trial). The README labels that selection. Keep
the complete v5 evidence beside derived views; selecting a trial does not make
its v4 payload standalone proof of counterbalancing. Pooling within-phase
Criterion samples does not create independent trials or significance evidence.

## Step 2: freeze final release evidence

On the final clean main commit, independently supply the final commit in the
candidate policy. The separately retained Step 1 digest must remain unchanged.
The consumer's `just release-evidence` invokes the shared `release evidence
candidate` command, supplying its native evidence configuration, clean checkout,
original payload/envelope, independent candidate policy, trusted
`--acquisition-sha256`, and a new `--output` acceptance file. This may run before
tag creation; it does not require a GitHub draft or perform measurement/preflight.

Only after Step 2 succeeds, create/push the release tag and follow the consumer's
normal native/package and draft-release procedure. A final source, dependency,
harness, fixture, plan, command, feature, or tool change requires another Step 1
acquisition. A matching version or subset of Rust files is insufficient. The
original candidate revision, dirty status, tools, host, session, and collection
times remain unchanged; the acceptance binds them to the final commit separately.

## Packaging, approval, and retries

Derive the archive with `plan_candidate_archive` and publish its immutable plan.
The derived bytes must match the archive digest frozen in Step 2. Rendering and
packaging never run acquisition or scientific gates. A conflicting existing output
fails instead of replacing history. Fix presentation/configuration outside the
measured-input contract and retry the read-only operation; retained raw evidence
does not need reacquisition. Calling `performance release` deliberately measures
again and is not a rendering retry.

At approval, rerun the candidate verifier with the original acceptance supplied
as `--acceptance`. Reverify the current native selection, whole-tree binding and
original acquisition before acquiring publication credentials. Verify the private
archive copy against the frozen acceptance and captured draft target, then pass
that same path to the existing asset uploader. Upload retries keep the exact
accepted bytes and target. Draft state, remote tag, protected environments, token
separation, and downloaded-asset byte checks remain required.

If a selected artifact expires, is replaced, or a native run/attempt changes,
approval fails. Explicitly review a new selection into a new acceptance file;
never replace the approved file silently. If only publication failed, retry from
the original accepted evidence without acquisition or preflight benchmarks.

## Interrupted acquisition and limitations

A schema-6 checkpoint accepts only a contiguous completed trial prefix. Resume
rechecks original source/environment and freshly prepares retained units to check
host, tools, dependencies, inventories, and gates before any new timing. Completed
trials retain their bytes, identities, and timestamps. If a trial started without
a complete retained receipt, it may have produced unretained measurements: resume
rejects it. Preserve that failed acquisition and begin an explicitly new session
at a new checkpoint path. Missing/duplicate/reordered receipts, gaps, changed
declarations/seeds, and corrupt checkpoints are errors, never resampling signals.

Historical v1–v4 evidence retains its original meaning: sequential acquisition
without a verified order-control receipt. It must not be relabeled counterbalanced.
Even a verified schedule does not establish temperature, clock, power, background
load, independence, or causal attribution. Recorded host compatibility and
counterbalancing reduce specific concerns; scientific interpretation remains the
consumer's responsibility. Marginal Criterion intervals are not uncertainty
intervals for between-trial effects.
