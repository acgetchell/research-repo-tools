# Releasing

1. Prepare the version, generated notes, and any benchmark report in a
   dedicated release PR. Pass local validation and merge after review.
2. Require complete trusted main CI and any benchmark acceptance for the final
   commit. Create the annotated tag and push it explicitly.
3. Review the original draft and attached evidence. Publish the GitHub Release
   from a human GitHub CLI session.
4. Approve the protected registry deployment after verification passes.
5. Verify the exact published package and record downstream verification.

Follow the shared steps, then use the registry instructions for
[Rust crates on crates.io](#rust-crates-on-cratesio) or
[Python packages on PyPI](#python-packages-on-pypi).

## Prepare the environment

Set `TAG=vX.Y.Z` and start from clean, current `main`. Check `gh auth status`
and [Trusted Publishing](#configure-trusted-publishing-before-the-next-release).
Use `just setup` for the locked development tools. Land any tool or dependency
updates separately before preparing the release PR.
[Consumer setup](https://github.com/acgetchell/research-repo-tools/blob/main/README.md#install-with-uv)

Declare `published-dependencies = ["research-repo-tools"]` in
`[tool.research-repo-tools.release]`. Use the exact verified PyPI release in
the manifest, lock, and isolated runtime. Full Git SHAs are available for ordinary
CI and rehearsal. Use `--release-intent` when preparing or acquiring release
performance evidence.
[Supplier adoption and release checks](https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md)

## Step 1: Prepare the release PR

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

```bash
just release-version-check
just ci
```

Require verified published dependencies, synchronized metadata and dated
notes, and the repository's packaging, platform, and scientific checks.
Commit the reviewed release artifacts, push the branch after its review gates
pass, and open the PR. Record any separately retained evidence in the
release tracker.

## Step 2: Publish after the PR is merged

### 1. Synchronize `main`

Use a clean checkout at the reviewed merged commit. Save its full SHA as
`RELEASE_COMMIT`. Require the complete exact main-push run, package producer,
and native checks. If benchmarks are required, verify and save acceptance for
that commit using the original acquisition and publication receipts, and the
repository's external trusted runtime.
[Verifying release evidence](https://github.com/acgetchell/research-repo-tools/blob/main/docs/VERIFYING_RELEASE_EVIDENCE.md)

### 2. Create and verify the annotated tag

```bash
just tag "$TAG" --commit "$RELEASE_COMMIT"
```

Success prints the local tag, reviewed SHA, and annotation or canonical
changelog link. Use `just tag-preview "$TAG" --commit "$RELEASE_COMMIT"` for
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

Let the configured preparation workflow create the draft. Record its original
run, draft ID, and captured target. Use the repository's
`just release-assets retrieve` command with the original full SHA and output
directory to retrieve or reverify those records.
[Draft preparation and receipt retrieval](https://github.com/acgetchell/research-repo-tools/blob/main/docs/PREPARING_DRAFTS.md#retrieve-original-preparation-output)

### 6. Review the draft

Require the expected stable tag, title, generated notes, and complete required
assets. If the repository attaches benchmarks separately, run its attachment
recipe with the original acceptance and captured draft target.

For the [Cargo publication workflow template](https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/publish-crates.yml),
enable immutable releases and attach the exact `PACKAGE-VERSION.crate` retained
by validation to this draft. Include its name pattern in `publishing.required-assets`
so `just release-publish` requires it at the GitHub approval step.

### 7. Publish the reviewed GitHub Release

```bash
just release-publish "$TAG"
```

Preserve any required receipt and acceptance arguments from the repository's
publication policy. Publish from a human GitHub CLI session. For repositories
that dispatch the registry workflow explicitly, use the same tag and original
approved handoff.

### 8. Approve the registry deployment

Approve the protected deployment after verification succeeds, then wait for
the upload job. Use the registry-specific check below to confirm what it published.

### 9. Verify registry availability and downstream resolution

```bash
just release-verify "$TAG"
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

Restore the tag, reviewed SHA and original run IDs from the release tracker.
Reuse the retained owner directory, acceptance, receipt, and captured target
paths when running the same recipes from a new shell.

| Stop | Next operation |
| --- | --- |
| Notes failed after metadata was prepared | Fix the generator prerequisite and rerun `just changelog-release "$TAG"`. |
| Acquisition or rendering interrupted | Resume the repository's capture command with its original owner directory and evidence. [Benchmark recovery](https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_BENCHMARKS.md#release-recovery-decisions) |
| Main CI pending or an external tag prerequisite failed | Wait for the exact CI run or fix the external prerequisite, then retry `just tag "$TAG" --commit "$RELEASE_COMMIT"` with the same reviewed SHA. |
| Tag preflight requires a tracked metadata correction | Commit and review the correction. Wait for main CI on the new commit and retry `just tag "$TAG" --commit "$RELEASE_COMMIT"` with its SHA. |
| Draft retrieval or attachment interrupted | Reuse the original output directory with `just release-assets retrieve`, then retry the configured attachment recipe with that target. [Draft recovery](https://github.com/acgetchell/research-repo-tools/blob/main/docs/PREPARING_DRAFTS.md#retry-and-recovery) |
| GitHub Release published, registry approval pending | Review the verification result and approve the protected registry deployment in the original publishing run. |
| Registry upload interrupted | Inspect the original GitHub Release and publishing run. Check the exact registry version with `just release-registry "$TAG" --expect present` and compare any published checksums before deciding whether to retry. |
| Registry verification timed out | Rerun the failing recipe with `RRT_REGISTRY_TIMEOUT_SECONDS=180` and the same candidate records. |

### Recovering a failed run

Preserve the original tag, evidence and draft while diagnosing a failure. Use
the [shared recovery guide](https://github.com/acgetchell/research-repo-tools/blob/main/docs/RECOVERING_RELEASES.md)
for failed workflow attempts, uncertain writes, and unpublished candidate
replacement. Corrections to already published contents require a new version.

## Configure Trusted Publishing before the next release

Configure the registry publisher and deployment reviewers before starting.
Keep account setup in the maintainer's private checklist.
[GitHub release configuration](https://github.com/acgetchell/research-repo-tools/blob/main/docs/CONFIGURING_GITHUB.md#release-configuration)
