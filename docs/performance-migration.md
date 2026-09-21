# Retained performance evidence migration

Adoption requires a published package after `0.1.2` containing the
[performance APIs](performance-api.md). Package implementation, publication,
and consumer adoption are separate checkpoints. Keep retained evidence readable
throughout the migration; a new common envelope does not replace old schemas
automatically. Consumer commands and new-evidence examples are in the
[README](../README.md#performance-evidence).

## Preserve existing evidence

MCMC retains `criterion-comparison/v1` CSV with
`mcmc-performance-provenance/v1` or `/v2` sidecars. la-stack and other consumers
have their own columns, eligibility rules, and provenance requirements. Keep
these original CSV/JSON files, their schema IDs, and recorded hashes untouched.
Retain a small consumer adapter for each supported historical schema. That
adapter parses its column layout and domain metadata, verifies its recorded
digest, and maps timings into shared `Estimate`/`Sample` values.

At the raw boundary, use the original bytes:

```python
from research_repo_tools.evidence import verify_sha256

def verified_legacy_csv(csv_path, recorded_sha256):
    payload = csv_path.read_bytes()
    verify_sha256(payload, recorded_sha256)
    return payload.decode("utf-8")
```

Read the sidecar in the consumer's existing schema parser to obtain the recorded
digest and validate release labels, suite/scope, commands, validation eligibility,
and source identities. Do not hash normalized CSV, regenerate sidecars, or
substitute live source metadata before validating the original evidence. If old
digests only validate after newline normalization, flag that artifact for an
explicit consumer migration decision; never silently repair the file or digest.

Map both complete sample inventories into `Sample`, not only the common rows.
Use `compare_samples` for paired rows, `missing_baseline` for current-only rows,
and `missing_current` for baseline-only rows. For existing MCMC reports, map
`Estimate.point/lower/upper` to its duration formatter and retain its existing
labels and narrative. `speedup` and `percent_reduction` retain the baseline/current
and positive-time-reduction meanings. Keep the old renderer's display precision
and scientific interpretation until the consumer explicitly changes those policies.

Existing schema validation can therefore stay small while generic number checks,
pairing, arithmetic, hashing, compatibility diagnostics, safe extraction, and
publication move to shared calls. Test each retained-schema adapter against small
representative known artifacts in the consumer, without copying old implementations
into this package.

## Introduce new evidence deliberately

For new measurements, serialize full comparison data with `serialize_comparison`
and wrap it in `Evidence` using `COMPARISON_SCHEMA`. Attach separately captured
baseline/current source commits, source and harness digests, and the context
required by the consumer's compatibility policy. Preserve missing historical
values as `None` or absent context keys; a required unknown field cannot pass
compatibility.

If converting retained data, write the new payload and envelope under new paths.
Keep the original payload and sidecar as immutable evidence, with explicit adapter
schema/version and original digests recorded in consumer provenance context.
Use those original files for old references. The new format never claims to be
byte-identical to the old CSV or a drop-in replacement for its digest scheme.
An existing opaque payload can also be wrapped with its original schema ID;
this adds a separate integrity envelope and does not remove its original sidecar.
The consumer must still validate that payload's schema before interpreting it.

Load new envelopes with `load_evidence` and parse the payload using its declared
schema. Rendering uses retained data without running Cargo or consulting today's
Git state. Promotion calls `publish_evidence` with the payload, sidecar, and every
pre-rendered report/index in one transaction. Mark archive paths immutable. Loading
and republishing a shared envelope preserves both original files byte for byte.

## Shared policy changes

The common contract deliberately supersedes several local implementation choices:

- A missing Criterion root fails instead of silently behaving like an empty tree.
- Comparison units/statistics must agree, and booleans/non-finite numbers and
  invalid intervals are rejected before storing estimates.
- Exact-byte hashing preserves CRLF and binary inputs; text-mode normalization is
  never part of integrity verification.
- Required unknown compatibility values fail even if both sides are unknown.
- Archives extract into an absent destination through private staging; links,
  portable path collisions, unsafe names, and size violations fail before publication.
- Shared publication rejects leaf symlinks and conflicting immutable content and
  exposes recovery backups if rollback is incomplete.

Do not add compatibility flags to reproduce weaker former behavior. Consumers
own the choice to adopt these contracts and any explicit remediation of old evidence.

## Execution and downstream acceptance

For README and document publishers, follow the
[publication migration guide](publication-migration.md). It covers reuse of
legacy schema adapters and renderers, exact tagged artifact verification, and
the shared marker/snapshot/transaction contract.

Workload selection, statistical tests, performance acceptance, published-release
selection, authentication, Cargo commands, worktree lifecycle, and harness
substitution remain consumer-owned. Inspection of the current consumers shows
different execution and compatibility contracts, so this increment introduces no
shared worktree or benchmark runner. Consumers can use the supported process API
for execution, retaining binary Git input and their own rollback guarantees.

Before deleting helpers, pin the published package and exercise each consumer's
complete workflow: retained legacy reads, unchanged reports, missing benchmarks,
stale source/harness rejection, unsafe release assets, and failed promotion with
unchanged originals. Keep domain tests in consumers and shared implementation
regressions here. Native Linux/macOS/Windows checks and wheel/sdist checks belong
to this package's existing CI matrix. A local run is not evidence that the other
platforms or a migrated consumer passed. Release and adoption completion remain
necessary before closing the parent extraction issue's published-package acceptance.
