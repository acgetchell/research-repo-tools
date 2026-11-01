# Verified benchmark acquisition reuse

This contract targets v0.2.5. It is a derived transport of an existing acquisition,
not a new measurement or an authentication mechanism. Consumer adoption requires
the exact supplying PyPI version and successful normal resolution, tracked by
[#170](https://github.com/acgetchell/research-repo-tools/issues/170). A merged
implementation or local wheel does not establish published availability.

## Architecture and trust decision

The Astra checkpoint for [#169](https://github.com/acgetchell/research-repo-tools/issues/169)
permits a bounded complete-run v4 conversion. Preparation, acquisition, rendering,
packaging, draft attachment and approval-time publication remain separate stages.
Existing engines continue to own each stage; conversion adds only independent
acceptance and a byte-preserving projection of the retained current observations.

| Capability | Existing contract | Reuse decision |
| --- | --- | --- |
| Acquisition and source identity (#127/#137) | Complete raw cases, source exports, gates, declarations and probes | Validate the entire v4 run against an independently supplied schema-5 plan; retain every original byte and observation. |
| Availability and collection ownership (#166) | Paired suite units with original sessions and nanosecond intervals | Support v4 only; absent sides remain absent, and no session is invented for older evidence. |
| Preparation and README (#129) | Independent reference assertions and offline retained-evidence publication | Reuse these stages; rendering never calls acquisition. |
| Saved-sample and suite transport (#158) | Verification of native fresh/saved equality and suite assembly | Keep that contract. The new derived archive does not claim native saved-pair production. |
| Evidence and assets (#140) | Captured release target, immutable artifact selection, signer/event/ref/run/attempt checks, upload byte verification | Preserve those boundaries. An evidence envelope, reuse policy or derived receipt is not signed CI evidence. |
| Missing conversion | No complete-run-to-release projection with independent acceptance assertions | Add `benchmark_reuse`, using existing archive and publication engines. |

Consumers choose acquisition location explicitly as `local` or `github-actions`,
recording it in every suite's measurement context as `acquisition-owner`, alongside
`repository = "owner/name"`. Missing recorded origin is rejected. This label alone
authenticates nothing. A consumer requiring hosted acquisition must verify its
original immutable artifact and permitted producer, repository, workflow, signer,
event, ref, run and attempt before supplying acceptance assertions. Re-signing a
local upload in CI does not establish that CI performed the acquisition.

The existing release evidence API verifies its source/job or Cargo-archive
contract; it does not automatically authenticate benchmark payload digests.
Consumer orchestration must bind the expected run identity and observations to
its trusted acquisition handoff. This API neither creates that trust binding nor
relaxes protected environments, approval or credential separation.

## Release ordering and identity

The supported one-acquisition ordering finalizes and tags the source before
measurement. Acquire once under the selected owner, then render the report and
README and derive the release archive from those bytes. README changes made
after acquisition belong to a later commit or a separate publication location.

If the new README must be inside the release commit, prospective measurement
precedes the README edit and final tag. That workflow still needs its existing
exact-tag acquisition. A matching source subtree cannot turn the earlier commit
into the final commit. Working-tree-to-tag equivalence is deliberately unsupported;
retain the safe two-acquisition behavior until a separate policy is implemented
and verified. Real release ordering and scientific acceptance remain downstream
in [la-stack#323](https://github.com/acgetchell/la-stack/issues/323).

Both the envelope's current source and every projected current observation must
identify the target tag and full commit. Each observation must retain a clean
revision export. The complete evidence still distinguishes effective measured
source, original source and any common harness overlay. A derived benchmark
archive is not the release's native library/package artifact and makes no claim
that an unmodified tagged harness ran.

## Public API

Import from `research_repo_tools.benchmark_reuse`:

| Interface | Contract |
| --- | --- |
| `REUSE_SCHEMA` | `research-repo-tools/benchmark-reuse/v1` |
| `ReusePolicy(target, acquisition_owner, identity, measurement, observations, comparisons=())` | Independent release target, explicit owner, original run identity, schema-5 plan, and exact `(suite, phase, Provenance)` assertions for every observed side |
| `parse_reuse_policy(payload)` | Strict versioned JSON; rejects unknown, duplicate and malformed fields; parsing does not authenticate assertions |
| `plan_reuse_archive(root, payload, manifest, policy, output, *, limits=ArchiveLimits())` | Read three independent root-relative inputs, validate and render the complete candidate, return an immutable `PublicationPlan` without output writes |
| `serialize_reuse_policy(policy)` | Deterministic UTF-8/LF JSON for a separately trusted handoff |
| `verify_reuse_archive(archive, policy, *, limits=ArchiveLimits())` | Bounded private extraction and full independent revalidation; return original `Evidence` without acquisition, host recapture, network or publication |

Obtain the expected identity and provenance from the trusted acquisition owner,
independently of the candidate being checked. Reading the candidate to manufacture
its own acceptance policy defeats the contract. The plan independently asserts
commands, workload policy, inventories, units, statistics, confidence and sampling;
exact provenance assertions also bind actual tool/dependency observations,
discovery inventories, effective/original source, harness, host, environment and
successful scientific gates. The expected run identity binds all original
sessions, collection intervals, raw data and envelope bytes. Consumer gates
remain responsible for scientific validity; structural validation cannot prove it.

`comparisons` contains explicit pairs of `(phase, full_id)` observations. Every
requested pair must pass the existing observation-level comparison checks.
Empty comparisons permit absolute-only export, including independent acquisitions
on different hosts. Common-host compatible peers can share their original unit;
cross-unit or incompatible cross-host ratios fail. Packaging creates no new
comparison authority. Renderers retain their existing eligibility checks.

The planner snapshots exact payload, envelope and policy-file bytes. Publish its
result through `publication.publish_publication`, which rechecks inputs and output
before the shared rollback/recovery transaction. An identical existing archive
is a no-op; a conflicting archive, stale input, alias or unsafe path fails before
replacement. Callers must exclude concurrent writers; this is not locking or
crash atomicity. `ArchiveLimits` applies to inputs, archive members and total content.

## Derived archive format

The deterministic tar.gz contains:

- `evidence/run.json` and `evidence/evidence.json`: the exact original files,
  including all baseline, current, unavailable and retained-unit provenance.
- `criterion/<original-storage-path>/<recorded-sample-name>/<raw-file>`:
  every original core and declared supplemental file for all current cases.
- `reuse.json`: a versioned derived-transport receipt binding the target, owner,
  original run identity, canonical policy digest, projection and file digests.

The archive does not create another native `new`/saved pair or rename measurements
to a release label. Original raw files retain exact encodings and newlines.
Storage collisions, including portable case/Unicode directory aliases, fail.
Every file is verified against a projection reconstructed from the retained
evidence and independent policy; embedded digests alone are not acceptance.

Old complete schemas, missing origins or tool identities, incomplete samples,
changed declarations, stale assertions, working-tree inputs and incompatible
requested comparisons fail. Legacy consumers requiring `criterion-suite/v1` or
native saved-pair semantics must explicitly adopt this derived format; conversion
does not silently satisfy their former contract.

## Attachment and approval

Packaging is offline and does not resolve or authenticate the target. Before
attachment and again at approval, copy the selected archive with a bounded binary
read into a private directory under exclusive trusted ownership. Verify that
private copy against the current independent policy, then pass the same private
path to `release_assets.publish_release_asset` with `target=policy.target`.
Verifying a shared path and later uploading that path is insufficient: a
replacement between the two operations would bypass acceptance. The existing API's target, draft-state,
remote-tag, asset digest and downloaded-byte checks remain required. Keep
`publish=False` for draft attachment; publication approval remains separate.
The acquisition host is never replaced by the packaging or uploading host.

Consumer release workflows own authentication and selection of the artifact being
approved. This contract does not authorize arbitrary local evidence as trusted
CI output or supply a custom uploader. Shared native/package regressions belong
upstream; adoption must wait for verified publication without blocking that
publication on downstream workflow changes.
