# Recovering an unfinished release

<!-- rumdl-configure-file { "MD033": { "allowed-elements": ["details", "summary"] } } -->

For a new research-repo-tools release, start with the [release checklist](RELEASING.md).
If you use the shared tools in another repository, see [consumer workflows](#consumer-workflows).

## Choose the recovery path

In a fresh shell, set `TAG` again. Keep the original checkout, tag, draft and
`../release-handoffs/research-repo-tools/<tag>/` intact while you inspect them.
The directory holds `evidence-<run>-<attempt>.md` and, after capture,
`<commit>/target.json`. Older handoffs may keep the matching target at the tag root.
If a tagged checkout predates automatic target lookup, supply its original
`target.json` to its publish and verify recipes with `--target PATH`.

| Observed state | Where to resume |
| --- | --- |
| Metadata prepared, notes missing or a review found a fix | [Preparation corrections](#preparation-corrections) |
| PR merged, CI pending or the tag push was interrupted | [Inspect the original runs](#inspect-the-original-runs) |
| Tag pushed, exact main CI passed, draft pending | Run `just release-evidence` and `just release-draft "$TAG"` from the tagged checkout. |
| Preparation failed waiting for CI, before draft creation | [Retry preparation](#preparation-timed-out-waiting-for-main-ci) |
| Draft creation or asset upload failed or returned an uncertain result | [Reconcile the original draft](#draft-creation-or-upload-failed) |
| Signed artifacts expired or main CI reran only failed jobs | [Renew the main evidence](#artifact-retention-and-recovery-window) |
| An unpublished tag needs a different commit | [Replace the unpublished candidate](#replace-an-unpublished-candidate) |
| GitHub publication, deployment approval or PyPI upload was interrupted | [Inspect publication](#recover-github-publication-or-pypi-upload) |
| Both files are on PyPI, downstream verification unfinished | [Verify the files and fresh install](#verify-pypi-files-and-a-fresh-install) |

## Preparation corrections

If the notes generator failed after the version update, fix its reported
prerequisite and rerun `just changelog-release "$TAG"`. It uses the prepared
citation date. To deliberately change that date, run
`just update-version "$TAG" --date YYYY-MM-DD` first.
[Metadata](UPDATING_RELEASE_METADATA.md) · [Changelog generation](GENERATING_CHANGELOGS.md)

For an unpushed preparation-only correction, amend the preparation commit and
keep its subject. Commit a user-facing fix with a descriptive subject before
regenerating the notes. Review any changed notes and commit them as
`chore(release): prepare $TAG notes`. Repeat the affected release checks and
the [push gates](RELEASING.md#3-review-and-open-the-pr) on the final content.
For a corrective push, first collect all checks and reviews on the existing PR
head as described in [repository validation](../AGENTS.md#validation).

If tag preflight reports a stale external tool or dependency receipt, fix it
and retry the same reviewed SHA. A tracked correction needs a reviewed PR
and complete main CI on its merge commit. If the old tag is already on GitHub,
use the [replacement procedure](#replace-an-unpublished-candidate).

## Inspect the original runs

Recover `RELEASE_COMMIT` from the [merged release or corrective PR](RELEASING.md#1-synchronize-main-and-wait-for-ci).
From the repository root, inspect the annotated tag and its peeled commit on
`origin`, then list the runs for this commit and event.

```bash
git ls-remote origin "refs/tags/$TAG" "refs/tags/$TAG^{}"
gh run list --workflow ci.yml --event push --branch main --commit "$RELEASE_COMMIT"
gh run list --workflow prepare-release.yml --event push --branch "$TAG" --commit "$RELEASE_COMMIT"
gh run list --all --workflow publish.yml --event release --branch "$TAG" --commit "$RELEASE_COMMIT"
```

If a tag push was interrupted, compare the remote tag object with
`git rev-parse "refs/tags/$TAG"` and its peeled SHA with `RELEASE_COMMIT`.
If both match, continue with evidence and draft capture. If the lookup succeeds
and the remote tag is absent, confirm the local annotated tag and reviewed SHA
with `git --no-pager show --no-patch "$TAG"` before pushing it. Allow time for
a new run to appear. Investigate any mismatch or ambiguous run before continuing.

Use the run ID and attempt from the matching run or retained summary. Wait for
a running job. For a failed attempt, read its failed step before choosing a retry.

```bash
gh run view "$RUN_ID" --attempt "$ATTEMPT" --verbose
gh run view "$RUN_ID" --attempt "$ATTEMPT" --log-failed
```

## Preparation timed out waiting for main CI

Keep the original tag. Once the exact complete main-push run passes, confirm the
failed preparation attempt stopped before draft creation. Set `PREPARATION_RUN_ID`
to its ID. From the tagged checkout, retry those jobs, save the signed main
evidence and capture the draft. The last command waits for preparation to succeed.

```bash
gh run rerun "$PREPARATION_RUN_ID" --failed &&
  just release-evidence &&
  just release-draft "$TAG"
```

## Draft creation or upload failed

Wait for the original writer to stop. Record its preparation run ID and attempt.
Preserve any saved attachment and signed distribution summary. When the workflow retained
`draft-recovery-<attempt>`, download it into a fresh directory outside the checkout.

```bash
gh run download "$PREPARATION_RUN_ID" --name "draft-recovery-$ATTEMPT" \
  --dir "../release-handoffs/research-repo-tools/$TAG/preparation-$PREPARATION_RUN_ID-$ATTEMPT"
```

1. Wait for the uploader to stop. Confirm the remote tag still peels to the
   reviewed commit. From the repository root, list **all** releases for the
   tag using authenticated access, including drafts.

   ```bash
   releases=$(gh api --paginate --slurp 'repos/{owner}/{repo}/releases?per_page=100') &&
     jq --arg tag "$TAG" '.[][] | select(.tag_name == $tag) | {id,draft,immutable}' <<< "$releases"
   ```

   An empty result establishes absence only when the command succeeds.
   If a release exists, require one mutable draft matching any saved release ID.

2. Set `DRAFT_ID` to that ID and list every asset page.

   ```bash
   gh api --paginate "repos/{owner}/{repo}/releases/$DRAFT_ID/assets?per_page=100" --jq '.[] | {id,name,state,size,digest}'
   ```

   Save the inventory. Download each uploaded asset by ID into a fresh handoff
   directory using its recorded filename. Check the distribution names and
   hashes against the original signed summary. If you retained the attachment,
   compare its bytes with the draft copy. If preparation succeeded before you
   saved a copy or summary, [verify the signed promotion](#verify-a-successful-draft-without-a-saved-attachment).
   A `starter` asset is incomplete. Hold a failed upload for maintainer
   reconciliation if the available evidence cannot identify its assets.
   [GitHub release asset API](https://docs.github.com/en/rest/releases/assets)

3. If preparation **succeeded** and all three assets match the current, unexpired
   evidence, [save the summary and capture the draft](RELEASING.md#3-save-the-signed-evidence-and-capture-the-draft) before
   [draft review](RELEASING.md#4-review-the-draft).
   If an upload failed or the draft needs [renewed evidence](#artifact-retention-and-recovery-window),
   keep the downloaded files and their IDs in the handoff. If ownership or bytes
   differ from the original attempt, hold publication for maintainer reconciliation.
   With explicit maintainer approval, recheck the tag, mutable draft ID and asset
   IDs. Remove only the recorded assets from the failed upload, or all three
   verified assets from the successful attempt whose evidence is being replaced.
   Keep the tag and draft.

4. Retry the original failed jobs only after a fresh successful listing shows
   **no release and no prior captured ID**, or **the same mutable draft with zero
   assets**. Reconfirm the tag, commit and signed inputs. In a reused staging
   directory, move the original attachment into the handoff first. After the run
   succeeds, repeat evidence and draft capture and compare all three assets again.

### Verify a successful draft without a saved attachment

A successful run leaves its signed attachment on the draft. To identify the
old assets for cleanup, verify that signature against both downloaded files.
Set `ASSET_DIR` to their directory. Use the reviewed `TAG` and
`RELEASE_COMMIT`, and get `PREPARATION_RUN_ID` and `ATTEMPT` from the
[successful tag-push run](#inspect-the-original-runs) that staged these assets.

```bash
(
  set -eu
  cd "${ASSET_DIR:?Set the directory containing the three downloaded assets}"
  repo=acgetchell/research-repo-tools
  predicate="https://github.com/$repo/attestations/release-promotion/v1"
  invocation="https://github.com/$repo/actions/runs/${PREPARATION_RUN_ID:?}/attempts/${ATTEMPT:?}"
  jq -erj '.promotion_bundle' release-attestation.json > promotion.sigstore.json
  for file in "research_repo_tools-${TAG#v}-py3-none-any.whl" "research_repo_tools-${TAG#v}.tar.gz"; do
    gh attestation verify "$file" --bundle promotion.sigstore.json --repo "$repo" \
      --signer-workflow "$repo/.github/workflows/prepare-release.yml" --signer-digest "${RELEASE_COMMIT:?}" \
      --source-digest "$RELEASE_COMMIT" --source-ref "refs/tags/${TAG:?}" --deny-self-hosted-runners \
      --predicate-type "$predicate" --format json > "$file.verified.json"
    jq -e --arg uri "$invocation" --arg schema "$predicate" --slurpfile proof release-attestation.json '
      length == 1 and ($proof[0].schema == $schema) and (.[0].verificationResult |
        .signature.certificate.runInvocationURI == $uri and .signature.certificate.buildTrigger == "push" and
        .statement.predicate == $proof[0].evidence)' "$file.verified.json"
  done
)
```

Require both checks to print `true` and exit successfully. Read the original
file hashes with `jq '.evidence.manifest | fromjson | .files' "$ASSET_DIR/release-attestation.json"`.
If either check fails, preserve the draft for reconciliation. Publication
still requires [current evidence](#artifact-retention-and-recovery-window).

## Artifact retention and recovery window

The build distributions and signed main evidence are retained for 30 days.
If main evidence is missing, expired or split across attempts, use
`gh run rerun "$MAIN_RUN_ID"` to rerun **all** jobs of the original main-push run
while it is eligible. Wait for the complete attempt, including `release-evidence`.
Preserve the old summary and record the new attempt, artifact IDs and digests.
[GitHub Actions rerun window](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)

For an unpublished tag, run `just release-evidence` for the renewed main attempt.
Confirm the original tag-push preparation run is eligible for a rerun before
clearing any assets. Follow [steps 1–3 of draft reconciliation](#draft-creation-or-upload-failed)
even when the old and new wheel hashes match. Require a fresh listing that shows
the same mutable draft with zero assets, or no release and no prior captured ID.
Then rerun **all jobs** of the original preparation run with
`gh run rerun "$PREPARATION_RUN_ID"`. It promotes the renewed evidence and
stages all three assets. After it succeeds, run `just release-draft "$TAG"` and
[review the draft](RELEASING.md#4-review-the-draft) against the renewed summary.

If either original run is no longer eligible, [replace the unpublished candidate](#replace-an-unpublished-candidate).
Published releases retain their original attestation.

## Recover GitHub publication or PyPI upload

If publication returned an error or disconnected, read the numeric release ID
from the original `target.json`. Check that same release and the remote tag
before repeating any write.

```bash
RELEASE_ID=$(jq -er '.release_id' "${RELEASE_TARGET:?Set the path to the retained target.json}")
gh api "repos/{owner}/{repo}/releases/${RELEASE_ID:?}" --jq '{id,tag_name,draft,prerelease,immutable}'
```

- **Same mutable draft.** Confirm the tag, commit, assets and evidence still
  match, then repeat [draft review and publication](RELEASING.md#4-review-the-draft).
  A missing release or changed ID needs maintainer reconciliation.
- **Same immutable release.** Open the original **Publish to PyPI** run for that
  tag and commit. If verification passed and `publish` awaits approval, click
  **Review deployments**, select **pypi**, and click **Approve and deploy**.
  Wait for the upload job. If no run appears, check the event, workflow
  state and publishing identity with a maintainer. Keep the published release intact.
- **Upload failed or its result is unknown.** Wait for the original uploader
  to stop. Inspect the exact version with `just release-registry "$TAG" --expect present`
  and the [PyPI file inventory](https://docs.pypi.org/api/json/#get-a-release).
  If both files match the signed bytes, continue verification. If
  `just release-registry "$TAG" --expect absent` confirms absence and the cause
  is fixed, rerun the original publication run using the same immutable release,
  let it verify the assets again, and approve the protected upload.
- **One file, an unexpected digest, or yanked files.** Preserve the observed
  filenames, hashes and failed job. Hold the upload for maintainer reconciliation.
  Release corrections to published contents under a new version. A timeout
  leaves the registry state unknown. For a slow GET, retry the lookup with
  `RRT_REGISTRY_TIMEOUT_SECONDS=180`.
- **Verification artifact expired.** The distribution handoff from `verify` to
  `publish` lasts seven days. After confirming PyPI absence, rerun the entire
  original publication run if the underlying signed evidence is still valid.
  If the immutable attestation can no longer pass verification, preserve the
  original records and prepare a new version.

## Verify PyPI files and a fresh install

From the exact PyPI version's files page, download its wheel and sdist into
a fresh directory. Run `shasum -a 256 *.whl *.tar.gz` there (or `sha256sum`)
and compare **both filenames and hashes** with the independent `release-evidence`
summary. The [published distribution verifier](publishing-api.md#reviewed-registry-publication-api)
can perform the downloads and comparison from the approved hash mapping.

After `just release-verify "$TAG"` and the byte checks pass, exercise the exact
pin in a fresh project against public PyPI. Use the release's supported Python.

```bash
(
  set -eu
  cd "$(mktemp -d)"
  uv init --bare --name rrt-release-check --vcs none --python 3.15
  uv add --default-index https://pypi.org/simple "research-repo-tools==${TAG#v}"
  uv run --locked research-repo-tools templates justfile --output justfile
  uv run --locked just help
)
```

Record the resolved version, hashes and consumer results with the handoff.
Resume [final verification and cleanup](RELEASING.md#7-verify-the-published-package).

## Replace an unpublished candidate

**If you have uncommitted corrections on the release branch, stay on that branch.**
Otherwise, after the original PR merges, start `fix/recover-$TAG` from a clean,
synchronized `main`. Keep the version, old tag, and draft while you prepare one corrective PR.
You move the tag only after the PR merges and the corrected commit passes CI.
If the contents and declared date must both stay fixed, leave the candidate on
hold or [prepare a new version](RELEASING.md#step-1-set-the-release-tag) before starting this procedure.

### 1. Hold publication and record the old candidate

Tell the other maintainers to hold publication. Disable the PyPI workflow if it
is active. In Actions, check that preparation and publication runs for this
candidate have stopped. Keep the preparation workflow enabled.

```bash
gh workflow disable publish.yml
```

If publication is already frozen, skip the disable command. Save the original
tag object, commit, and draft inventory outside the checkout.

```bash
just release-recover record "$TAG"
```

The recipe verifies the freeze, the matching local and remote annotated tag,
the current draft inventory and the version's absence on PyPI. Keep the
original signed evidence and attachment with the handoff. A previously
published version requires a new version even if its files were later removed.

### 2. Stage the corrections

For a content correction, keep the declared release date. If only the evidence
expired, choose the new planned release date and run
`just update-version "$TAG" --date YYYY-MM-DD` with that date. Review the
changed date in `CITATION.cff` and the current `CHANGELOG.md` heading.
These tracked changes give the recovery PR a new commit for the same version.

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

For a date-only change, use `git commit -m "chore(release): prepare $TAG"`.
For a content correction, use a subject that describes the fix.

```bash
git commit -m "fix(release): correct recovery for $TAG"
```

Run `git status --short` after committing. Continue when it prints nothing.
The notes recipe reads committed history.

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

Run the [release checks](RELEASING.md#2-run-the-release-checks) and
[push gates](RELEASING.md#3-review-and-open-the-pr) on the final content.
Submit the corrections and notes together against `main`. The handoff accepts
a same-repository PR from `release/$TAG` or `fix/recover-$TAG`. If the original
PR already merged, open a corrective PR and keep its number for the CI lookup.
Keep any release tracker open.

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
[Review the draft](RELEASING.md#4-review-the-draft). Check the title, notes, and
three uploaded assets. Match the wheel and sdist digests to the saved evidence
and the attestation to the same CI run and attempt.

### 13. Lift the freeze and resume publication

Once the draft passes review, coordinate with the other maintainers and re-enable
the PyPI workflow.

```bash
gh workflow enable publish.yml
```

Return to [GitHub publication](RELEASING.md#5-publish-the-github-release), approve the PyPI deployment,
and complete the registry and consumer checks for the replacement.

## Consumer workflows

The `release-recover` recipe belongs to this repository. Consumers using the shared
draft writer must follow [authenticated preparation recovery](PREPARING_DRAFTS.md#retry-and-recovery)
for the original attempt. Replacing a candidate requires a maintainer-owned
procedure for that writer, agreed before deleting a draft or moving a remote tag.
Keep its original journal, attachment, receipts, and workflow history.

For benchmark releases, follow the [benchmark recovery decisions](REUSING_BENCHMARKS.md#release-recovery-decisions).
Revalidate reusable measurements and bind a new acceptance and attachment to the
corrected commit and draft. Resume under the consumer's own publication policy.
