# Reusing benchmark acquisitions

This procedure is the v0.2.6 contract for consumer release preparation. Consumers
own workloads, scientific acceptance, output declarations, and thin recipe/workflow
wiring. The shared package owns acquisition, trial receipts, validation, archive
conversion, and evidence mechanics. See the [API and trust contract](candidate-reuse-api.md).

## Step 1: prepare and acquire

When using the v0.2.7 published-dependency policy, run `just release-dependencies`
and pass `--release-intent` to preparation and acquisition before final release
measurements. Exact Git pins remain supported for development rehearsals.
Replacing them with a published pin changes dependency/harness inputs and can
invalidate checkpoints and candidates; revalidate and reacquire when required.
See [upstream provenance](dependency-provenance-api.md).

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

This is one bounded campaign containing the declared trial slots. Each paired
suite uses fresh exports/output trees. For each suite, the declared
phase order is executed before advancing to the next suite. Alternating order
starts baseline/current, then current/baseline. Randomization ranks zero-based
trial indices by SHA-256 of the ASCII string `rrt-schedule-v1:SEED:INDEX`, with
index as the tie-breaker; the first half run current/baseline. The declaration
retains the resulting entire schedule. No consumer chooses a fresh seed later.

The acquisition owner freezes the independent Step 1 assertion digest and original
payload/envelope through the trusted handoff before report edits or final merge.
Use `CandidatePolicy(commit=None, ...)` and `acquisition_assertion_digest` under
that owner's control. Retain original raw evidence in ignored scratch or immutable
artifact storage. To commit the shared publisher's generated evidence, declare
`report="tooling/performance-report.toml"` in that policy before freezing its
digest. The schema-2 report declaration must be present before acquisition.
Use `plan_candidate_publication` to freeze a separate exact-byte publication
receipt **before** promotion, then publish its returned plan. Independently retain
both the receipt and its SHA-256. The existing content-addressed run files,
indexes, pointer and reports may then be committed in the release PR.

For existing `measure_release` adapters, use its `before_promotion` callback to
capture the handoffs without changing the acquire/retain/promote order. The
default CLI does not capture trusted assertions on the consumer's behalf; wire
the callback in the trusted acquisition owner's thin adapter, or use the staged
public APIs. Never rerun acquisition merely because capture or promotion failed.
See the [committed publisher contract](candidate-reuse-api.md#committed-publisher-evidence).
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
`--acquisition-sha256`, and a new `--output` acceptance file. When committing
publisher evidence, also supply the original `--publication-receipt` and separately
trusted `--publication-sha256`. Add `--format summary` for a concise verification
result; the acceptance bytes are unchanged. This may run before
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

Keep final acceptance, native attestations and derived archive receipts outside
committed source; they bind the final commit and cannot be committed into that
same commit. Implementation, maintainer-approved patch publication, and consumer
adoption are separate gates. Verify the supplying published package before
adoption; consumer migrations do not block upstream publication.

## Interrupted acquisition and limitations

Schema-6 recovery retains a contiguous completed trial prefix and the contiguous
completed paired-suite prefix within the next trial. Resume rechecks original
sources/environment and freshly prepares every suite to check host, tools,
dependencies, inventories, policies, declarations, and gates against retained
assertions, including interrupted attempts, before any new timing. It retains
every durable completed pair and repeats the first uncommitted pair in full,
in its original scheduled phase order. It never borrows an interrupted phase.
The schedule and measurement budget of completed slots do not change; retries
are additional, explicitly disclosed work.

The exclusive schema-2 checkpoint adds a checksummed append-only event journal.
Starts precede timing; phase receipts retain accepted raw bytes and provenance;
paired-suite commits precede the next suite. Interrupted attempts remain in a
versioned trial lifecycle, outside accepted paired samples. On caught phase
failure, opaque output is retained up to 4,096 files and 16 MiB per attempt.
Absent, unreadable, unsafe, or oversized output has an explicit capture status;
a hard-killed process can leave partial bytes unavailable. Such an unfinished
attempt has no invented finish time. Recovery records its own observation time
and a separate collection segment. If all pairs committed before interruption,
recovery validates and finalizes them without measuring again. Completed trial
payloads, sessions, timestamps, order receipts, and raw bytes remain exact.

Missing/duplicate/reordered receipts, gaps, changed declarations/seeds, partial
event records, and corrupt checkpoints reject resume. An unresolved writer lock
requires explicit operator recovery after verifying exclusive ownership; it is
never stolen. Legacy schema-1 checkpoints can retain completed whole trials, but
their interrupted trials lack lifecycle receipts and still reject resume. Preserve
those acquisitions and use a fresh checkpoint. Existing complete-run v5 evidence
remains readable without synthesizing lifecycle fields; older readers may reject
new lifecycle-bearing records.

Recovery retains whole pairs, immutable prefixes, explicit segments/abandonments,
and validated interrupted-attempt preparation. Recorded retries make exclusions
auditable; they do not prove that interruptions were independent of observed
performance or eliminate optional-stopping bias. A recovered trial is a completed
campaign slot, not evidence of one uninterrupted or freshly independent session.

Historical v1–v4 evidence retains its original meaning: sequential acquisition
without a verified order-control receipt. It must not be relabeled counterbalanced.
Even a verified schedule does not establish temperature, clock, power, background
load, independence, or causal attribution. Recorded host compatibility and
counterbalancing reduce specific concerns; scientific interpretation remains the
consumer's responsibility. Marginal Criterion intervals are not uncertainty
intervals for between-trial effects.
