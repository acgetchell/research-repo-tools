# Release evidence API

`research_repo_tools.release_evidence` provides the public core used to create,
verify and freeze exact-commit release evidence. Consumer deployment workflows,
build commands, scientific checks, approval environments and token acquisition
remain consumer-owned. Installed `release evidence` and `release assets` commands
expose the foundation through strict declarative policies and file handoffs.
Working consumer adapters remain until the verified published supplying release
is pinned and its integration passes. See the
[migration map](performance-migration.md#release-interface-reconciliation).

[Verified CI reuse](REUSING_CI.md) is a separate, opt-in source-check contract.
The APIs on this page continue to require the exact release commit and preserve
the distinction between source validation and package/archive identity.

The v0.2.6 `release evidence candidate` command composes this exact final-commit
verification with [pre-tag benchmark acceptance](candidate-reuse-api.md). It
requires the separately trusted Step 1 assertion digest and freezes the original
acquisition, full-tree binding, native selection, and exact benchmark archive.
Later approval uses the original `--acceptance`; no benchmark or preflight runs.

For the v0.2.7 committed-publication contract, schema-2 publisher outputs
additionally require `--publication-receipt` and the acquisition owner's
independent `--publication-sha256`. The receipt is
captured before promotion with `candidate_publication.plan_candidate_publication`;
it cannot be reconstructed as trusted evidence from the final candidate.

## Verification presentation

In v0.2.7, `candidate`, `verify` and `published` accept `--format summary` to render the
existing verifier's completed result, after any requested handoff writes succeed.
The summary identifies release/version/full commit, native run/attempt, actual
package guarantee, optional benchmark identity and original source, signature and
artifact integrity. Absent benchmark/signature/reproduction checks are explicitly
not configured or not requested. Cargo reproduction is reported only when supplied
and verified; retained Cargo evidence does not establish registry-byte equality.

`--format json` emits the same canonical record saved to `--output`. The shared
CLI default remains `--format status`; output files, `GITHUB_OUTPUT`, return
statuses and approval revalidation are unchanged. Creation reports an unverified
manifest, never verified success. Failure diagnostics identify missing, stale or
incompatible evidence and do not produce a success summary. The public pure
`release_summary.render_release_summary` formats just-returned verifier selections;
it does not authenticate arbitrary JSON or select/check evidence itself.

## Policy and guarantees

The CLI/configuration module is `research_repo_tools.release_interfaces`.
`load_evidence_policy(path)` reads schema-1 TOML into `EvidencePolicy`.
Required fields are `repository`, `package`, `workflow`, `workflow-name`,
`artifact-prefix`, `guarantee`, and `jobs`. Optional `branch` defaults to `main`,
and `path-in-vcs` defaults to the package root. Optional `package-kind` is `auto`
(the existing Cargo-first behavior), `cargo`, or `python`. Each `[[jobs]]` contains exact
`name`, nonempty `steps`, and optional `runner-labels`. `[signing-job]` contains
its separate `name` and `steps`. Use the packaged `release-evidence.toml` as a
scaffold, declaring the consumer's actual native/feature/Cargo gates.

`EvidencePolicy(validation, package, artifact_prefix, guarantee,
signing_job=None, path_in_vcs="", package_kind="auto", distributions=None)` is immutable. Its `validation` is a
`release_validation.ValidationPolicy(repository, workflow, workflow_name,
required_steps, branch="main", runner_labels=())`. Job and step names are exact;
optional runner-label requirements bind named jobs to declared hosted platforms.
Self-hosted labels reject when a job has a platform requirement. Consumers must
declare every native/feature/package gate they require, including checks proving
the actual retained archive was validated. Job names alone do not prove that an
arbitrary workflow tested its checkout or archive correctly.

| Guarantee | What verification establishes | What it does not establish |
| --- | --- | --- |
| `signed-source` | Successful declared jobs/steps for the exact trusted push, plus a signed manifest binding their job IDs and source/policy | Identity of a Cargo archive or bytes eventually uploaded |
| `cargo-archive` | Successful declared gates, immutable artifact identity, archive hash and clean Cargo source/package metadata; optional exact reproduction with matching producer tools | Cryptographic signing unless a signing job is configured; identity of a later native Cargo upload |
| `python-distributions` | Successful declared gates for the exact push/attempt, immutable artifact provenance, exact wheel/sdist inventory, core metadata and byte hashes, bounded age and live retention | Cryptographic signing unless a signing job is configured; scientific correctness or proof that consumer jobs actually tested those files |

Signed source evidence requires a separate `signing_job=(name, steps)`.
Cargo evidence may also declare one. Signatures always require the configured
repository/workflow, exact signer and source commit, branch ref, push trigger,
hosted signer and exact run/attempt invocation. `gh attestation verify` performs
cryptographic verification; parsing a bundle never grants trust.

The policy supports stable `X.Y.Z` versions. Python identity accepts normalized
Python distribution names; existing automatic/Cargo policies retain Cargo names.
Workspace crates declare their canonical POSIX `path_in_vcs`; the root default
is the empty string. Artifact names are always
`PREFIX-RUN_ID-ATTEMPT`. This standard naming and the internal manifest bind the
attempt because GitHub's artifact association does not expose an attempt field.

## Creation and verification

`create_evidence_manifest(policy, version, commit, run_id, attempt, read, *,
archive=None, tools=None, distributions=None)` returns deterministic UTF-8 JSON bytes. It observes the
executing push through authenticated provider reads and checks its identity again
before returning. Signed-source creation requires completed validation jobs;
the separate signing/upload job may still be running. Cargo creation requires
the already-validated archive and nonempty `cargo`/`rustc` producer tool strings.
It does not run Cargo or assume two different dry runs produced equal archives.

The consumer must independently observe the clean checkout, package/version,
executing event and run/attempt, and preserve those inputs while retaining the
manifest. Retain it as `validation.json`, together with `validation.sigstore.json`
when signing is required and `PACKAGE-VERSION.crate` for archive evidence. The
producer signs the exact manifest bytes. Creation does not write or sign files.

`verify_release_evidence(policy, version, commit, read, *, expected=None,
reproduced_archive=None, reproduced_tools=None, distributions=None)` returns deterministic JSON
selection bytes only after all verification succeeds. It performs one selection
attempt; pending CI rejects with a manual-retry diagnostic. Missing, expired,
ambiguous, wrong-context and incomplete evidence never select a fallback commit.

The `read(endpoint)` callable must return parsed JSON from authenticated,
repository-scoped GitHub GETs. `release_validation.api(endpoint,
repository=...)` supplies that transport through `gh`. For example:

```python
from functools import partial
from pathlib import Path

from research_repo_tools.files import replace_many
from research_repo_tools.release_evidence import verify_release_evidence
from research_repo_tools.release_validation import api

# policy, version and observed_commit are trusted consumer inputs.
read = partial(api, repository=policy.validation.repository)
selection = verify_release_evidence(policy, version, observed_commit, read)
replace_many({Path("selection.json"): selection})

# After approval, re-observe the source and use the original saved record.
verified = verify_release_evidence(
    policy, version, observed_commit, read,
    expected=Path("selection.json").read_bytes(),
)
```

The selection binds schema, complete policy, package/version, repository IDs,
workflow/event/ref/commit, run/attempt, every required job ID, artifact ID/name/size/
SHA-256 and manifest SHA-256. Its parser rejects duplicate fields, nonfinite
numbers, invalid UTF-8 and nesting beyond 128 levels. Numeric identity types
remain significant. Caller-supplied records are assertions, never authority.

After approval, the verifier reads the captured run and artifact IDs and checks
current gates, signatures and retained bytes again. It rejects changed attempts,
job IDs, policies, artifacts and manifests. It rechecks provider state after the
download/signature work. There is no cross-commit/equal-tree reuse in this API.
Consumers must call it immediately before token acquisition; approval records
are not offline authorization tokens or substitutes for a live release/tag gate.

For Cargo reproduction, pass both `reproduced_archive` and `reproduced_tools`.
The consumer creates that archive without publishing credentials. Verification
requires identical original archive SHA-256 and exact tool identity strings.
`verify_cargo_archive(path, package, version, commit, *, path_in_vcs="")` is also
available as a separate integrity check; it makes no CI or signature claim.
It reads bounded bytes without extracting or executing files: at most 64 MiB
compressed/expanded, 4,096 members and 1 MiB per metadata member. It rejects
links, sparse files, duplicate/unsafe member paths and mismatched Cargo metadata.
The outer artifact download additionally enforces its 32 MiB ZIP/expanded bound,
exact portable flat-file inventory and provider ZIP digest.

## Python distribution evidence

Use `package_kind="python"` with `guarantee="python-distributions"` and
`distributions=PythonDistributionPolicy(files, index_url="https://pypi.org",
max_age_seconds=2592000, max_distribution_bytes=268435456)`. In TOML these are
`package-kind` and a `[distributions]` table with `files`, optional `index-url`,
`max-age-seconds` and `max-distribution-bytes`.
`files` is an explicit list of filename templates with only `{version}`
substitution: exactly one normalized `.tar.gz` sdist and one or more wheels,
including consumer-selected native wheel tags. There are no filename globs.
Only canonical stable versions and static PEP 621 name/version are supported;
dynamic versions are rejected without executing build code.
`read_package_info(root, kind="python")` selects this identity directly, including
in mixed Cargo workspaces. `kind="cargo"` explicitly selects Cargo; omitted
`kind` preserves the existing behavior. A signed-source policy may also select
Python identity, without claiming distribution identity.

The producer receives `distributions=Path(...)` after every declared required
job/step has succeeded. Its manifest binds those job IDs and each distribution's
SHA-256. Retain `validation.json`, the exact distributions, and, if configured,
`validation.sigstore.json` in the flat `PREFIX-RUN_ID-ATTEMPT` artifact. The
evidence upload job may still be running during creation; verification requires
the complete run to succeed and every declared job to belong to that attempt.
Include build, all native installed-package tests and required security workflows
in the consumer's gates. Separate workflows must expose a trusted required gate
in this run; this API does not infer cross-workflow success.

Verification checks the original ZIP digest, bounded safe archive inventories,
unambiguous wheel/sdist core Name and Version, and exact file hashes. The full
source SHA is bound by the manifest and authenticated provider provenance, not
by an inherent wheel/sdist source field. It requires timezone-aware provider
run/artifact creation and expiration timestamps. The original push run must be
no older than `max-age-seconds`, the artifact must be created after that run and
before the current time, and its expiration must remain in the future. These
timestamps, destination and inventory policy are frozen with the selection.
Verification rechecks live state after download/signature work. Extending
retention, changing policy, attempts, jobs or artifact bytes requires new review
and a new handoff; it never updates an approved selection implicitly.

`max-distribution-bytes` bounds the combined retained distribution bytes, defaults
to 256 MiB, and accepts positive integers up to 2 GiB. Creation rejects an inventory
over this budget before returning a manifest; verification enforces the same
budget, which is frozen into approval records. Each wheel/sdist still has a 32 MiB
per-file bound. Python artifacts have a separate compressed ZIP bound equal to
the distribution budget plus 64 MiB for evidence and ZIP overhead; their expanded
contents may exceed the distribution budget only by the two 1 MiB evidence-file
allowances. The manifest and optional signature bundle are each bounded to 1 MiB.
The signed-source and Cargo artifact bounds remain 32 MiB.

With no signing job, Python mode uses authenticated GitHub Actions APIs and the
provider's immutable artifact ID/digest. It does not invoke attestation APIs,
claim a signature, or accept caller-provided JSON as authority. This supports
private repositories where the account can read Actions runs, jobs and retained
artifacts. Adding a signing job requires successful cryptographic verification
as well. GitHub currently requires Enterprise Cloud for private/internal
artifact attestations; see
[GitHub's availability documentation](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).

Pass the original selection as `expected` after approval. Pass the local
publication directory as `distributions` to compare its bytes with the retained
artifact; omitting it verifies remote evidence only. Consumers retrieve the
original distributions, keep exclusive control of those files, revalidate
immediately before token acquisition and upload them unchanged. There is no
rebuild fallback. Builds, upload credentials, tag gates and scientific checks
remain consumer-owned. For GitHub attachments, compose the separately captured
release-target handoff; evidence never recaptures or substitutes a draft ID.

`release_validation.verify_distributions(dist, tag, *, package="research-repo-tools",
filenames=None, max_total_bytes=None)` is the generic local integrity API. Set
`max_total_bytes` to require a combined distribution byte budget. `tag` is a version assertion
and does not require an existing Git tag. Omitting `filenames` selects a universal
wheel and sdist for that package; explicit filenames support multiple/native
wheels. The original two-argument tooling-package contract remains supported.
Archives are read without extraction/execution: each input is bounded to 32 MiB,
expanded content to 256 MiB, members to 10,000 and core metadata to 1 MiB.
Member paths use the shared extractor's portability checks, rejecting Windows
device names, Unicode and case aliases (including implicit directories), and
file/directory overlaps regardless of member order.

`registry.verify_published_distributions(package, version, files, *,
index_url="https://pypi.org")` requires hashes from verified evidence. It checks
the exact non-yanked inventory and package metadata through the destination's
[PyPI JSON API](https://docs.pypi.org/api/json/), then downloads each HTTPS file
and compares its size and SHA-256 with the assertions. Metadata and distribution
requests reject redirects before requesting their targets or consuming their
bodies. It also rejects credentials in URLs, missing/extra files and changed bytes.
The endpoint must implement PyPI's JSON contract; arbitrary Simple API indexes and authenticated
private registries are not supported. `registry.lookup_version(..., index_url=...)`
separately checks presence and metadata, without proving byte identity.

The installed `release evidence published` command requires `--expected`, runs
live evidence verification, checks published bytes at the captured destination,
and revalidates the same evidence again before saving the selection. It does not
upload, create a tag, rebuild or modify a draft. Saved selection JSON alone is
neither an offline publication authorization nor a permanent registry receipt.
Usage is in the [README](../README.md#releases). Downstream adoption requires the
exact supplying release to be published and its wheel/sdist and locked consumer
installation verified; source-only availability does not satisfy that gate.

## Shared primitives and asset composition

### Python registry approval

In v0.2.9, `PythonDistributionPolicy(..., index_url=None)` explicitly freezes an
undecided destination (`destination="undecided"` in TOML, mutually exclusive with
`index-url`). Evidence remains verifiable, with status and summaries identifying
it as not publishable. The original `published` command rejects this state.
Omitted fields retain the existing PyPI-bound default.

`python_publication.PythonRegistryAuthority(index_url, account, project,
repository, workflow, environment, protocol="pypi-json", authentication="none")`
declares a separately approved destination and publisher. Only unauthenticated
HTTPS PyPI JSON verification is supported; credentials in URLs, other protocols
and private authentication modes reject before requests. This protocol declaration
does not configure the consumer's upload credentials. `load_registry_authority`
loads the strict schema-1 policy shown in the [README](../README.md#releases).

`approve_python_publication(policy, version, commit, read, *, selection,
authority, distributions, approve=False)` returns deterministic approval bytes
only after explicit approval and live verification of the original selection and
local distribution inventory. `verify_python_publication` takes the same inputs
plus `approval` and optional `published=False`; it revalidates the frozen source,
run/attempt, gates, artifact, signature when configured, and exact local bytes.
`published=True` also checks the destination's exact inventory and downloaded
bytes, then revalidates evidence and local bytes again. No command requests
credentials, rebuilds, uploads or selects replacement evidence.

The versioned approval binds the SHA-256 of the original selection and all authority
fields. Callers independently supply and authenticate both authority and approval;
records are assertions, not offline authorization. Changed authority requires new
approval, and registry-bound evidence cannot change its endpoint. Retain exclusive
control of original files and verify immediately before credential acquisition.
After an uncertain upload, inspect with `published=True` before considering a
consumer-owned retry. CLI equivalents are `approve-python`,
`verify-python-approval` and `published-python` under `release evidence`.

### Authenticated tag-to-draft workflow

In v0.2.9, `draft_workflow.plan_release_draft(config, evidence_policy, source, *,
event, expected=None)` returns a deterministic inert handoff. `source` is an
independently observed `TagSource`; `event` must be `push`. Planning checks clean
source/package metadata, protected-default-branch ancestry, required checks and
signed native evidence, then freezes generated notes and the original selection.
Repeat with `expected` to retain that selection and reject changed inputs.
Planning enforces the writer's 1 MiB note/selection limits and the complete
handoff's 4 MiB bound before returning it.
The CLI `release assets plan` also requires independent `--repository` and writes
outside the observed checkout, revalidating before retaining the handoff.

`DraftHandoffArtifact(run_id, attempt, artifact_id, sha256)` requires original
attempt 1 and independently trusted immutable artifact ID/digest. The shared
`prepare_authenticated_draft(root, policy, source, selected, *, output,
executing_run_id, executing_attempt, approve=False, recover_release_id=None)`
uses `TagPushPolicy` to authenticate the exact workflow, tag-only trigger,
repository, source, run/attempt, completed verification gates and artifact before
extracting the single `handoff.json`. The containing workflow may still run its
writer job. Both the ZIP and expanded handoff are bounded to 4 MiB.

The writer rejects execution within a checkout and composes the existing draft
writer with repeated producer, source and check revalidation. It reserves the
original `producer.json`, `handoff.json` and first `draft-receipt.json` together
after pre-effect checks, before any creation request. Failed pre-effect checks
leave no incomplete state directory. Final `target.json` retention rechecks the
same draft after producer verification. Receipt and target formats are unchanged.
Fresh state requires the original execution's
first attempt and unambiguous retained tag-run history. Recovery restores all
original state, including its journal, and reuses the captured ID. Missing journals,
changed producers, expired artifacts and deleted/replaced drafts reject without
creating replacements. The [two-job guide](PREPARING_DRAFTS.md) covers CLI arguments,
durability, serialization, ambiguous POST recovery and consumer migration.

### Tag-push receipt retrieval

The v0.2.7 module `research_repo_tools.release_receipts` retrieves the original
successful preparation output. It leaves `ValidationPolicy`, `select_main_run`
and `validate_run` as main-branch evidence contracts; dotted tags are never
recast as branches. It shares their job/artifact identity checks and bounded ZIP
transport without inheriting main-push recovery instructions.

`TagSource(ref, commit)` requires `refs/tags/vX.Y.Z` and a full lowercase source
commit. The remote tag must peel to that commit; lightweight and annotated tags
are supported. `TagPushPolicy(repository, workflow, workflow_name, required_steps,
artifact_prefix="draft-receipt", runner_labels=())` declares exact workflow and
gate identities. Only original attempt 1 and the inert `target.json` /
`draft-receipt.json` inventory are supported. The artifact name is
`PREFIX-RUN_ID-1`. `release_interfaces.load_receipt_policy(path)` loads the
[corresponding TOML policy](PREPARING_DRAFTS.md#retrieve-original-preparation-output).

`retrieve_draft_receipt(policy, source, read, *, selection=DiscoverOriginal())`
returns `DraftReceipt(target, selection, target_bytes, receipt_bytes)`. `target`
is the existing `ReleaseTarget`; the byte fields preserve the authenticated
files exactly. The result record's constructor itself grants no trust.
The selection modes are distinct immutable variants:

| Mode | Contract |
| --- | --- |
| `DiscoverOriginal()` | Require one retained push run for this exact tag across unfiltered, bounded workflow history; no newest-run heuristic |
| `OriginalRun(run_id)` | Explicit manual selection of an independently reviewed original run; retain every identity/gate/attempt/artifact check |
| `FrozenReceipt(selection_bytes)` | Revalidate the saved original IDs and full selection; never search for another run or artifact |

`read(endpoint)` must be authenticated and scoped to the declared repository;
`partial(release_validation.api, repository=policy.repository)` supplies it.
The existing `download_artifact` transport authenticates the original ZIP bytes
using `gh`, its provider digest and exact inventory. No extracted file is executed.
ZIP/expanded data is bounded to 2 MiB, with 1 MiB per JSON file; workflow source
and saved selection each have a 1 MiB limit.

Run identity binds repository/head repository IDs and names, exact source commit,
workflow ID/path/name, push event, canonical tag and attempt 1. Because GitHub's
short ref name alone cannot prove tag identity, retrieval also reads the workflow
at the asserted commit, verifies its blob bytes and requires a tag-only push
trigger. Mixed branch/tag workflows, ambiguous YAML and implicit push triggers
reject. It does not relax the main-push verifier to accept dotted branch names.

The versioned `research-repo-tools/draft-selection/v1` record freezes the complete
policy/source assertions, workflow blob/SHA-256, run identity, required job IDs,
artifact ID/name/size/provider SHA-256, and both receipt-file SHA-256 values.
Frozen inputs are parsed before provider access, then checked against live
provider identities and downloaded bytes. Self-declared hashes are not authority.
Required steps must succeed, and the journal must be captured with the same
repository/tag/commit/title/release ID as the target. Provider state is checked
again after download; initial automatic discovery also rechecks uniqueness.

Persist all returned bytes under exclusive control outside the consumer checkout.
On retry, use the original bytes:

```python
from functools import partial
from research_repo_tools.release_receipts import FrozenReceipt, TagSource, retrieve_draft_receipt
from research_repo_tools.release_validation import api

# policy and source assertions are independently trusted consumer inputs.
source = TagSource("refs/tags/v1.2.3", observed_commit)
read = partial(api, repository=policy.repository)
first = retrieve_draft_receipt(policy, source, read)
again = retrieve_draft_receipt(policy, source, read, selection=FrozenReceipt(first.selection))
assert again.target == first.target
```

This API is read-only against GitHub and returns data without writing persistent
consumer files. Missing/failed/ambiguous/expired or changed selections raise
`ValueError`; provider errors retain their transport category. There is no
automatic retry, alternate selection, draft creation, benchmark acquisition or
publication. Historical workflow data is not a permanent intent ledger and cannot
prove that deleted history never existed. Consumers retain original selections
and own any stronger durable intent policy. Live checks are not an atomic GitHub
transaction or perpetual authorization; existing asset APIs revalidate the captured
release before effects.

The CLI `release assets retrieve` stores all three files transactionally, uses
an existing output selection automatically on retry, and refuses changed output
bytes. `--run-id` selects a reviewed original run manually; `--expected` restores
a frozen selection into a new output directory. They are mutually exclusive.

### Draft preparation

The v0.2.7 public function is
`release_drafts.prepare_release_draft(root, repository, tag, commit, *, policy,
expected, title, notes, receipt, approve=False, recover_release_id=None,
source_checks=())`.
It returns the existing `release_assets.ReleaseTarget`; serialize it with
`serialize_release_target` for later attachment/revalidation. The CLI is
`release assets prepare EVIDENCE_POLICY`, with independent `--repository`,
`--tag`, `--commit`, `--expected-title`, `--expected SELECTION`, `--notes`,
`--receipt`, `--output TARGET`, and mandatory `--approve`. It requires no checkout.
Retrieval uses its separate tag-push policy above; preflight/upload/verify retain
their existing asset-policy/checkout contracts.
Optional `source_checks` is a tuple of `RequiredCheck` values; when supplied,
preparation also rechecks protected-default-branch ancestry and exact source
checks around evidence verification. The authenticated composition supplies the
checks frozen by its read-only planner.

`policy` is an `EvidencePolicy` for the asserted repository with a signing job.
`expected` must be the original canonical selection bytes returned by
`verify_release_evidence`, authenticated separately by the consumer. Preparation
never chooses a replacement selection. Live verification uses that exact
source/run/attempt, jobs, artifact ID/digest and manifest/signature, including
current expiration and any configured freshness limits. Complete main-push and
signer requirements remain those of the existing evidence policy. This applies
to signed-source, signed Cargo archive and signed Python distribution policies;
preparation has no hard-coded package, registry, gate names or file inventory.
Signed-source verification does not establish byte-identical Cargo promotion.

`tag` is canonical stable `vX.Y.Z`; `commit` is a full lowercase SHA. `title` is
a required nonempty single line. `notes` is nonempty UTF-8 bytes without NUL,
bounded to 1 MiB, and is compared byte-for-byte, including CRLF and trailing
newlines. The selection is also nonempty bytes bounded to 1 MiB. Caller input
and receipts are parsed before provider mutations. Local receipt parent aliases
resolve to their native storage; the receipt itself cannot be a link/junction.

Successful complete authenticated paginated release lookup is the only proof
of absence. Provider/network/authentication/JSON/pagination errors fail closed.
Duplicate matches reject. An existing release must have the expected ID, tag,
title and exact body and be draft, non-prerelease and mutable; its assets remain
unchanged. The remote `refs/tags` identity is peeled through bounded annotated
tags to the asserted commit, so same-named branches cannot substitute. Evidence,
tag and release identities are rechecked around creation and before returning.

The durable receipt is schema `research-repo-tools/draft-receipt/v1`, binding
repository, tag, commit, title, notes SHA-256, exact selection SHA-256, state and
release ID. `pending` requires a null ID; `captured` requires a positive integer.
Unknown fields, duplicate JSON keys, invalid state/ID combinations and changed
bindings reject. Parsing is not authentication. Callers retain exclusive control
and authenticate receipts passed between jobs, including their immutable artifact
ID/digest and producer run/attempt.

Before POST, preparation exclusively creates and fsyncs a pending receipt. After
a valid creation identity is returned, it retains that ID before further network
reads. Retries with a captured receipt read the same ID and reject deletion or
replacement; they never create again. A matching existing draft can also be
captured without a POST. A pending receipt blocks automatic retry, even if lookup
now shows a matching draft. After independent inspection, provide
`recover_release_id=ID` / `--recover-release-id ID` with the original pending
receipt; recovery revalidates that exact ID, body, lifecycle, tag and evidence,
then captures it without release mutation. It cannot replace a captured ID.

Invalid assertions/current prerequisites raise `ValueError`; provider read
errors retain their transport category. `DraftPreparationUnknownError` signals
an unconfirmed attempted creation or an unresolved pending receipt. A creation
may have succeeded despite this exception. Never discard the receipt or rerun
with a new path to escape recovery. No automatic POST retry, edit, deletion,
asset overwrite, publication or tag movement is performed. Filesystem failures
also require inspecting retained state. Receipt writes are synced and replacements
use the shared file publisher; they are not a distributed transaction or a guarantee
against lost disks, hard runner termination, or concurrent hostile writers.

GitHub provides no atomic conditional release creation spanning evidence/tag
checks and POST. The tag can race after the last check; competing writers can
create duplicates. Postchecks detect observable drift but cannot undo creation
or make it linearizable. Serialize all writers for the repository/tag with
non-cancelling concurrency and preserve the same receipt across jobs/reruns.
See the [workflow example and recovery procedure](PREPARING_DRAFTS.md).

### Asset batch staging

For several independently selected inert files, use
`release_staging.stage_release_assets(root, target, assets, *, journal,
destination, approve, checkout=None, dependencies=(), limits=ArchiveLimits(),
progress=None, verify_inputs=None)`. It never creates a draft, moves a tag,
measures, regenerates acceptance or publishes. `approve=True` authorizes asset
uploads to the independently captured mutable draft. The optional checkout
continues to enforce the clean source/tag/commit assertions at every live target
observation. An additional `verify_inputs` callback may guard controller inputs.
Standalone upload/revalidation APIs keep their full verification behavior.

The batch owns fresh entry/final dependency passes and local guards with
[bounded freshness](dependency-provenance-api.md). It still verifies live release
identity and lifecycle around every upload, downloads exact bytes, and checks
provider size, digest and unchanged asset ID. Up to 64 distinct portable names
are supported within the per-asset archive and aggregate content byte limits.
Inputs, journal and transfer must be distinct regular paths without links.
Retain both outputs outside the observed checkout under exclusive writer control.

The canonical `research-repo-tools/release-asset-batch/v1` journal binds the
captured target, input names/sizes/SHA-256 values, dependency input/runtime digest,
verified asset IDs/sizes/digests, and `staging` or `complete` state. The initial
intent is durable before uploads; each verified asset updates progress. Only
fresh final verification permits a complete journal and matching transfer to be
retained together. The digest binds local paths/environment as well as bytes;
recovery requires the original environment, not merely a relocated file copy.
The journal is a retained assertion, not authentication or a permanent registry
receipt. Authenticate it separately when transporting it between jobs.

On interruption preserve the journal, original files and draft. Repeat the same
`release assets stage` command with the same `--target`, repeated `--asset`,
`--journal` and `--output` arguments. Matching existing assets are downloaded
and verified without another upload. A lost upload response can therefore be
recovered from a uniquely matching asset. Recorded IDs cannot be replaced;
changed inputs, dependencies, receipts, duplicate names or ambiguous lifecycle
state stop recovery for explicit reconciliation. The original transfer is never
overwritten with conflicting evidence. Default human progress and `--progress
json` report the current asset and checked/total count. CLI examples are in
[README](../README.md#releases).

### Existing target and attachment commands

Consumer commands are in the
[task guide](VERIFYING_RELEASE_EVIDENCE.md#release-evidence-and-attachment-handoffs). Both Justfiles use
`release-evidence` for `release evidence` and `release-assets` for `release assets`.
Recipes forward arguments; stdout is human-readable status, never evidence.

`release evidence create` requires independent `--version`, `--commit`,
`--checkout`, `--run-id`, `--attempt`, and `--output`. It observes the clean
checkout/package and requires the executing GitHub repository, push event,
branch ref, SHA, run and attempt to match. Cargo mode also requires `--archive`
and `--tools`, a JSON object with independently captured `cargo` and `rustc`
identity strings. The workflow owns those producer commands and signing/upload
of the exact manifest. Creation never builds, signs, uploads a crate or gets
publication credentials.

`release evidence verify` observes the same checkout/version/commit, verifies
retained evidence, and saves `--output`. Pass a frozen selection with `--expected`
after approval, using a separate output path. Optional `--reproduced-archive`
and `--tools` require exact archive/tool identity. Run the live verification
immediately before token acquisition; saved JSON alone is not authorization.

Both commands optionally accept `--github-output PATH`. That file must already
exist, be writable and contain complete LF-terminated assignments or be empty.
Only after verification are `identity` (compact complete JSON) and `commit`
exported. `write_release_outputs(destination, payload, *, github_output=None,
values=None, verify=None)` stages command data through `ci.export_environment`
and publishes it with the handoff in one rollback-capable transaction. Existing
handoffs permit identical bytes only; changed selections require new paths.
Missing/unwritable output, changed inputs or failed replacement preserve prior
records on successful rollback; recovery errors identify retained backups.
Exclusive writer control is required; this is not crash-atomic across files.
Local verification omits `--github-output` and needs no Actions environment.

`load_asset_policy(path)` returns `AssetPolicy(repository, expected_title=None)`.
Schema-1 `release-assets.toml` accepts `repository` and optional
`expected-title = "tag"`. `release assets preflight` requires `--tag`, `--commit`,
`--checkout`, and `--output`, capturing release ID, tag, commit and title policy.
Optional Actions output contains `commit`, `tag`, and `release_id`. `verify`
rechecks a captured `--target` against those independent assertions. `upload`
also requires `--asset` and `--approve`; `--publish` separately permits publishing
the verified draft. Upload never silently recaptures its target. Existing direct
`performance release-draft` / `release-upload` interfaces remain supported.

Run trusted pinned installed tooling outside the observed checkout in workflows
that carry credentials. `--checkout` is separate from the runtime/configuration
root, and these CLI interfaces reject a runtime imported from that checkout.
This is not proof that an already-running interpreter, executable path or Git
configuration is trustworthy. Consumers retain clean environments, event/ref
gates, approval/OIDC policy and trusted handoff storage. They must not copy
arbitrary consumer Python into the trusted runtime.

`release_validation` also exposes `read_pages`, `parse_release_json`,
`artifact_identity`, `download_artifact`, `verify_attestation`, `select_main_run`,
`validate_run` and `validate_jobs`. Pagination is bounded to 100 pages and checks
provider totals when present. `artifact_identity` alone does not prove an attempt
or a signature. Prefer the complete evidence verifier for approval handoffs.
Policies snapshot job, step and runner-label sequences into immutable tuples;
scalar strings do not stand in for sequences. Workflow metadata must have a
positive integer ID and match the declared active workflow before run discovery.
Malformed workflow identities and hosted runner labels raise `ValueError`.
`redact_release_diagnostic(text_or_bytes)` removes URL user information, queries,
fragments and recognizable GitHub token forms from rendered diagnostics before
logging. It cannot recognize arbitrary secrets in free text.

Use the existing [release-target/asset API](workflow-api.md#release-assets) for
captured release ID/tag/commit and title/lifecycle checks. Its preflight,
revalidation and asset publication functions accept `checkout=Path(...)` to
observe a clean source checkout at the captured commit. The source path may be
separate from the trusted installed runtime's working directory. The exported
`require_release_checkout(checkout, commit)` performs the same read-only check.
It rejects assume-unchanged and skip-worktree index entries, including those in
checked-out submodules, and checks submodule changes regardless of ignore settings.
It leaves index flags and checkout contents unchanged.
Git is selected from absolute `PATH` entries outside the observed checkout.
The same absolute executable and filtered `PATH` are used for recursive submodule
checks; relative, empty, and checkout-owned entries cannot supply executables.
Inherited `GIT_*` environment overrides are removed so these observations use the
supplied checkout's repository, working tree, and index.
Run trusted installed tooling outside consumer source; these helpers execute no
consumer build commands, but cannot secure an interpreter already importing
untrusted code. Checkout observation also requires trusted Git configuration
(including filters and filesystem-monitor settings). Perform these checks before
credentials and keep that configuration and source stable throughout publication.

Existing assets are reused only when their provider metadata and downloaded
bytes match exactly; conflicting bytes reject and are never overwritten. This
is the shared retry policy, including for consumers that formerly rejected all
existing baselines. Recheck evidence and the captured release target before
privileged actions. A failed upload or uncertain publication must be inspected
before retrying; the existing API preserves its uncertainty error contract.

Native Cargo still repackages during publication. See the
[Cargo publication policy](cargo-publication-policy.md) for the exact-byte
promotion evaluation and preserved guarantees.
