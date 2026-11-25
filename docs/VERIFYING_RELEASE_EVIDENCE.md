# Verifying release evidence and attachments

Use this guide when composing trusted preflight and writer jobs. Follow your
consumer's adapted release procedure, using the
[packaged release template](../src/research_repo_tools/templates/RELEASING.md)
for the overall order and approvals. This guide describes current source;
check [release availability](../README.md#release-availability) before adopting
the documented interfaces. Exact-release-commit evidence is the default;
[CI reuse](REUSING_CI.md) is a separate explicit policy.

## Release evidence and attachment handoffs

For Cargo archives, `release evidence verify --expected SELECTION --retain-crate DIRECTORY` retains the authenticated original crate for staging.
Use `release evidence candidate --required-checks` to require the publishing policy's hosted checks before accepting the measurement and native selection.
Pass the frozen native selection digest to `release assets retrieve --expected-native-sha256 DIGEST` when retrieving the tag-push draft receipt.

After `release assets stage`, retain the complete transfer, its SHA-256, and the original target independently. A checkout-free receiver can use
`release assets receive --transfer TRANSFER --transfer-sha256 DIGEST --target TARGET --output DIRECTORY`. It observes the draft before and after
downloading the exact asset IDs and verifies their sizes and hashes. For a release whose complete inventory was staged together, supply
`--asset-transfer TRANSFER --transfer-sha256 DIGEST --target TARGET` to `release publish`. Publication checks the approved IDs immediately before
the request and checks the response. The original staging transfer binds each asset ID to its verified size and SHA-256. Publication reuses those
assertions and requires the current GitHub inventory to match every field, including the uploaded state, without downloading the asset payloads again.
Wait for every asset writer to finish and hold exclusive control of the draft through confirmation, including manual edits, uploads and other workflows.
GitHub does not offer an atomic conditional publication against an asset inventory. Inspect an uncertain result before attempting recovery.

Declare selected tooling under
`release.published-dependencies`. Pre-tag evidence and later approval/asset gates
check the observed checkout's dependency declarations and lock against the
executing runtime and published artifact bytes before provider operations or
handoff writes. Use `just release-dependencies` before an expensive campaign;
see [development-pin transitions and proof limitations](dependency-provenance-api.md).

Local builds and merged source do not establish package availability.
Install the verified supplying release before composing these jobs.

The packaged `release-evidence.toml` declares exact repository/workflow/native
job/step/signer policy; `release-assets.toml` declares repository and optional
release-title policy. Merge the `release-evidence` and `release-assets` recipes
into a separate trusted controller project. Run the examples below there, using
its installed tooling and policy files. Its uv environment must be outside
`/path/to/source`, the observed checkout, which must be clean at the independently
asserted commit and package version. The same recipes cannot verify the checkout
that contains their executing uv environment.

```sh
just release-evidence verify tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --output /trusted/selection.json
# After approval, before token acquisition:
just release-evidence verify tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --expected /trusted/selection.json --output /trusted/revalidated.json
just release-assets preflight tooling/release-assets.toml \
  --checkout /path/to/source --tag v1.2.3 --commit FULL_COMMIT \
  --output /trusted/target.json
just release-assets upload tooling/release-assets.toml \
  --checkout /path/to/source --tag v1.2.3 --commit FULL_COMMIT \
  --target /trusted/target.json --asset /path/to/baseline.tar.gz --approve
```

Add `--format summary` for a concise human-readable result after verification.
The summary states the actual package guarantee and labels absent benchmark or
signature checks explicitly. JSON handoff bytes and approval selections remain
unchanged; `--format json` also emits those bytes on stdout. For committed benchmark
histories, follow [the two-step handoff](REUSING_BENCHMARKS.md), including its
independently pinned publication receipt. Verification never acquires benchmarks
or repeats build/test validation.

For Actions, append `--github-output "$GITHUB_OUTPUT"` to evidence verification
or asset preflight. It requires an existing writable command file; normal and
quiet Just execution produce the same evidence. Evidence records bind the exact
run, attempt, jobs, artifact and digest/signature policy. Existing output records
can only be reused with identical bytes; use new paths for changed selections.

`release-evidence create` produces the manifest only in the matching trusted
push/run/attempt. Cargo evidence also consumes the independently retained archive
and tool identity JSON; signing, Cargo gates and archive reproduction stay in the
workflow. `release-assets upload` reuses identical existing attachments, rejects
conflicting bytes, and publishes the draft only with a separate `--publish` flag.
Neither interface acquires a registry token or runs scientific workloads. See the
[release evidence API](release-evidence-api.md) and
[migration map](performance-migration.md#release-interface-reconciliation).

## Verifying Python distributions

Use a separate trusted controller project with its own pinned tools and policies.
Set `package-kind = "python"` and `guarantee = "python-distributions"` in the
evidence policy to select the Python project, including beside a Cargo workspace.
Build once and run installed-package and security checks against the same files.

```toml
[distributions]
files = ["example_app-{version}-py3-none-any.whl", "example_app-{version}.tar.gz"]
index-url = "https://test.pypi.org"
max-age-seconds = 86400
```

In a subsequent job of the same push attempt, retain the manifest with the
validated distributions. Add a signature bundle when the policy selects a signer.

```sh
just release-evidence create tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --run-id RUN_ID --attempt ATTEMPT --distributions /path/to/dist \
  --output /trusted/validation.json
```

```sh
just release-evidence wait tooling/release-evidence.toml \
  --commit FULL_COMMIT --attempts 241 --interval 30
just release-evidence verify tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --distributions /path/to/dist --output /trusted/selection.json
# After approval, immediately before the consumer's publication step:
just release-evidence verify tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --distributions /path/to/dist --expected /trusted/selection.json \
  --output /trusted/revalidated.json
# After the consumer uploads those exact files to the configured destination:
just release-evidence published tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --expected /trusted/selection.json --output /trusted/published.json
```

Allow enough time for the producer and verification in the enclosing job. The
example allows two hours of retry delay. The final `published` check downloads
and compares the actual registry files. See the [distribution evidence contract](release-evidence-api.md#python-distribution-evidence).

### Approving a registry after capture

Select `destination = "undecided"` in place of `index-url` to defer the registry
decision. Bound evidence keeps its original endpoint. For undecided evidence,
retain the original selection and use a separate trusted authority declaration.

```toml
schema = 1
index-url = "https://test.pypi.org"
account = "publishing-account"
project = "example-app"
repository = "owner/repository"
workflow = ".github/workflows/publish.yml"
environment = "registry-approval"
protocol = "pypi-json"
authentication = "none"
```

```sh
just release-evidence approve-python tooling/release-evidence.toml \
  --authority tooling/registry.toml --commit FULL_COMMIT --version 1.2.3 \
  --distributions /path/to/dist --expected /trusted/selection.json \
  --output /trusted/approval.json --approve
# Immediately before the consumer requests credentials:
just release-evidence verify-python-approval tooling/release-evidence.toml \
  --authority tooling/registry.toml --commit FULL_COMMIT --version 1.2.3 \
  --distributions /path/to/dist --expected /trusted/selection.json \
  --approval /trusted/approval.json --output /trusted/verified-approval.json
# After upload, including an uncertain outcome, inspect before any retry:
just release-evidence published-python tooling/release-evidence.toml \
  --authority tooling/registry.toml --commit FULL_COMMIT --version 1.2.3 \
  --distributions /path/to/dist --expected /trusted/selection.json \
  --approval /trusted/approval.json --output /trusted/published-approval.json
```

Verify the authority and the original bytes before requesting credentials. A new
endpoint or publisher requires new approval. After an uncertain upload, check the
registry before retrying. See the [supported registry protocol](release-evidence-api.md#python-registry-approval).

## Staging assets on a captured draft

Use an asset policy with `schema = 1`, the exact `repository`, and
`expected-title = "tag"`. Run from the observed checkout with a trusted Python
installation outside it. On Windows select `Scripts/python.exe`.

```sh
/trusted/release-tools/bin/python -I -m research_repo_tools release assets stage tooling/release-assets.toml \
  --checkout . --commit FULL_COMMIT --tag v1.2.3 \
  --target /durable/target.json --asset /durable/final-policy.json \
  --asset /durable/accepted.json --asset /durable/benchmark.tar.gz \
  --journal /durable/staging.json --output /durable/transfer.json --approve
```

Retain the journal and original files across retries. The stage command verifies
existing attachments and rejects conflicting identities or bytes. It leaves the
draft unpublished. See the [batch contract](release-evidence-api.md#asset-batch-staging).

## Complete-run recovery

Pending evidence requires waiting for the selected run to complete and then
retrying. Use the shared `release evidence wait` command before the verifier or
draft planner, with an explicit polling budget sized for the complete producer
workflow and sufficient additional time in the enclosing job. The
[distribution verification example](#verifying-python-distributions) shows the
wait followed by strict verification. A pending timeout preserves the original
run and does not require rerunning main CI. Failed, cancelled, incomplete,
expired or ambiguous evidence rejects;
no PR, dispatch, local rebuild or different commit is selected as a fallback.
For a recoverable exact push within the provider's rerun window and the policy's
age limit, rerun **all jobs**, including the original distribution producer and
every required native/security gate. A failed-jobs-only rerun cannot supply a
complete attempt. The new complete attempt must create a new attempt-specific
artifact and manifest, followed by a new selection and approval. An existing
approved handoff intentionally rejects the changed attempt.

When the original push is stale, unavailable or outside the rerun window, follow
the consumer's reviewed new-commit procedure and obtain fresh complete push
evidence. Never change a published release or silently refresh a selected
artifact/draft identity. Diagnose duplicate runs/artifacts and failed required
steps before retrying. Python distribution policy, private-repository provenance
limits and published-byte verification are described in the
[API contract](release-evidence-api.md#python-distribution-evidence), with
[consumer commands](#verifying-python-distributions) above.

## Trusted release target handoff

Install the same exact published pin in trusted preflight and writer jobs. With the installed console command
on PATH, preflight
captures the expected workflow commit and a title-equals-tag assertion:

```sh
research-repo-tools --root "$RUNNER_TEMP" performance release-draft \
  "$GITHUB_REPOSITORY" "$RELEASE_TAG" --commit "$GITHUB_SHA" \
  --expected-title "$RELEASE_TAG" --output "$RUNNER_TEMP/release-target.json"
```

Omitting `--output` prints only target JSON on stdout. Pass this target through a
consumer-controlled trusted job output or artifact, separately from benchmark
output. The benchmark job retains read-only permissions and owns its correctness
checks and archive format. The final writer reads the original trusted target and
inert asset, then explicitly requests publication:

```sh
research-repo-tools --root "$RUNNER_TEMP" performance release-upload \
  "$GITHUB_REPOSITORY" "$RELEASE_TAG" "$RELEASE_ASSET" \
  --target "$RUNNER_TEMP/release-target.json" --publish
```

Without `--publish`, attachment and verification leave the draft unpublished.
Without `--target`, the existing upload command captures a fresh target inside
that invocation and cannot bind an earlier benchmark run. The old `release-draft`
command without `--commit` still checks draft state and emits no target. Output
and title assertions require `--commit`. These commands require authenticated
`gh`; consumers own token permissions, workflow/ref policy and fresh-preflight
run-attempt checks. JSON parsing alone does not authenticate the handoff.

The same supported Python API can run in a trusted job containing only inert files:

```python
from pathlib import Path

from research_repo_tools.release_assets import (
    parse_release_target,
    preflight_release_target,
    publish_release_asset,
    serialize_release_target,
)

root = Path("/trusted/job")  # Choose the native job directory on each platform.
target = preflight_release_target(root, "example/project", "v1.2.3", "a" * 40, expected_title="v1.2.3")
(root / "release-target.json").write_bytes(serialize_release_target(target))
# After the consumer's trusted handoff and benchmark gates:
captured = parse_release_target((root / "release-target.json").read_bytes())
publish_release_asset(root, captured.repository, captured.tag, root / "baseline.tar.gz", target=captured, publish=True)
```

An invalid or unavailable publication response reports an unknown outcome.
Inspect the captured release ID and attached asset before retrying; no rollback
or automatic publication retry occurs. The [API contract](workflow-api.md#release-assets)
details validation, retry guarantees and the limits of client-side rechecks.
The [la-stack deletion map](performance-migration.md#captured-release-target-adoption)
keeps downstream migration tied to an exact published package and integration checks.

Only when no local executable modules remain and the consumer neither installs
nor distributes its own Python package, remove its build backend, console scripts
and self-referencing notebook extra. Set `[tool.uv] package=false`,
retain exact shared pins in tooling/notebook dependency groups, refresh the lock,
and remove local wheel/entry-point tests. Rust/scientific and configuration tests
remain. Declare `release.tag-policy="canonical-stable"` where `vX.Y.Z` is required;
use `just update-version TAG --previous-release PREVIOUS --date DATE` for offline release preparation.
Version-independent documentation examples avoid release metadata callbacks.

## Release benchmark jobs

Release benchmarks use three jobs: validate a mutable draft, measure a checked-out
tag with read-only permissions, then attach/publish inert bytes in a separate
writer job. The measurement recipe invokes `performance baseline benchmark.toml
v1.2.0 project-v1.2.0-baseline.tar.gz`; it requires HEAD and configured source bytes
to match the tag. Writer jobs install the exact published package and use the
[captured release target](#trusted-release-target-handoff) to attach and publish
without checking out benchmark code. Identical completed attachment retries are
accepted; conflicting bytes or incomplete provider metadata fail.
