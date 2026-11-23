# Recovering an unfinished release

## Choose the recovery path

| What happened? | What to do |
| --- | --- |
| The intended commit is correct. Preparation timed out waiting for CI. | Wait for the original main CI run, then [retry preparation](RELEASING.md#preparation-timed-out-waiting-for-main-ci). Keep the tag. |
| The intended commit is correct. Draft creation or an upload failed. | [Inspect the existing draft and reconcile its assets](RELEASING.md#draft-creation-or-upload-failed) before retrying. Keep the tag and signed attachment. |
| The tag points to an earlier commit, and the corrected version has never been published. | [Replace the unpublished candidate](#replace-an-unpublished-candidate) below. |
| GitHub or the registry already published this version. | Resume verification for those exact bytes. Release corrections under a new version. |

## Replace an unpublished candidate

**If you have uncommitted corrections on the release branch, stay on that branch.**
Keep the version, declared date, old tag, and draft while you prepare one corrective PR.
You move the tag only after the PR merges and the corrected commit passes CI.

These commands are for research-repo-tools. Set `TAG` to your version once, for example
`TAG=v0.3.2`. If your project consumes RRT, see [Consumer workflows](#consumer-workflows).

### 1. Hold publication and record the old candidate

Tell the other maintainers to hold publication. Disable the PyPI workflow if it
is active. In Actions, check that preparation and publication runs for this
candidate have stopped. Keep the preparation workflow enabled.

```bash
gh workflow disable publish.yml
```

If publication is already frozen, skip the disable command. Save the original
tag object, commit, and draft inventory outside the checkout:

```bash
just release-recover record "$TAG"
```

The recipe verifies the freeze and checks that PyPI has never published this
version. Keep the original signed evidence and attachment with the handoff.

### 2. Stage the corrections

```bash
git status --short
git diff
```

Run the next command only if every listed file belongs in the PR. For a partial selection, stage the files in GitKraken and continue at step 3.

```bash
git add -A
```

### 3. Review the staged changes

```bash
git diff --cached --check
git diff --cached
just review origin/main
```

Address any findings and stage the fixes before committing.

### 4. Commit the reviewed corrections

```bash
git commit -m "fix(release): correct recovery for $TAG"
git status --short
```

Continue when `git status --short` prints nothing. The notes recipe reads committed history.

### 5. Generate and review the notes

```bash
just release-recover notes "$TAG"
git diff -- CHANGELOG.md docs/archives/changelog
```

Check that the notes include the corrections and retain the release date.

### 6. Commit the reviewed notes

If the notes changed, commit them on the same branch.

```bash
git add -- CHANGELOG.md docs/archives/changelog
git diff --cached --check && git commit -m "chore(release): prepare $TAG notes"
```

### 7. Submit the corrective PR

Run the [release checks](RELEASING.md#2-run-the-release-checks) and [local review](RELEASING.md#4-run-the-local-coderabbit-review).
Push both commits in one PR against `main`. If the original PR merged, open a corrective PR. Keep any release tracker open.

### 8. Wait for the corrected commit on main

After the corrective PR **merges**, switch to a clean, synchronized `main`.
Use the **merge commit shown on that PR** and wait for its exact
[main-push CI run](RELEASING.md#1-synchronize-main-and-wait-for-ci). If `main`
has advanced, check out that merge commit explicitly. Review its release notes.
If they omit changes you intend to ship, regenerate the notes on the corrective branch and repeat the PR checks.

### 9. Replace the local tag

Run this from the clean, validated merge commit.

```bash
just release-recover tag "$TAG"
```

Review the printed annotation, commit, and old draft ID. If dependency receipts are stale,
run `just release-dependency-builds` and `just release-dependencies`, then retry.

### 10. Delete the old draft in GitHub Releases

Confirm the draft ID, status, and assets match the handoff, then delete that draft only. Keep the remote Git tag.
If the record shows no draft, skip this step. Stop if the release has been published or its identity changed.

### 11. Push the replacement tag

```bash
just release-recover push "$TAG"
```

The recipe checks the saved tag objects and requires the old draft to be gone. The push starts a new preparation run.

If a push was interrupted, repeat the push recipe. It recognizes the recorded
replacement on the remote. An unexpected tag object stops recovery.

### 12. Verify the new draft and resume publication

Run [Step 2.4 through draft review](RELEASING.md#4-verify-the-final-release-commit)
for the corrected commit. Require a new preparation run, new draft ID, and new
signed evidence. Once the new draft passes review, coordinate lifting the freeze:

```bash
gh workflow enable publish.yml
```

Resume at [Step 2.7, publish the reviewed GitHub Release](RELEASING.md#7-publish-the-reviewed-github-release).

## Consumer workflows

The `release-recover` recipe belongs to this repository. Consumers using the shared
draft writer must follow [authenticated preparation recovery](PREPARING_DRAFTS.md#retry-and-recovery)
for the original attempt. Replacing a candidate requires a maintainer-owned
procedure for that writer, agreed before deleting a draft or moving a remote tag.
Keep its original journal, attachment, receipts, and workflow history.

For benchmark releases, follow the [benchmark recovery decisions](REUSING_BENCHMARKS.md#release-recovery-decisions).
Revalidate reusable measurements and bind a new acceptance and attachment to the
corrected commit and draft. Resume under the consumer's own publication policy.
