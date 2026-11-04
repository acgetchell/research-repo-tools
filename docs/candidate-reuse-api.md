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
future final commit is omitted. The Step 1 policy can use the candidate commit
temporarily; substituting the reviewed final commit does not change this digest.
The consumer must retain the digest through a separately trusted immutable
handoff. A policy, embedded checksum, or re-signed upload cannot authenticate
itself. Computing the trusted digest from the candidate being checked defeats
this boundary. Hosted acquisition requires verification of the original permitted
producer, signer, event, ref, run, attempt, and immutable artifact upstream of
this API; local acquisition remains labeled local.

Step 2 independently captures the selected final commit and requires clean HEAD
at that commit. It compares **all** original candidate and final file names,
bytes, and executable bits. Additions, deletions, renames, and input changes
invalidate reuse unless they are exact predeclared output paths. Output exceptions
are limited to nonexecutable Markdown, reStructuredText, CSV, and SVG files. They
cannot overlap declared input patterns or the actual source/harness/workload
inventories, including portable case/Unicode aliases. The measurement controller
cannot exempt its own configuration. Build files, lockfiles, scripts, and arbitrary
configuration files are not output exceptions. Consumers must truthfully declare
all inputs: a document read by a build or workload is an input, regardless of its
extension. Unrecognized output formats require an upstream contract extension.

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

## Public interfaces

Import from `research_repo_tools.candidate_reuse`:

| Interface | Contract |
| --- | --- |
| `CandidatePolicy` | Repository, canonical tag, full final commit, original owner/run identity, schema-6 measurement, original current envelope provenance, and `(trial, suite, phase, Provenance)` assertions |
| `acquisition_assertion_digest(policy)` | Step 1 digest captured by the trusted acquisition owner; excludes only the future final commit |
| `parse_candidate_policy`, `serialize_candidate_policy` | Strict versioned JSON handoff; serialization does not authenticate the assertions |
| `verify_candidate_release(root, evidence, policy, native_policy, read, *, acquisition_sha256, expected=None)` | Step 2 whole-tree binding plus existing native release verification; returns frozen acceptance bytes |
| `plan_candidate_archive(root, payload, manifest, policy, acceptance, output)` | Offline immutable archive planning with stale-input checks and shared publication transactions |
| `verify_candidate_archive(archive, policy, acceptance, *, target=None)` | Bounded verification of the exact frozen archive and every member, optionally bound to the independently captured draft target |

All archive interfaces accept `ArchiveLimits`. Publish plans with
`publication.publish_publication`. Policy, acceptance, evidence, and archive paths
must be distinct. Original payload/envelope bytes and current raw samples from
every trial remain exact, including CRLF and supplemental binary data. Projection
paths include trial and suite identifiers; repeated measurements never overwrite
one another or become a synthetic pooled sample.

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
merge, verified exact 0.2.6 PyPI publication, and downstream normal-resolution
verification are separate completion gates. Local wheels and version edits do
not establish published availability or unblock downstream adoption.
