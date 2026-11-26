# Releasing

## Contents

1. [Set the release tag](#step-1-set-the-release-tag)
2. [Prepare the release PR](#step-2-prepare-the-release-pr)
3. [Publish after the PR is merged](#step-3-publish-after-the-pr-is-merged)
   - [Open the run and approve publishing](#8-open-the-publishing-run-and-approve-the-registry-deployment)
   - Verify [crates.io](#rust-crates-on-cratesio) or [PyPI](#python-packages-on-pypi)

**Resuming a release?** [Choose where to resume](#choose-where-to-resume).

## Step 1: Set the release tag

Set the target tag first, replacing `X.Y.Z` with the intended version.

```bash
TAG=vX.Y.Z
```

Start from clean, current `main`. Use the same Bash or zsh session throughout and check `gh auth status`.
Use `just setup` for the locked development tools. Land any tool or dependency
updates separately before preparing the release PR.
[Consumer setup](https://github.com/acgetchell/research-repo-tools/blob/main/README.md#install-with-uv)

Declare `published-dependencies = ["research-repo-tools"]` in
`[tool.research-repo-tools.release]`. Use the exact verified PyPI release in
the manifest, lock, and isolated runtime. Full Git SHAs are available for ordinary
CI and rehearsal. Use `--release-intent` when preparing or acquiring release
performance evidence.
[Supplier adoption and release checks](https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md)

## Step 2: Prepare the release PR

```bash
git switch -c "release/$TAG"
just update-version "$TAG"
just changelog-release "$TAG"
```

For a first stable release, use `just release-first "$TAG" YYYY-MM-DD` in place
of `update-version`. The changelog generator uses the prepared citation date.
Review the metadata, lock, changelog, and generated archive diff.

For a Cargo project, prepare the locked graph with `just release-cargo-fetch`
before checking dependencies or acquiring release benchmarks. If the Cargo
project lives in a subdirectory, pass `--manifest-path PATH` for its manifest.
When there is no root `Cargo.toml`, set `release_cargo := "true"` in the Justfile.
The managed release commands use the same cache and root toolchain declaration.

```bash
just release-dependency-builds
just release-dependencies
```

When release performance evidence is required, run the repository's declared
acquisition and report commands after these dependency checks. Keep the original
acquisition, checkpoints, publication receipt, and digests outside the checkout.
Include the generated report in the release PR.
[Benchmark acquisition and recovery](https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_BENCHMARKS.md)

Stage the reviewed release metadata and generated artifacts. Commit them with
the subject `chore(release): prepare $TAG`, substituting the release tag.

```bash
just release-version-check
just ci
```

Require verified published dependencies, synchronized metadata and dated
notes, and the repository's packaging, platform, and scientific checks.
Run the repository's local security gate on this commit.

If your review policy assigns one archived raw payload to offline preflight,
verify it on the clean preparation commit using the original owner receipts
and independent digests. Use the linked worktree procedure to exclude only
`<report archive>/runs/<verified identity>/run.json` from local branch review.
Every sibling must be reviewed. An in-scope skip blocks the gate.
[Reviewing a large archived payload](https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_BENCHMARKS.md#reviewing-a-large-archived-payload)

Run the repository's local branch review on the preparation commit. If a
correction changes the candidate, commit it and repeat the validation and
review gates on the final commit. Once the gates pass, push the branch and
open the release PR:

```sh
git push -u origin "release/$TAG"
gh pr create --base main --head "release/$TAG" --title "chore(release): release $TAG"
```

Include validation results and links to any separately retained evidence in the PR.

## Step 3: Publish after the PR is merged

### 1. Synchronize `main`

From a clean checkout, update your local `main` after the release PR merges.

```sh
git switch main && git pull --ff-only origin main
```

In GitHub **Actions**, open the push to `main` for the release PR you just merged.
Wait for the complete validation run to succeed, including the package producer
and native jobs. Confirm the commit shown on the run matches your local checkout.
If your pull brought in a later commit, [check out the release commit][release-checkout].

If benchmarks are required, verify and save acceptance for this checkout using
the original acquisition and publication receipts and the repository's external trusted runtime.
[Verifying release evidence](https://github.com/acgetchell/research-repo-tools/blob/main/docs/VERIFYING_RELEASE_EVIDENCE.md)

### 2. Create and verify the annotated tag

```bash
just tag "$TAG"
```

Success prints the local tag, reviewed SHA, and annotation or canonical
changelog link. Use `just tag-preview "$TAG"` from the same checkout for
optional inspection. For a cold managed Cargo cache, run `just release-cargo-fetch`
and retry the same tag and commit. Projects with additional independent Cargo
roots can pass `--manifest-path PATH` to fetch each declared lock.

### 3. Push the tag

```bash
git push origin "refs/tags/$TAG"
```

### 4. Verify the release commit

Use the repository's evidence workflow or `just release-evidence` wrapper with
its reviewed selection. Record the run, attempt, artifact IDs, and digests for
the exact tagged commit.

### 5. Create the draft GitHub release

In GitHub **Actions**, open the preparation run triggered by your tag push.
Confirm it shows **Success** before retrieving the draft. If it produces RRT draft
receipts, follow the [receipt retrieval command][draft-receipt]
from this release checkout with the same `TAG`. It saves the original run,
attempt, draft ID and commit outside the checkout. A retry revalidates those
same records. If your workflow supplies its own authenticated target, use the
original file it produced. For workflows that only create a draft, capture it
once using the [release target handoff][release-target].
Set `RELEASE_TARGET` to the saved target file for the remaining steps.

[draft-receipt]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PREPARING_DRAFTS.md#retrieve-original-preparation-output
[release-checkout]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RECOVERING_RELEASES.md#check-out-the-release-commit
[release-target]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/VERIFYING_RELEASE_EVIDENCE.md#trusted-release-target-handoff

### 6. Review the draft

Open the draft for your tag in GitHub **Releases**. Confirm the title and
read the notes or linked changelog as they will appear to users. Under
**Assets**, check that every required attachment is uploaded and nonempty.
Compare the names and SHA-256 digests with the retained validation evidence.
[Command-line draft checks](https://github.com/acgetchell/research-repo-tools/blob/main/docs/RECOVERING_RELEASES.md#inspect-a-draft-from-the-command-line)
If a required file is missing or differs, leave the draft unpublished.
If the repository attaches benchmarks separately, run its attachment recipe
with the original acceptance and captured draft target before approving the inventory.

For the [Cargo publication workflow template](https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/publish-crates.yml),
enable immutable releases and attach the exact `PACKAGE-VERSION.crate` retained
by validation to this draft. Include its name pattern in `publishing.required-assets`
so `just release-publish` requires it at the GitHub approval step.

### 7. Publish the reviewed GitHub Release

```bash
just release-publish "$TAG" --target "$RELEASE_TARGET"
```

Preserve any required receipt and acceptance arguments from the repository's
publication policy. Publish from a human GitHub CLI session. For repositories
that dispatch the registry workflow explicitly, use the same tag and original
approved handoff.

### 8. Open the publishing run and approve the registry deployment

In GitHub Actions, open the publishing run for the reviewed tag and commit.
If the repository dispatches publication explicitly, open that dispatch run.

| Destination | Workflow run | Protected environment to select |
| --- | --- | --- |
| Python package on PyPI | **Publish to PyPI** | `pypi` |
| Rust crate on crates.io | **Publish to crates.io** | `crates-io` |

Use the names configured in your repository if they differ. Once verification
passes and the upload job requests approval, click **Review deployments**,
select the environment above, then click **Approve and deploy**. Wait for the
upload job to succeed before verifying the registry version.
[GitHub deployment review](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments)

### 9. Verify registry availability and downstream resolution

```bash
just release-verify "$TAG" --target "$RELEASE_TARGET"
```

Require the published stable GitHub Release, its required assets, and the exact
registry version. Check the package in a fresh consumer and record the release,
workflow, artifact, and consumer identities after its registry checksum check passes.

#### Rust crates on crates.io

`cargo publish` packages the reviewed checkout for upload. The `--no-verify`
flag skips the verification build. The packaged workflow checks that the
immutable GitHub Release has the retained `.crate` before obtaining a registry
token. After upload, it compares the crates.io SHA-256 with the SHA-256 GitHub
records for that attachment. A mismatch fails the workflow. To repeat this check,
run `just release-verify "$TAG" --require-crate-asset --publication-only`.
The workflow checks current dependency yank status after deployment approval,
before requesting the token.

For a manual check from outside the checkout, set `REPOSITORY` to the GitHub
`owner/repo` and `PACKAGE` to the crates.io name, then run the installed CLI:

```sh
research-repo-tools release visibility "$TAG" \
  --repository "$REPOSITORY" --registry crates-io --package "$PACKAGE"
```

Pass `--target PATH` if you have the original captured release target. Compare
the printed `Registry archive SHA-256` with the SHA-256 of the retained `.crate`.
Require an exact match before recording the upload as verified.

#### Python packages on PyPI

The publication workflow downloads the wheel and sdist attached to the GitHub
Release, verifies them against signed evidence, and uploads those exact files.
Compare the published wheel and sdist with the approved hashes of those
attachments before recording the upload as verified.
[Published distribution verification](https://github.com/acgetchell/research-repo-tools/blob/main/docs/publishing-api.md#reviewed-registry-publication-api)

### 10. Verify the Zenodo release metadata

If enabled, verify the new version-specific record against the citation
metadata and record its DOI.

### 11. Remove the merged release branch

Remove the merged release branch locally and on the remote after registry,
asset, consumer, and applicable Zenodo verification.

## Choose where to resume

Set the release tag again and find the original push to `main` for the merged release or corrective PR.
Restore `RELEASE_TARGET` to the original captured file. Reuse the retained
owner directory, acceptance and receipt when running the same recipes in a new shell.

| Stop | Next operation |
| --- | --- |
| Notes failed after metadata was prepared | Fix the generator prerequisite and rerun `just changelog-release "$TAG"`. |
| Acquisition or rendering interrupted | Resume the repository's capture command with its original owner directory and evidence. [Benchmark recovery](https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_BENCHMARKS.md#release-recovery-decisions) |
| Local `main` has advanced past the merged release PR | [Check out the release commit][release-checkout] and wait for its push to `main` to pass CI. |
| Main CI pending or an external tag prerequisite failed | Wait for the release's main CI run or fix the prerequisite, then retry `just tag "$TAG"` from the reviewed checkout. |
| Tag preflight requires a tracked metadata correction | Merge the corrective PR, [validate and select its checkout](#1-synchronize-main), then retry `just tag "$TAG"`. |
| Draft retrieval or attachment interrupted | Reuse the original output directory with `just release-assets retrieve`, then retry the configured attachment recipe with that target. [Draft recovery](https://github.com/acgetchell/research-repo-tools/blob/main/docs/PREPARING_DRAFTS.md#retry-and-recovery) |
| GitHub Release published, registry approval pending | [Open the original publishing run and approve its registry deployment](#8-open-the-publishing-run-and-approve-the-registry-deployment). |
| Registry upload interrupted | Inspect the original GitHub Release and publishing run. Check the exact registry version with `just release-registry "$TAG" --expect present` and compare any published checksums before deciding whether to retry. |
| Registry verification timed out | Rerun the failing recipe with `RRT_REGISTRY_TIMEOUT_SECONDS=180` and the same candidate records. |

### Recovering a failed run

Preserve the original tag, evidence and draft while diagnosing a failure. Use
the [consumer recovery procedures](https://github.com/acgetchell/research-repo-tools/blob/main/docs/RECOVERING_RELEASES.md#consumer-workflows)
for failed workflow attempts, uncertain writes, and unpublished candidate
replacement. Corrections to already published contents require a new version.
