# Release evidence API

`research_repo_tools.release_evidence` provides the public core used to create,
verify and freeze exact-commit release evidence. Consumer deployment workflows,
Cargo commands, scientific checks, approval environments and token acquisition
remain consumer-owned. Installed `release evidence` and `release assets` commands
expose the foundation through strict declarative policies and file handoffs.
Working consumer adapters remain until the verified published supplying release
is pinned and its integration passes. See the
[migration map](performance-migration.md#release-interface-reconciliation).

[Verified CI reuse](REUSING_CI.md) is a separate, opt-in source-check contract.
The APIs on this page continue to require the exact release commit and preserve
the distinction between source validation and package/archive identity.

## Policy and guarantees

The CLI/configuration module is `research_repo_tools.release_interfaces`.
`load_evidence_policy(path)` reads schema-1 TOML into `EvidencePolicy`.
Required fields are `repository`, `package`, `workflow`, `workflow-name`,
`artifact-prefix`, `guarantee`, and `jobs`. Optional `branch` defaults to `main`,
and `path-in-vcs` defaults to the package root. Each `[[jobs]]` contains exact
`name`, nonempty `steps`, and optional `runner-labels`. `[signing-job]` contains
its separate `name` and `steps`. Use the packaged `release-evidence.toml` as a
scaffold, declaring the consumer's actual native/feature/Cargo gates.

`EvidencePolicy(validation, package, artifact_prefix, guarantee,
signing_job=None, path_in_vcs="")` is immutable. Its `validation` is a
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

Signed source evidence requires a separate `signing_job=(name, steps)`.
Cargo evidence may also declare one. Signatures always require the configured
repository/workflow, exact signer and source commit, branch ref, push trigger,
hosted signer and exact run/attempt invocation. `gh attestation verify` performs
cryptographic verification; parsing a bundle never grants trust.

The policy supports stable Cargo-style package names and `X.Y.Z` versions.
Workspace crates declare their canonical POSIX `path_in_vcs`; the root default
is the empty string. Artifact names are always
`PREFIX-RUN_ID-ATTEMPT`. This standard naming and the internal manifest bind the
attempt because GitHub's artifact association does not expose an attempt field.

## Creation and verification

`create_evidence_manifest(policy, version, commit, run_id, attempt, read, *,
archive=None, tools=None)` returns deterministic UTF-8 JSON bytes. It observes the
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
reproduced_archive=None, reproduced_tools=None)` returns deterministic JSON
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

## Shared primitives and asset composition

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
