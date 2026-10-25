# Reusing verified source checks

Exact-release-commit CI remains the default. A consumer can explicitly reuse
signed source checks from a same-repository PR's synthetic merge checkout after
a squash merge, provided the tested tree and merge base match the release and
the checks implement the reviewed execution contract below. This capability is
part of the planned v0.2.4 release; use it in consumers only after that version is
published, normally installed and integration-tested.

This does not enable reuse in this package's own workflows. Its build-once
wheel/sdist validation and promotion still require the exact release commit.
No timing saving is established by this contract.

## Trust and execution contract

Equal trees do not establish equal executions. The trust root is a maintainer's
review of an exact workflow definition and its execution contract, expressed in
`publishing.reuse`. Never obtain that policy from the evidence being verified.
The workflow must enforce all of these conditions on every reused job:

- Check the actual synthetic merge commit (`GITHUB_SHA` for the PR merge ref),
  with its two parents, and export its exact Git tree. Each check receives the
  exported tree without Git metadata, submodule indirections, LFS substitutions,
  untracked files, checkout filters or platform newline transformations.
- Run the same commands on each declared platform without access to PR/push
  event data, refs, commit identity, secrets or other ambient context. Use a
  fixed environment, working-directory layout, locale and clocks where relevant.
  Commit-dependent tests, version derivation and package construction are outside
  this mode.
- Provision and verify all tools and external inputs before the checks. Run the
  checks offline with immutable dependencies and no mutable caches or downloads.
  Bind command/helper bytes, the effective environment, complete input inventory
  and toolchain contents using SHA-256. A version label or lockfile alone does
  not identify downloaded bytes or the effective environment.
- Observe the enforced execution outside the checked code, then collect one
  receipt per job. Prevent checked code from editing the observer, receipts or
  signing inputs. Pin external actions, reusable workflows and helpers used by
  that observer. Keep the signing job separate from the native checks.

The four per-job policy digests are `commands`, `environment`, `inputs` and
`tools`. Consumers own their canonical inventories and the workflow that
measures/enforces them. The shared verifier compares these exact identities and
the signed isolation observations; it does **not** sandbox arbitrary workflows,
infer context independence from YAML, or turn caller-supplied claims into proof.
If a consumer cannot establish this contract, keep its exact-commit CI.

This trust boundary follows GitHub's warning that attestation security depends
on a trusted builder whose execution cannot be influenced by caller inputs.
See [GitHub CLI attestation verification](https://cli.github.com/manual/gh_attestation_verify).
The [PR event reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
distinguishes the synthetic merge checkout from the PR head. They are recorded
separately here.

## Configuration and required checks

Keep every required check in `publishing.required-checks`, including exact-commit
package/artifact checks. Add an explicit `[publishing.reuse]` table in a standalone
configuration, or `[tool.research-repo-tools.publishing.reuse]` in `pyproject.toml`.
Its fields are:

| Field | Contract |
| --- | --- |
| `workflow`, `workflow-name` | Exact `.github/workflows/*.yml` path and displayed workflow name |
| `workflow-sha256` | SHA-256 of reviewed workflow file bytes; verified at both tested and release commits |
| `branch` | Protected default release branch; default `main` |
| `jobs` | Array of job tables with `name`, nonempty `steps`, hosted `labels`, and the four execution digests |
| `signing-job` | Table with exact `name` and nonempty `steps`, separate from reused jobs |
| `artifact-prefix` | Portable artifact prefix; default `ci-reuse` |
| `max-age-seconds` | Maximum age from run creation, including reruns; default 86400, bounded to 1–604800 |
| `app-id` | Expected check provider ID; default GitHub Actions, 15368 |

All job names are unique, all four digests are full lowercase SHA-256 values, and
every job has hosted runner labels. Linux (`ubuntu-*`), macOS (`macos-*`) and
Windows (`windows-*`) jobs are mandatory. For example, one entry in `jobs` has
this shape (replace each illustrative digest with a measured, reviewed value):

```toml
[[publishing.reuse.jobs]]
name = "source (ubuntu-24.04)"
steps = ["Check offline tree"]
labels = ["ubuntu-24.04"]
commands = "1111111111111111111111111111111111111111111111111111111111111111"
environment = "2222222222222222222222222222222222222222222222222222222222222222"
inputs = "3333333333333333333333333333333333333333333333333333333333333333"
tools = "4444444444444444444444444444444444444444444444444444444444444444"
```

The reused name/provider pairs must be a strict subset of required checks.
At least one check remains tied to the exact release commit; consumers must use
that path for their package/archive validation and other context-sensitive gates.
The reuse policy does not relax those gates or establish archive identity.

## Produce and retain evidence

`research_repo_tools.release_reuse` exposes `ReusePolicy`, `parse_reuse_policy`,
`create_reuse_manifest` and `verify_ci_reuse`.

`ReusePolicy(validation, workflow_sha256, contracts, signing_job,
artifact_prefix="ci-reuse", max_age_seconds=86400, app_id=15368)` is immutable.
`validation` is the existing `ValidationPolicy`; its branch identifies the
protected release branch. `contracts` is a tuple of `(job_name, digest_pairs)`;
each `digest_pairs` contains the four named SHA-256 identities above.
`parse_reuse_policy(table, repository)` parses the configuration table.

In the reviewed PR signing job, call
`create_reuse_manifest(policy, checkout, pull_request, run_id, attempt,
observations, read)`. It reads the real clean Git checkout and tree, checks the
workflow digest, run and completed native jobs, then compares the independently
observed receipts with the policy. It returns deterministic JSON bytes without
signing, running tests or writing files. The `read(endpoint)` callable supplies
authenticated, repository-scoped GitHub GET data, using the shared
`release_validation.api` transport if desired.

`observations` maps each required job name to:

```json
{
  "job_id": 123,
  "checkout": "<full synthetic merge commit SHA>",
  "tree": "<full Git tree SHA>",
  "mode": "offline-tree",
  "git_metadata": false,
  "event_context": false,
  "network": false,
  "commands": "<SHA-256>",
  "environment": "<SHA-256>",
  "inputs": "<SHA-256>",
  "tools": "<SHA-256>"
}
```

Do not construct receipts by copying policy values. They attest actual execution.
Sign the returned `reuse.json` with GitHub build provenance in the reviewed
workflow and retain its bundle as `reuse.sigstore.json`. Upload exactly these
two files in `PREFIX-RUN_ID-ATTEMPT`. The native jobs must all finish in this
attempt; a failed-jobs-only rerun cannot reuse earlier jobs.

Verification checks the cryptographic signature against the repository,
workflow, exact synthetic merge signer/source digest, `refs/pull/N/merge`,
`pull_request` trigger and run/attempt, denying self-hosted signers. It also
checks current job/step outcomes, hosted labels, latest checks, provider IDs,
check-suite/job relationships, artifact ID/digest/retention, freshness and the
merged PR's relationship to the protected release commit. It repeats provider
checks after downloading and verifying the evidence.

## Select, approve and revalidate

After the reviewed PR is squash-merged, use the full release commit SHA and an
explicit successful run/attempt. With uv installed and the user-level Just
command set up as described in [README](../README.md), use:

```sh
just release-reuse RELEASE_SHA --pull-request PR_NUMBER --run-id RUN_ID \
  --attempt ATTEMPT --output /path/outside/checkout/ci-approval.json
just release-publish TAG --reuse-evidence /path/outside/checkout/ci-approval.json
```

The first command is read-only on GitHub and writes a new selection file only
after successful verification. It refuses to overwrite an existing file and
rejects destinations inside the resolved checkout before querying evidence,
including paths through directory aliases. Save the file outside the release
checkout so it does not make that checkout dirty.
The second command retains the usual explicit approval of the reviewed draft.
It verifies the saved selection again on both preflight passes before publishing
the GitHub Release. It does not itself upload registry packages.

The equivalent API is `verify_ci_reuse(policy, release_commit, read, *,
pull_request=None, run_id=None, attempt=None, expected=None, now=None)`. Initial
selection requires all three identity arguments. Approval revalidation takes
only the saved bytes in `expected`; it cannot select another candidate. `now` is
an aware clock for deterministic integrations/tests; production gates use UTC.
The returned selection binds policy, tested/release commits, tree, PR, run/attempt,
job IDs, immutable artifact and manifest digest.

Pass those same bytes as `reuse_evidence=` to `check_reviewed_release` and
`publish_reviewed_release`. The deployment workflow must retain the selection
through its approval boundary and pass `--reuse-evidence FILE` to **both** calls
to `research-repo-tools release gate TAG`, including the call after environment
approval. A new selection after approval is not revalidation. Consumer workflows
remain responsible for transporting the saved record and exact release assets.

## Rejection and fallback

Without `--reuse-evidence` (or its API equivalent), all configured checks require
the exact release SHA even when a reuse policy is present. Configuration alone
never changes the gate. A present failed, cancelled, pending, skipped or ambiguous
release-commit check also remains blocking when reuse is requested.

Forks, PR-head checkouts, push/dispatch evidence, merge/rebase publication,
different trees or bases, unreviewed workflows, context-sensitive execution,
changed tools/inputs, missing platforms, stale evidence, partial reruns and
changes before approval reject. Reuse does not automatically search for older
evidence, dispatch CI, publish or weaken required checks. Run complete CI on the
exact release commit and use the ordinary release commands as the fallback.

Source reuse never authorizes promotion of a PR-built package containing another
commit. Native Cargo publication, `.cargo_vcs_info.json`, package metadata,
signed archive evidence and this package's wheel/sdist build-once promotion keep
their exact-release-commit contracts. See [release evidence](release-evidence-api.md)
and [Cargo publication policy](cargo-publication-policy.md). Real consumer
migrations remain separate from implementation and publication of this package.
