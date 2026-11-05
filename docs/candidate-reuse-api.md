# Candidate acquisition acceptance

The v0.2.6 candidate contract supports measurement before the release-preparation
merge. It is separate from the unchanged [exact-tag v1 contract](benchmark-reuse-api.md).
It requires measurement schema 6 and complete-run v5 evidence. Historical v1–v4
evidence is not upgraded, assigned missing receipts, or silently measured again.

## Source and trust contract

Step 1 declares the whole experiment, including source and harness selections,
dependency files, commands, scientific gates, probes, workloads, trial schedule,
and exact permitted report/document output paths. Source exports retain the
entire original tracked/nonignored tree, byte digests and native permissions.
Per-observation provenance retains the effective source after any common harness
overlay, original source, host, tools, environment, inventory, and raw bytes.

The acquisition owner retains the original payload/envelope and independent
assertions. `acquisition_assertion_digest(policy)` covers the repository, version,
owner, entire measurement declaration including output exceptions, run identity,
original candidate and every trial/suite/phase provenance assertion. Only the
future final commit is omitted. Set `commit=None` in the Step 1 policy; assigning
the reviewed final commit with `dataclasses.replace` does not change this digest.
Existing policies that supply a commit in Step 1 remain supported.
The consumer must retain the digest through a separately trusted immutable
handoff. A policy, embedded checksum, or re-signed upload cannot authenticate
itself. Computing the trusted digest from the candidate being checked defeats
this boundary. Hosted acquisition requires verification of the original permitted
producer, signer, event, ref, run, attempt, and immutable artifact upstream of
this API; local acquisition remains labeled local.

Step 2 requires a non-null final commit, independently captures it, and requires
clean HEAD at that commit, including the index, working tree and submodules.
It checks checkout cleanliness before and after native verification and rejects
assume-unchanged or skip-worktree entries. It compares **all** original candidate and final file names,
bytes, and executable bits. Additions, deletions, renames, and input changes
invalidate reuse unless they are exact predeclared output paths. Output exceptions
are limited to nonexecutable Markdown, reStructuredText, CSV, and SVG files. They
cannot overlap declared input patterns or the actual source/harness/workload
inventories, including portable case/Unicode aliases. The measurement controller
cannot exempt its own configuration. Build files, lockfiles, scripts, and arbitrary
configuration files are not output exceptions. Consumers must truthfully declare
all inputs: a document read by a build or workload is an input, regardless of its
extension. Committed shared-publisher evidence uses the separate exact-byte
contract below; JSON is not an ordinary output exception.

Permission comparison projects native permissions onto Git's executable bit;
ordinary writable/read-only differences are not Git tree differences. The
original native permission receipt is preserved. A Windows working-tree capture
does not establish an executable bit that the filesystem cannot observe. If the
final tree introduces such a bit, binding rejects it; use a revision export that
retains the original Git modes for that acquisition. No platform normalization
changes the measured bytes or invents missing executable-state evidence.

The existing native evidence verifier still authenticates final-commit CI,
required jobs/steps/platforms, signer, event/ref, run/attempt and immutable artifact.
Candidate acceptance does not weaken its source or Cargo archive guarantee.
Acquisition provenance is never rewritten to the final commit or packaging host.
The independent binding describes final release acceptance separately.

## Committed publisher evidence

Declare `CandidatePolicy(report="tooling/performance-report.toml", ...)` before
freezing the acquisition assertion digest. The report configuration must already
exist in the original measured tree. This selects the existing **schema-2 complete
run publisher**, including configurable archive locations such as
`docs/performance-runs` and `docs/performance/runs`. Schema-1 comparison histories
require explicit adoption of schema 2; existing history is never rewritten or
assigned missing acquisition receipts. The optional declaration serializes as
candidate-policy v2. Policies without it retain their v1 bytes and behavior.

After acquisition and before promotion, call
`candidate_publication.plan_candidate_publication(root, payload, manifest, policy,
acquisition_sha256=trusted_digest)`. It returns `(PublicationPlan, receipt_bytes)`
from the existing validated run publisher. It verifies the original acquisition,
report configuration, prior output states and complete history against the
independent acquisition assertions. Publish the plan through
`publication.publish_publication`. Keep the canonical receipt and its SHA-256 in
the acquisition owner's separately trusted immutable handoff. Never create trusted
expectations by scanning the final candidate or accepting its embedded checksum.

The receipt binds the original acquisition digest/identity, publisher version,
original configuration and input digests, and exact roles, paths, lengths, hashes
and nonexecutable modes for seven outputs: the content-addressed
`runs/<identity>/{run.json,evidence.json,report.md}`, current report,
`index.json`, `latest.json`, and history `README.md`. Content-addressed names are
resolved after acquisition. No directory or JSON suffix is exempted. Inputs,
configuration, portable path aliases, executable files, and unrelated additions
remain prohibited. Historical runs and their original renderer bytes remain exact,
even if a legacy document exception names them. A generated output also named in
`measurement.outputs` must satisfy the stricter exact-byte receipt.

Step 2 passes paired `publication_receipt=receipt_bytes` and
`publication_sha256=trusted_publication_digest` to `verify_candidate_release`.
Every generated file must be present with its frozen bytes and mode, whether or
not it changed. The binding retains the publication receipt and digest; approval
revalidation and offline archive verification use that same binding. The receipt
does not authenticate acquisition ownership or replace signed native evidence.
Keep final-commit acceptance and attestations as release assets or trusted scratch
handoffs, outside committed history, to avoid self-referential records.

`performance_release.measure_release(..., before_promotion=callback)` provides a
trusted-owner hook after raw retention and before report writes. The callback can
freeze the independent candidate policy and publication receipt; it must not
publish the returned plan itself. A failed callback leaves raw evidence available
and prevents promotion. Retry capture/promotion directly from retained bytes;
calling `measure_release` again is a new acquisition. The standalone
`measure_and_retain` → `plan_candidate_publication` → `publish_publication` APIs
also support explicit staged orchestration. README publication remains separate
and uses the existing exact declared document exceptions.

## Public interfaces

Import from `research_repo_tools.candidate_reuse`:

| Interface | Contract |
| --- | --- |
| `CandidatePolicy` | Repository, canonical tag, final commit (`None` until selected), original owner/run identity, schema-6 measurement, original current envelope provenance, `(trial, suite, phase, Provenance)` assertions, and optional original `report` configuration |
| `acquisition_assertion_digest(policy)` | Step 1 digest captured by the trusted acquisition owner; excludes only the future final commit |
| `parse_candidate_policy`, `serialize_candidate_policy` | Strict versioned JSON handoff; serialization does not authenticate the assertions |
| `verify_candidate_release(root, evidence, policy, native_policy, read, *, acquisition_sha256, publication_receipt=None, publication_sha256=None, expected=None)` | Step 2 whole-tree binding plus existing native release verification; returns frozen acceptance bytes. When `policy.report` is set, both publication arguments are required as a pair, and `publication_sha256` must be independently trusted |
| `plan_candidate_archive(root, payload, manifest, policy, acceptance, output)` | Offline immutable archive planning with stale-input checks and shared publication transactions |
| `verify_candidate_archive(archive, policy, acceptance, *, target=None)` | Bounded verification of the exact frozen archive and every member, optionally bound to the independently captured draft target |

All archive interfaces accept `ArchiveLimits`. Publish plans with
`publication.publish_publication`. Policy, acceptance, evidence, and archive paths
must be distinct. Original payload/envelope bytes and current raw samples from
every trial remain exact, including CRLF and supplemental binary data. Projection
paths include trial and suite identifiers; repeated measurements never overwrite
one another or become a synthetic pooled sample.

Use keyword arguments to distinguish the policy's independent assertions. The
owner's Step 1 code supplies the observed values below through its trusted handoff:

```python
from dataclasses import replace

from research_repo_tools.candidate_reuse import CandidatePolicy, acquisition_assertion_digest

step_one = CandidatePolicy(
    repository=repository,
    tag=prospective_tag,
    commit=None,
    acquisition_owner=owner,
    identity=original_run_identity,
    measurement=predeclared_measurement,
    candidate=original_candidate,
    observations=original_observations,
)
trusted_digest = acquisition_assertion_digest(step_one)
# Later, after independently selecting the reviewed final commit:
final_policy = replace(step_one, commit=reviewed_commit)
assert acquisition_assertion_digest(final_policy) == trusted_digest
```

`verify_candidate_release` also accepts keyword-only `reproduced_archive` and
`reproduced_tools`, forwarding them to `release_evidence.verify_release_evidence`.
Supply both for a `cargo-archive` policy to compare independently reproduced bytes
and tool identities. Native verification failures propagate as `ValueError`;
the candidate adapter neither bypasses rejection nor invokes a reproduction.
The verifier returns immutable acceptance bytes and does not write output files
or modify the observed checkout. Missing files and process failures retain the
underlying I/O/process exceptions.

The acceptance record freezes the canonical policy digest, acquisition identity,
whole-tree binding, native evidence selection and exact derived archive digest.
At approval, pass the **original saved acceptance bytes** as `expected` to
`verify_candidate_release`; this rechecks current native evidence and source.
It must not silently select a replacement artifact or run attempt. Archive
verification alone is offline integrity validation, not fresh native CI approval.

Before attachment, copy the selected archive with a bounded binary read into
exclusive trusted private storage. Verify that copy against the frozen acceptance
and captured `ReleaseTarget`, then upload that same path through
`release_assets.publish_release_asset`. Keep its existing draft, remote-tag,
asset digest, downloaded-byte, and approval checks. Credential acquisition,
protected environments, and publication approval remain consumer responsibilities.

The full operational sequence and recovery rules are in
[Reusing benchmark acquisitions](REUSING_BENCHMARKS.md). Upstream implementation,
merge, verified supplying PyPI publication, and downstream normal-resolution
verification are separate completion gates. Local wheels and version edits do
not establish published availability or unblock downstream adoption.
