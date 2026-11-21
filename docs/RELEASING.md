# Releasing research-repo-tools

1. Prepare and review a dedicated release PR. Generate the changelog and pass the
   local release checks before pushing the branch.
2. After merge, wait for the exact main-push CI run to pass. Create the annotated
   tag for the reviewed commit, then push it explicitly.
3. Review the draft and its retained evidence. Publish the GitHub Release from
   a human GitHub CLI session.
4. Approve the protected PyPI deployment after its verification job passes.
5. Verify the exact published version on PyPI and in a fresh consumer. Record
   the commit, run and attempt IDs, artifact IDs and digests, and consumer
   results in the release tracker.

## Prepare the environment

Set `TAG=vX.Y.Z`. Check `gh auth status` and verify [Trusted Publishing](#configure-trusted-publishing-before-the-next-release).
Start from a clean, current `main` with the [development environment](../CONTRIBUTING.md#development-environment)
and pinned `git-cliff` available. Run `just changelog-setup` if the generator is
missing. Land tool and dependency updates separately before starting the
release PR.

## Step 1: Prepare the release PR

```bash
git switch -c "release/$TAG"
just update-version "$TAG"
just changelog-release "$TAG"
```

Review the synchronized package, lock, citation, and generated changelog
changes. The update selects the previous stable published release and prepares
a UTC citation date. Generation reuses that prepared date on retries. To change
it deliberately, pass `--date YYYY-MM-DD` to `just update-version` and rerun
generation. See [metadata](UPDATING_RELEASE_METADATA.md) and
[changelog generation](GENERATING_CHANGELOGS.md).

```bash
just release-dependency-builds
just release-dependencies
just release-version-check
just security
just ci
```

The dependency checks require published sources and authenticated receipts for
the configured local builds. Repeat preparation when its inputs change.
`just ci` includes the build, strict PyPI metadata check, and isolated wheel
and sdist tests. See [local validation](VALIDATING.md) and
[source-built dependencies](../README.md#source-built-python-dependencies).

Stage and commit only the reviewed release artifacts. Complete the local review
required by [AGENTS.md](../AGENTS.md), then push the branch and open
`chore(release): release $TAG`. Include validation results and resolve review
findings before merging.

## Step 2: Publish after the PR is merged

### 1. Synchronize `main` and wait for CI

From a clean checkout of the reviewed main commit, use its full SHA as
`RELEASE_COMMIT`. Enter the matching run ID from the list and require the
complete run to pass.

```bash
gh run list --workflow ci.yml --event push --branch main --commit "$RELEASE_COMMIT" --limit 20
read -r -p "Main CI run ID: " MAIN_RUN_ID
gh run watch "$MAIN_RUN_ID" --exit-status
```

### 2. Create and verify the annotated tag

```bash
just tag "$TAG" --commit "$RELEASE_COMMIT"
```

Success prints the tag, commit, and annotation or changelog link. The command
requires clean source at the reviewed commit, matching release metadata and
published dependency evidence before creating the tag. Use
`just tag-preview "$TAG" --commit "$RELEASE_COMMIT"` for optional inspection.

### 3. Push the tag

```bash
git push origin "refs/tags/$TAG"
```

The push starts `Prepare GitHub release` for this tag and commit.

### 4. Verify the release commit

```bash
just release-evidence
```

Record the verified main run and attempt, distribution hashes, and original
artifact IDs and digests. The recipe derives the tag and commit from the clean
checkout, verifies the already-pushed tag, and inspects retained signed CI
evidence. See [reusable CI evidence](#reusable-ci-evidence).

### 5. Wait for the draft GitHub release

Enter the matching preparation run ID from the list.

```bash
gh run list --workflow prepare-release.yml --event push --branch "$TAG" --commit "$RELEASE_COMMIT" --limit 20
read -r -p "Preparation run ID: " PREPARATION_RUN_ID
gh run watch "$PREPARATION_RUN_ID" --exit-status
```

The workflow creates the original draft and attaches the validated wheel,
sdist, and `release-attestation.json`.

### 6. Review the draft

```bash
gh release view "$TAG" --json databaseId,tagName,name,isDraft,isPrerelease,body,assets
```

Record the draft ID. Require the expected stable tag and title, the three
complete uploaded assets, and the generated notes or their canonical changelog
link. Inspect the release in GitHub or use `just release-notes "$TAG"` to
review the notes separately.

Before publishing the draft, compare each attached wheel and sdist filename
and SHA-256 digest with the signed distribution records from
`just release-evidence`. Confirm that the attached `release-attestation.json`
identifies the same validation run and attempt. A missing or differing digest
must be reconciled while the release is still editable.

### 7. Publish the reviewed GitHub Release

```bash
just release-publish "$TAG"
```

Use a human GitHub CLI session. The published event starts `Publish to PyPI`.
Find and record the `publish.yml` run for the exact tag and commit.

### 8. Approve the PyPI deployment

The workflow confirms that GitHub marks the published release immutable, then
downloads its attached wheel and sdist and checks their hashes and signed
provenance. Once verification passes, approve
the protected `pypi` deployment. The uploader sends those exact files to PyPI.
Wait for the upload job to finish.

### 9. Verify registry availability and downstream resolution

```bash
just release-verify "$TAG"
```

Success confirms the stable GitHub Release, its required assets, and the exact
version on PyPI. Separately, download the wheel and sdist from that version's
PyPI files page. Compute the SHA-256 of each download and compare it with the
matching filename and hash recorded during `just release-evidence` before marking
the release verified. Automation can perform this comparison with the
[published distribution verifier](publishing-api.md#reviewed-registry-publication-api).
Install the version in a fresh project, run its focused consumer checks, and
record the results in the release tracker.
[Consumer installation](../README.md#install-with-uv)

### 10. Verify the Zenodo release metadata

If enabled, verify the version-specific record against `CITATION.cff`, including
date, authors, ORCID, and license. Record its version DOI in the release tracker.

### 11. Remove the merged release branch

After registry, consumer, and applicable Zenodo verification, remove the merged
release branch locally and on the remote.

## Choose where to resume

In a new shell, take `TAG`, `RELEASE_COMMIT`, and workflow run IDs from the
release tracker. Cross-check them with the exact tag, main-run listing, and
recorded draft. Keep the retained evidence associated with that candidate.

| Last completed operation | Resume with |
| --- | --- |
| Metadata prepared, notes missing or rendering failed | Fix the reported generator prerequisite and run `just changelog-release "$TAG"` with the prepared date. |
| Release PR merged, exact CI still pending | Watch the original `MAIN_RUN_ID`. |
| Tag preflight requires a tracked metadata correction | Commit and review the correction. Wait for main CI on the new commit and retry `just tag "$TAG" --commit "$RELEASE_COMMIT"` with its SHA. |
| Tag preflight stopped on an external dependency or tool | Resolve the reported prerequisite and retry `just tag "$TAG" --commit "$RELEASE_COMMIT"` with the same reviewed SHA. |
| Tag pushed, draft pending | Inspect or watch the recorded `PREPARATION_RUN_ID`. |
| GitHub Release published, PyPI approval pending | Review the verification result and approve the `pypi` deployment in the original publishing run. |
| PyPI upload interrupted | Inspect the original GitHub Release and publishing run. Check the exact registry version with `just release-registry "$TAG" --expect present` and compare any published file hashes before deciding whether to retry. |
| Unpublished candidate needs a different commit | Follow the [replacement procedure](RECOVERING_RELEASES.md#replace-an-unpublished-candidate). |
| Registry GET timed out | Set `RRT_REGISTRY_TIMEOUT_SECONDS=180` for the failing recipe and retry with the same tag, commit, and retained evidence. |

### Recovering a failed run

Inspect the failed operation and the actual GitHub and registry state. Use the
[shared recovery guide](RECOVERING_RELEASES.md) for partial drafts, uncertain
uploads, and unpublished corrections. Published corrections use a new version.

#### Preparation timed out waiting for main CI

Keep the original tag. Once the original complete main run passes, rerun the
recorded RRT preparation run's failed jobs and watch the new attempt:

```bash
gh run rerun "$PREPARATION_RUN_ID" --failed
gh run watch "$PREPARATION_RUN_ID" --exit-status
```

This retry is for an RRT preparation run that stopped while waiting for CI,
before draft creation. For an interrupted draft or asset write, first reconcile
the original draft and its inventory using the recovery guide.

#### Draft creation or upload failed

Staging writes `release-attestation.json` beside `dist/` before contacting
GitHub to create the draft or upload assets. It keeps the attachment if either
operation fails. On a hosted runner, download `draft-recovery-<attempt>` from
the failed preparation run when available. The artifact contains the attachment
for inspection.

Inspect the release for the tag, including its ID, draft state, asset names,
sizes, and digests. A transport error can occur after GitHub accepted a write.
If the release is still a draft and all three assets match the signed evidence,
continue with the draft review. For a published release, check the publishing
run and its registry approval.
For partial or conflicting assets, follow the [recovery guide](RECOVERING_RELEASES.md).
If the draft is absent or empty and the verified inputs still match, keep a
copy of the attachment before retrying. In a reused working directory, move
the inspected attachment aside first. Staging refuses to overwrite it.

#### Artifact retention and recovery window

The distributions and signed validation artifacts are retained for 30 days.
Within GitHub's rerun window, rerun **all** jobs of the original exact main-push
run with `gh run rerun "$MAIN_RUN_ID"` to recover missing evidence. Wait for
`release-evidence` and record the replacement attempt and its artifact
identities. If the draft is absent or empty, retry preparation for that evidence.
For a nonempty draft, compare the retained assets and signed attestation with
the replacement attempt before publishing. Follow the [recovery guide](RECOVERING_RELEASES.md)
to reconcile stale attachments. For an immutable release, use the published
correction procedure. When the original main run is no longer eligible, prepare
a new reviewed candidate and use the unpublished replacement procedure.

## Reusable CI evidence

The `Package checks` workflow builds the distributions once and tests them on
Linux, macOS, and Windows. The complete main-push attempt signs the original validation evidence.
The tag workflow verifies it and signs promotion of the same bytes. PyPI
publication checks both signatures, the live run and artifact identities, and
the published release event before deployment approval.
[Evidence contracts and commands](VERIFYING_RELEASE_EVIDENCE.md)

## Configure Trusted Publishing before the next release

Register the GitHub trusted publisher for owner `acgetchell`, repository
`research-repo-tools`, workflow filename `publish.yml`, and environment
`pypi`. Verify the deployment reviewer policy and human CLI permissions before
release. For Zenodo, enable the GitHub integration before publishing.
[GitHub release configuration](CONFIGURING_GITHUB.md#release-configuration)
