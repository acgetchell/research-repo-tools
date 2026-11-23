# Reusing benchmark acquisitions

Consumers own workloads, scientific acceptance, output declarations, and thin
recipe/workflow wiring. The shared package owns acquisition, trial receipts,
validation, archive conversion, and evidence mechanics. See the
[API and trust contract](candidate-reuse-api.md).
The procedure below acquires scheduled pairs. For a first release without a
predecessor, follow the [current-only procedure](candidate-reuse-api.md#first-stable-release)
with unscheduled schema-5 measurement and complete-run v6 evidence. If earlier
stable releases exist but predate the workload, [start a benchmark series](#starting-a-benchmark-series-after-earlier-releases).

## Step 1: prepare and acquire

Run `just release-dependencies` before collecting reusable evidence. Owner
capture checks the published dependencies again before timing. Development
rehearsals can use exact Git pins without owner capture or release intent.
Switching pins changes the measured inputs. See [upstream provenance](dependency-provenance-api.md).

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

Configure a schema-2 report using the `complete-report.toml` template before acquisition. In each suite's
`[suites.measurement]` table, set `context = { repository = "owner/project", acquisition-owner = "local" }`
with the repository from the release workflow. Set `owner` to a new absolute
directory path outside the checkout, under the acquisition owner's control.

```sh
just performance-prepare tooling/performance-release.toml --release-intent
just performance-release tooling/performance-release.toml --owner "$owner" --checkpoint target/release-trials
just performance-readme tooling/performance-release.toml
```

This is one bounded campaign containing the declared trial slots. Each paired
suite uses fresh exports/output trees. For each suite, the declared
phase order is executed before advancing to the next suite. Alternating order
starts baseline/current, then current/baseline. Randomization ranks zero-based
trial indices by SHA-256 of the ASCII string `rrt-schedule-v1:SEED:INDEX`, with
index as the tie-breaker; the first half run current/baseline. The declaration
retains the resulting entire schedule. No consumer chooses a fresh seed later.

Before promoting the report, the command saves the original run, evidence manifest,
policy, publication receipt, and both SHA-256 digests in the owner directory.
Retain this handoff independently of the release PR. If capture or promotion
fails after measurement, retry with
`just performance capture tooling/performance-release.toml --owner "$owner" --promote`.
For Python adapters, see the [publisher contract and capture hooks](candidate-reuse-api.md#committed-publisher-evidence).
Commit the generated outputs to the preparation PR. With v0.3.2, run the
[offline preflight](candidate-reuse-api.md#offline-preparation-preflight)
on its clean commit before merging.

Complete-run v5 retains original v4 payload/envelope bytes per trial, unique trial
and suite sessions, observed phase start/end timestamps, and exact execution
order. Reports show each trial separately. A table/CSV/plot consumer must explicitly
select a trial with `select_trial`; schema-2 README configuration requires
`trial = 1` (or another declared trial). The README labels that selection. Keep
the complete v5 evidence beside derived views; selecting a trial does not make
its v4 payload standalone proof of counterbalancing. Pooling within-phase
Criterion samples does not create independent trials or significance evidence.

## Step 2: freeze final release evidence

Wait for the required native CI evidence on the final main commit. From its
clean checkout, set `release_commit` to the independently selected full commit
ID. Obtain both digest values from the trusted Step 1 handoff.
Use a separately installed interpreter (on Windows, its
`Scripts/python.exe`) and a durable directory outside the checkout for the
final policy and acceptance. This can run before tag or draft creation.

```sh
/trusted/release-tools/bin/python -I -B -m research_repo_tools release evidence candidate tooling/release-evidence.toml \
  --checkout . --commit "$release_commit" --policy-output /durable/final-policy.json \
  --policy "$owner/policy.json" --payload "$owner/run.json" --manifest "$owner/evidence.json" \
  --acquisition-sha256 "$acquisition_sha256" \
  --publication-receipt "$owner/publication.json" --publication-sha256 "$publication_sha256" \
  --output /durable/accepted.json
```

Final acceptance checks the source binding again and verifies the signed native
evidence for this commit. Add `--format summary` for a concise verification result.

After Step 2 succeeds, create and push the release tag, then follow the consumer's
package and draft-release procedure. A final source, dependency,
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

At approval, rerun the candidate verifier with `--acceptance /durable/accepted.json`
and a new output path. Reverify the current native selection, whole-tree binding and
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

## Starting a benchmark series after earlier releases

With v0.3.2, use a series when earlier published releases predate the workload.
Copy the suite measurement template to `benchmark-series/ID.toml` and point
the release workflow's `measurement` at that path. Keep the suite declarations,
local owner context, and schema-2 report from Step 1. Set these top-level fields
to the series ID, first participating tag, and exact native case IDs per suite.
The declaration path, suite names, and semantic `contract` values identify
the series across release tags.

```toml
# benchmark-series/complete-suite.toml
schema = 5
outputs = ["README.md"]
benchmark-series = { id = "complete-suite", first-release = "v1.2.0", workloads = { extended = ["extended/size-2"] } }
```

Configure the release workflow with `mode = "infer-release"` and
`order = "version"`. Preparation enumerates stable GitHub releases, resolves each
published tag to its commit, and reads the measurement declaration from that
Git tree. Make the historical objects available locally first. A complete tree
listing establishes whether the declaration existed. An inaccessible object,
failed provider request, or changed contract stops selection. The preparation
records the release IDs, tags, commits, and matching contract digests. Review
that record before measurement. The initial release must match `first-release`.

Use the same three recipes for initial and paired releases. Choose new ignored
scratch paths and a new trusted owner directory for each acquisition.

```sh
just performance-prepare tooling/performance-release.toml
just performance-release tooling/performance-release.toml --owner "$owner"
just performance-readme tooling/performance-release.toml
```

For the initial README, configure a schema-2 publication from the report
archive with current-phase series, `[provenance.current]`, and references for
`version` and `tag`. Omit comparisons, the ratio SVG, and baseline references.
Declare the README and any generated figures in `outputs` before acquisition.
The report and README reuse the original samples and timing intervals. Review
the resulting files with the consumer's scientific gates and the final commit.

For the next compatible release, keep the ID, first tag, suite contracts, and
workload inventory. Select schema 6 and declare the paired trial schedule.
For the README, select a numbered `trial` and configure both phases.
Preparation selects the highest participating published version and binds
its tag and commit. Existing acquisitions without
a series keep their existing selection rules. The repository's first-release
command continues to require empty stable published history.

### Recovering a series candidate

If the raw payload and envelope exist, retry handoff and report promotion with
`just performance capture tooling/performance-release.toml --owner "$owner" --promote`.
A README retry uses `just performance-readme tooling/performance-release.toml`.
To retry an initial series README with independent assertions, pass
`--current-release` and `--current-revision` with the prospective tag and the
base commit of the measured working tree from the trusted Step 1 handoff.
Keep the original preparation and independent handoff digests. Follow
[Step 2](#step-2-freeze-final-release-evidence) and the [attachment procedure](#packaging-approval-and-retries)
for the reviewed commit. The verifier enumerates history again at acceptance
and approval. A changed predecessor or tag identity invalidates the selection.
Native/package evidence, archive derivation, attachment retries, and approval
all refer to the original acquisition. The consumer owns scientific
compatibility, Cargo guarantees, and release approval.

## Release recovery decisions

Use this table when an existing release candidate fails or needs another PR.
The [release recovery guide](RECOVERING_RELEASES.md) owns tag/draft replacement;
this section determines what happens to the measurements. A new tag object or
draft does not by itself require new timing, but a corrected commit must pass
the original acquisition contract before its raw data can be reused.

| What changed or failed? | Acquisition | Final acceptance and publication |
| --- | --- | --- |
| Preparation wait, rendering, capture, promotion, or upload failed; selected inputs and commit are unchanged | Preserve the original raw data and owner receipts. Retry the owning recovery/derivation operation. | Verify final acceptance and live native evidence before publication. Retain the captured target for an upload retry. |
| New commit changes only exact predeclared, eligible output paths or matches the frozen publisher receipt | Reuse only after whole-tree and original-policy verification succeeds. | Require complete native CI for the new commit and a newly reviewed Step 2 acceptance outside the checkout. |
| Source, dependency, lockfile, harness, workflow, script, command, tool, plan, fixture, or another nonexempt path changed | Finish those changes, then perform a new Step 1 acquisition under a newly frozen policy. | Generate/review outputs and obtain final-commit native evidence and Step 2 acceptance. |
| Native artifacts expired or the selected run/attempt changed; acquisition inputs remain valid | Existing raw data may still qualify. CI evidence recovery does not itself measure again. | Obtain a complete trusted producer/validation attempt and explicitly review a new selection into a new acceptance file. |
| Independent acquisition digest, owner evidence, or required publication receipt is missing or corrupt | Recover the authentic original handoff. If it cannot be recovered, reacquire with a new trusted handoff. | Never manufacture an independent digest from the candidate being checked. |
| Draft replaced for an otherwise accepted candidate | Preserve raw evidence if its reuse contract still passes. | Capture the new draft target and derive/reverify its attachment. Old target and upload receipts belong to the old draft. |

The original policy compares the **whole tree**, including file names, bytes,
and executable bits. Its output exceptions must already name exact eligible
nonexecutable Markdown, reStructuredText, CSV, or SVG paths. A document consumed
by a build, gate, or workload is an input; its extension cannot exempt it.
Committed shared-publisher evidence has a separate frozen exact-byte receipt.
Do not widen output exceptions after acquisition to accommodate a fix.
“Documentation-only” and “release-workflow-only” are not sufficient evidence for
reuse. See the [binding contract](candidate-reuse-api.md#source-and-trust-contract).

Preserve original revisions, dirty state, hosts, tools, sessions, timestamps, and
raw bytes. A new acceptance binds that historical acquisition to the corrected
final commit; it does not relabel the measurements as having run on that commit.
Save new acceptances separately and retain superseded approvals for audit.
After a required fresh acquisition, use its new independently frozen handoff
for Step 2; the old acquisition's policy and approval remain historical evidence.
The older [exact-tag v1 transport](benchmark-reuse-api.md) retains its own identity
rules; it does not acquire these candidate output exceptions automatically.

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
