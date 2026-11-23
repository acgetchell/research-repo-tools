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

Set `TAG` to the intended new tag in `vX.Y.Z` format. Use the same Bash
or zsh session for the commands below. Check `gh auth status` and verify [Trusted Publishing](#configure-trusted-publishing-before-the-next-release).
Start from a clean, current `main` with the [development environment](../CONTRIBUTING.md#development-environment)
and pinned `git-cliff` available. Run `just changelog-setup` if the generator is
missing. Land tool and dependency updates separately before starting the
release PR.

## Step 1: Prepare the release PR

### 1. Prepare the release branch and metadata

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

Review the README installation pin, [upgrade instructions](UPDATING_DEPENDENCIES.md#shared-python-adoption), and consumer
command examples against the target package and its Justfile template. The
metadata updater leaves these Python examples for manual review. Keep versions
that identify when a feature first appeared or the minimum an interface requires.

### 2. Run the release checks

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
[source-built dependencies](UPDATING_DEPENDENCIES.md#source-built-python-dependencies).

### 3. Commit the release preparation

Stage only the reviewed release artifacts. Use the preparation commit subject
shown below. The changelog generator skips this subject when regenerating the notes.

```bash
git diff --cached
git commit -m "chore(release): prepare $TAG"
```

### 4. Run the local CodeRabbit review

```bash
just review origin/main
```

The review covers the full diff against `origin/main`. Wait for it to finish
and verify each finding against the release branch.

- For a release-preparation correction such as a typo in this guide, amend the
  unpushed preparation commit with its existing subject. The changelog stays
  current when the correction leaves its inputs unchanged.
- For a user-facing fix, make a separate commit with a descriptive subject.
  Then run `just changelog-release "$TAG"` so the notes include that commit.

Also regenerate if another changelog input changes, such as the prepared release
date. Review any generated diff and commit it as `chore(release): prepare $TAG notes`.
If the release metadata or generated notes have changed since the earlier check,
rerun `just release-version-check` on the final commit before pushing.
After any edits, run `just security`, `just ci`, and `just review origin/main`
again on the final commit before pushing.
See the [PR push requirements](../AGENTS.md).

### 5. Push the branch and open the release PR

Push the reviewed release branch to `origin`.

```bash
git push -u origin "release/$TAG"
```

After the push succeeds, open the PR against `main`.

```bash
gh pr create --base main --head "release/$TAG" --title "chore(release): release $TAG"
```

When GitHub CLI prompts for the PR description, include the release check and
local review results. Resolve review findings before merging.

## Step 2: Publish after the PR is merged

### 1. Synchronize main and wait for CI

Start with a clean checkout and set `TAG` in this terminal. The first lookup
gets the merge commit from the release PR. The second selects the CI run only
if exactly one push run matches that commit. Stop if either lookup fails.

```bash
git switch main
git pull --ff-only origin main
RELEASE_COMMIT=$(gh pr view "release/${TAG:?Set TAG first}" --json state,baseRefName,mergeCommit \
  --jq 'select(.state == "MERGED" and .baseRefName == "main") | .mergeCommit.oid')
MAIN_RUN_ID=$(gh run list --workflow ci.yml --event push --branch main --commit "${RELEASE_COMMIT:?Check the merged release PR}" \
  --limit 2 --json databaseId --jq 'select(length == 1) | .[0].databaseId')
gh run watch "${MAIN_RUN_ID:?Expected one main-push CI run for the merged commit}" --exit-status
```

If GitHub has not listed the run yet, repeat the lookup. If multiple runs match,
inspect their history before selecting one. Continue only after the exact run
passes. If `main` has advanced, use `git switch --detach "$RELEASE_COMMIT"`
to return to the reviewed commit before tagging.

### 2. Create the annotated tag

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

### 4. Verify the final release commit

```bash
just release-evidence
```

The summary includes the verified main run and attempt, distribution hashes,
and original artifact IDs and digests. Retain the summary with the release
tracker. The recipe derives the tag and commit from the clean
checkout, verifies the already-pushed tag, and inspects retained signed CI
evidence. See [reusable CI evidence](#reusable-ci-evidence).

### 5. Wait for the draft GitHub release

Look up the preparation run for the pushed tag and the same commit.

```bash
PREPARATION_RUN_ID=$(gh run list --workflow prepare-release.yml --event push --branch "${TAG:?}" --commit "${RELEASE_COMMIT:?}" \
  --limit 2 --json databaseId --jq 'select(length == 1) | .[0].databaseId')
gh run watch "${PREPARATION_RUN_ID:?Expected one tag-push preparation run}" --exit-status
```

If the run is not visible yet, repeat the lookup. Continue after it succeeds.
The workflow creates the draft and attaches the validated wheel, sdist, and
`release-attestation.json`.

### 6. Review the draft

Capture the draft once. The CLI saves the release ID, repository, tag,
title assertion and reviewed commit in a target file outside the checkout.
Keep that file for publication, verification and retries.

```bash
RELEASE_DIR="../release-handoffs/research-repo-tools/${TAG:?}"
RELEASE_TARGET="$RELEASE_DIR/target.json"
if [ ! -e "$RELEASE_TARGET" ]; then
  uv run --locked research-repo-tools performance release-draft acgetchell/research-repo-tools "$TAG" \
    --commit "${RELEASE_COMMIT:?}" --expected-title "$TAG" --output "$RELEASE_TARGET"
fi
gh release view "$TAG"
gh release view "$TAG" --json assets --jq '.assets[] | [.name, .state, .size, .digest] | @tsv'
```

Review the stable draft's title and generated notes (or canonical changelog
link). All three assets must be uploaded and nonempty.

Before publishing the draft, compare each attached wheel and sdist filename
and SHA-256 digest with the signed distribution records from
`just release-evidence`. Confirm that the attached `release-attestation.json`
identifies the same validation run and attempt. Download it for inspection with
`gh release download "$TAG" --pattern release-attestation.json --dir "$RELEASE_DIR"`.
The JSON in `evidence.manifest` contains the validation run and file hashes.
Reconcile any missing or differing digest while the release is still editable.

### 7. Publish the reviewed GitHub Release

```bash
just release-publish "$TAG" --target "$RELEASE_TARGET"
```

Use a human GitHub CLI session. The published event starts `Publish to PyPI`.
Open the URL for the matching run to review the protected deployment:

```bash
gh run list --workflow publish.yml --event release --branch "${TAG:?}" --commit "${RELEASE_COMMIT:?}" \
  --json status,conclusion,url
```

### 8. Approve the PyPI deployment

The workflow confirms that GitHub marks the published release immutable, then
downloads its attached wheel and sdist and checks their hashes and signed
provenance. Once verification passes, approve
the protected `pypi` deployment. The uploader sends those exact files to PyPI.
Wait for the upload job to finish.

### 9. Verify registry availability and downstream resolution

```bash
just release-verify "$TAG" --target "$RELEASE_TARGET"
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

In a new shell, set `TAG` again. [Step 2.1](#1-synchronize-main-and-wait-for-ci) looks up the merged PR's commit and
main run, and [Step 2.5](#5-wait-for-the-draft-github-release) looks up the tag's preparation run. After draft capture,
restore `RELEASE_TARGET` to the original target file in the release handoff directory.
Keep the retained evidence associated with that candidate.

| Last completed operation | Resume with |
| --- | --- |
| Metadata prepared, notes missing or rendering failed | Fix the reported generator prerequisite and run `just changelog-release "$TAG"` with the prepared date. |
| Release PR merged, exact CI still pending | Resume [Step 2.1](#1-synchronize-main-and-wait-for-ci) with the original `MAIN_RUN_ID`. |
| Tag preflight requires a tracked metadata correction | Commit and review the correction. Wait for main CI on the new commit and retry `just tag "$TAG" --commit "$RELEASE_COMMIT"` with its SHA. |
| Tag preflight stopped on an external dependency or tool | Resolve the reported prerequisite and retry `just tag "$TAG" --commit "$RELEASE_COMMIT"` with the same reviewed SHA. |
| Tag pushed, draft pending | Restore `RELEASE_COMMIT` from the merged PR using [Step 2.1](#1-synchronize-main-and-wait-for-ci). If the release tracker lacks the `just release-evidence` summary for this tag and commit, run [Step 2.4](#4-verify-the-final-release-commit) from that clean commit and retain the summary. Then resume [Step 2.5](#5-wait-for-the-draft-github-release) with the recorded `PREPARATION_RUN_ID`. |
| GitHub Release published, PyPI approval pending | Review the verification result and approve the `pypi` deployment in the original publishing run. |
| PyPI upload interrupted | Inspect the original GitHub Release and publishing run. Check the exact registry version with `just release-registry "$TAG" --expect present` and compare any published file hashes before deciding whether to retry. |
| Unpublished candidate needs a different commit | Follow the [replacement procedure](RECOVERING_RELEASES.md#replace-an-unpublished-candidate). |
| Registry GET timed out | Set `RRT_REGISTRY_TIMEOUT_SECONDS=180` for the failing recipe and retry with the same tag, commit, and retained evidence. |

### Recovering a failed run

Use the [recovery decision table](RECOVERING_RELEASES.md#choose-the-recovery-path) to select the procedure for the observed GitHub and registry state.

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

1. Preserve the original attachment outside the staging directory with the failed
   preparation run ID and attempt. Wait for its uploader to stop. Confirm the
   remote tag still resolves to `RELEASE_COMMIT`. Use an authenticated release
   listing to locate the unique mutable draft for that tag. Record its numeric
   release ID and check it against any previously captured target.
2. List every page of `GET /repos/{owner}/{repo}/releases/{release_id}/assets`.
   Record each asset's ID, name, state, size and digest. Download each `uploaded`
   asset by its recorded ID. Compare the wheel and sdist names and SHA-256 hashes
   with [Step 2.4's signed inventory](#4-verify-the-final-release-commit).
   Compare the uploaded `release-attestation.json` byte for byte with the
   retained attachment from that preparation attempt. If exactly these three
   assets match, resume [draft review](#6-review-the-draft).
3. A `starter` asset is an incomplete upload. Record its ID and exclude it
   from the verified files. RRT staging refuses a nonempty draft, even when
   every uploaded file matches. Keep the inventory and downloaded bytes.
   If an asset is unexpected, has different bytes, or its
   ownership is unclear, stop for maintainer reconciliation. Once a maintainer
   confirms which assets belong to the failed attempt and authorizes cleanup,
   recheck the mutable draft and asset IDs and remove only those assets, including
   incomplete uploads. [GitHub release asset operations](https://docs.github.com/en/rest/releases/assets)
4. Retry staging only when a fresh authenticated listing shows **no release
   for the tag** after failed creation (with no previously captured ID), or the
   **same mutable draft with zero assets**. Reconfirm the tag, commit and signed
   distribution inputs. In a reused working directory, move the original
   attachment into the recovery handoff first. Rerun the failed jobs of the
   original `PREPARATION_RUN_ID` and inspect the new attempt's complete draft
   in [Step 2.6](#6-review-the-draft).

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
