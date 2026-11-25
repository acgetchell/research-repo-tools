# Recovering an unfinished release

<!-- rumdl-configure-file { "MD033": { "allowed-elements": ["details", "summary"] } } -->

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

Start from the clean, validated merge commit. Pause automatic tag fetching
(for example, quit GitKraken) through step 11 to keep the old remote tag
from overwriting your local replacement.

```bash
just release-recover tag "$TAG"
```

Continue when the recipe prints the reviewed annotation, the corrected merge commit,
the replacement tag object, and the old draft ID (or `none`). If dependency receipts
are stale, run `just release-dependency-builds` and `just release-dependencies`, then retry.

<details>
<summary>If a background fetch restores the old local tag in step 9 or 11</summary>

Pause the fetching client. From the corrected checkout, compare the local
and remote tag objects with the saved replacement.

```bash
plan="../release-handoffs/research-repo-tools/$TAG/$(git rev-parse HEAD)/replacement.json"
git show-ref --verify "refs/tags/$TAG"
git ls-remote --refs origin "refs/tags/$TAG"
cat "$plan"
```

Continue only if both objects equal `old` and the record's `tag` and `commit`
match this release and checkout. Then restore the reviewed object and repeat step 9.

```bash
old=$(jq -er '.old' "$plan") &&
  new=$(jq -er '.new' "$plan") &&
  git update-ref "refs/tags/$TAG" "${new:?}" "${old:?}" &&
  just release-recover tag "$TAG"
```

</details>

### 10. Delete the old draft in GitHub Releases

If step 9 printed `none`, skip this step. Otherwise, confirm the draft ID,
status, and assets match the original handoff. In GitHub **Releases**, click
the trash icon beside that draft and confirm **Delete this release**. Keep the
remote Git tag. Stop if the release has been published or its identity changed.

### 11. Push the replacement tag

```bash
just release-recover push "$TAG"
```

The recipe checks the saved tag objects and requires the old draft to be gone.
The push starts **Prepare GitHub release**, which creates the new draft. Once
the recipe confirms the push, you can resume automatic tag fetching.

If a push was interrupted, repeat the recipe. It recognizes the recorded
replacement on the remote. For a local tag mismatch, see [step 9](#9-replace-the-local-tag).

### 12. Capture and review the replacement draft

Keep publication frozen. Save the signed evidence for the corrected commit,
then capture the new draft. The second recipe waits for preparation to finish.

```bash
just release-evidence && just release-draft "$TAG"
```

Confirm the saved target identifies the corrected commit and the new draft ID.
[Review the draft](RELEASING.md#6-review-the-draft). Check the title, notes, and
three uploaded assets. Match the wheel and sdist digests to the saved evidence
and the attestation to the same CI run and attempt.

### 13. Lift the freeze and resume publication

Once the draft passes review, coordinate with the other maintainers and re-enable
the PyPI workflow.

```bash
gh workflow enable publish.yml
```

Then run `just release-publish "$TAG"` to [publish the reviewed GitHub Release (step 2.7)](RELEASING.md#7-publish-the-reviewed-github-release)
and trigger the PyPI workflow. Continue with [deployment approval](RELEASING.md#8-approve-the-pypi-deployment).

## Consumer workflows

The `release-recover` recipe belongs to this repository. Consumers using the shared
draft writer must follow [authenticated preparation recovery](PREPARING_DRAFTS.md#retry-and-recovery)
for the original attempt. Replacing a candidate requires a maintainer-owned
procedure for that writer, agreed before deleting a draft or moving a remote tag.
Keep its original journal, attachment, receipts, and workflow history.

For benchmark releases, follow the [benchmark recovery decisions](REUSING_BENCHMARKS.md#release-recovery-decisions).
Revalidate reusable measurements and bind a new acceptance and attachment to the
corrected commit and draft. Resume under the consumer's own publication policy.
