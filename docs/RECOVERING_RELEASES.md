# Recovering an unfinished release

Use this guide when a release already has a tag, a draft, or retained evidence.
It applies to research-repo-tools and consumers of its release tooling. Keep the
repository's normal release guide for package-specific checks, workflow names,
benchmark commands, and publication approvals.

## Choose the recovery path

| Current state | Start here | What stays valid? |
| --- | --- | --- |
| The intended commit is unchanged; preparation timed out waiting for healthy main CI | Wait for the complete original main run, then resume under the owning workflow's recovery contract. Use the [RRT preparation retry](RELEASING.md#preparation-timed-out-waiting-for-main-ci). | Keep the tag. Reuse successful, unexpired evidence from that complete attempt. |
| The intended commit is unchanged; draft creation or upload failed | For RRT, [reconcile the draft and its assets before retrying staging](RELEASING.md#draft-creation-or-upload-failed). For the shared consumer writer, use its [retained-state recovery](PREPARING_DRAFTS.md#retry-and-recovery). | Preserve the original attachment, receipts and accepted bytes. |
| Corrections must land after the tag or draft was created | Follow **[Replace an unpublished candidate](#replace-an-unpublished-candidate)** below. | Keep the version and declared date unless deliberately changing them. Reassess benchmark reuse separately. |
| GitHub or the registry already contains a published release | Preserve that publication. Resume verification or the missing publication stage for the same bytes; use a new version for corrected contents. | Never move its tag or replace its published assets. |

A failed staging job alone does not require another version bump, dependency
update, CI run, or benchmark campaign. First identify which identity or evidence
actually changed. Repository lookup failures are not proof that publication is
absent.

## Check the consumer's draft writer

RRT's `Prepare GitHub release` workflow supports the replacement sequence below.
Consumers must first establish how their own writer handles a superseded intent.
The shared `release assets prepare-authenticated` writer requires the original
attempt-1 handoff and unique retained tag-push history for fresh state. Automatic
Actions reruns and another push of the same tag cannot create fresh retry state,
even when the tag now names a corrected commit.

For an unchanged candidate, use the
[authenticated writer's retained-state recovery](PREPARING_DRAFTS.md#retry-and-recovery).
For a replacement candidate, the consumer needs an explicitly reviewed,
maintainer-owned replacement procedure that reconciles the old intent and
authorizes the new source, evidence, and draft. Establish that procedure **before
deleting the old draft or moving the remote tag**. If none exists, keep the old
candidate while adding it. Never delete workflow history or discard a journal
to make a repeated preparation appear new. The generic tag commands below do
not override these writer requirements.

## Replace an unpublished candidate

Consumers must complete the [writer check](#check-the-consumers-draft-writer)
before using this sequence. RRT can proceed with its existing preparation workflow.

The sequence is **keep the old candidate → merge corrected notes/source → wait
for final main CI → replace the unpublished tag and draft → verify and publish**.
The replacement uses the same version name, a new annotated tag object pointing
to the corrected commit, and a new draft with new assets and promotion evidence.

These commands are an operator procedure, not an automatic retry. Obtain the
repository's required authorization for the publication freeze, local tag changes,
draft deletion, and remote tag replacement. Run from the repository root in Bash
or Zsh (Git Bash on Windows). Stop after any failed command and inspect the
resulting state before continuing.

Use the same shell throughout. The assignments below retain the candidate's
identity; they do not create tags or drafts. Copy the printed assignments into
the release tracker as you complete each step. After a shell restart, restore
those recorded values before resuming. Do not recompute `OLD_*` from a candidate
that you have already replaced.

### Freeze publication before step 1

Set the intended tag and derive the repository from this checkout:

```bash
TAG=vX.Y.Z
gh auth status
git --no-pager remote -v
git --no-pager status --short --branch
REPO=$(gh repo view "$(git --no-pager remote get-url origin)" --json nameWithOwner --jq .nameWithOwner)
```

Confirm `origin` is the named repository and the account can see its drafts.
Record a publication freeze in the release tracker and coordinate it with every
maintainer: do not publish this tag or the recorded draft (`OLD_RELEASE_ID` below)
until replacement completes and the new candidate passes step 5. Do not publish
any other release while its publication workflow is disabled: those release
events will not start an upload run. Disable every workflow that can publish
this candidate. For RRT, the workflow is `publish.yml`; consumers must use their
own publication workflow names:

```bash
(
  set -eu
  WORKFLOW_STATE=$(gh api "repos/$REPO/actions/workflows/publish.yml" --jq .state)
  case "$WORKFLOW_STATE" in
    active) gh workflow disable publish.yml --repo "$REPO" ;;
    disabled_manually) ;; # The publication freeze is already in place.
    *) printf 'Stop: unexpected publication workflow state: %s\n' "$WORKFLOW_STATE" >&2; exit 1 ;;
  esac
  WORKFLOW_STATE=$(gh api "repos/$REPO/actions/workflows/publish.yml" --jq .state)
  test "$WORKFLOW_STATE" = disabled_manually
  printf 'Publication workflow: %s\n' "$WORKFLOW_STATE"
)
```

An already-disabled workflow needs no second disable request; GitHub rejects
that request with HTTP 403. Require `disabled_manually`. Inspect queued and active
publication runs for `$TAG`, cancel those candidate runs, and confirm they have
stopped before proceeding. Leave runs for other tags unchanged. Disabling a workflow
does not prevent manual GitHub publication through the UI or API, so maintain
the coordinated freeze throughout replacement. Keep the preparation workflow
enabled for step 5. See [GitHub CLI workflow disable](https://cli.github.com/manual/gh_workflow_disable).

### 1. Record the existing candidate

First define this read-only helper. It returns one unpublished draft ID, or
`none` after a successful listing with no matching release. A failed lookup,
published/immutable release, or multiple matching drafts stops it. Keep the
function available for step 4; after a shell restart, define it again without
rerunning the old-candidate assignments.

```bash
candidate_draft_id() (
  set -eu
  : "${REPO:?Set REPO first}" "${TAG:?Set TAG first}"
  ids=$(gh api --paginate "repos/$REPO/releases?per_page=100" \
    --jq ".[] | select(.tag_name == \"$TAG\") |
      if .draft == true and .published_at == null and .immutable == false
      then .id else error(\"Stop: candidate is published or immutable\") end") || exit "$?"
  case "$ids" in
    '') printf 'none\n' ;;
    *[!0-9]*) printf 'Stop: expected exactly one candidate draft\n' >&2; exit 1 ;;
    *) printf '%s\n' "$ids" ;;
  esac
)
```

Now record the annotated local tag, its commit, and the draft ID in this shell.
The chained checks require the same tag object on the remote before printing
the recovery values:

```bash
OLD_TAG_OBJECT=$(git --no-pager rev-parse --verify "refs/tags/$TAG") &&
  OLD_COMMIT=$(git --no-pager rev-parse --verify "refs/tags/$TAG^{commit}") &&
  test "$(git --no-pager cat-file -t "$OLD_TAG_OBJECT")" = tag &&
  REMOTE_TAG=$(git --no-pager ls-remote --exit-code origin "refs/tags/$TAG") &&
  test "${REMOTE_TAG%%[[:space:]]*}" = "$OLD_TAG_OBJECT" &&
  OLD_RELEASE_ID=$(candidate_draft_id) &&
  printf 'TAG=%s\nREPO=%s\nOLD_TAG_OBJECT=%s\nOLD_COMMIT=%s\nOLD_RELEASE_ID=%s\n' \
    "$TAG" "$REPO" "$OLD_TAG_OBJECT" "$OLD_COMMIT" "$OLD_RELEASE_ID"
```

Require success before proceeding. `OLD_RELEASE_ID` is now the numeric draft ID
or the literal `none`; no later placeholder assignment is needed. If the local
tag is missing or differs, reconcile it with the recorded remote candidate
first. A draft without its expected remote tag needs the owning writer's
recovery. Authenticated listings include drafts only for users with the
necessary access; see the [GitHub releases API](https://docs.github.com/en/rest/releases/releases#list-releases).

Check the **exact version** using the existing publication configuration:

```bash
just release-registry "$TAG" --expect absent
```

The recipe reads `registry` and `package` from
`[tool.research-repo-tools.publishing]` in `pyproject.toml`; there is no package
name or registry URL to re-enter. Consumers should include `release-registry`
from the shared Justfile. It calls the existing `release registry` command and
succeeds only on a proven exact-version 404. An existing version or an unavailable
registry stops recovery; lookup errors never prove absence.

Preserve the old/full commit IDs, tag object, draft ID and asset digests,
preparation/main run IDs and attempts in the release tracker. Retain original
benchmark payloads, envelopes, and owner receipts in durable storage outside
the checkout and the draft being replaced. Coordinate one operator
for replacement. Inspect active preparation and publication runs for this
candidate; cancel superseded runs and confirm they have stopped before replacing
anything.

### 2. Finish the corrective PR and regenerate notes

**Follow 2.1–2.5 in order.** Keep the old remote tag and draft throughout this
step. Retain the prepared version and declared release date; do not rerun
`just update` or the version bump solely to recover. Keep `TAG`, `REPO`, and
`OLD_TAG_OBJECT` from step 1 in this shell.

The result is **one PR containing all remaining corrections and the regenerated
changelog**. Corrections must be committed before generation so git-cliff can
include their history. If the corrections and the required tag recipe are already
on `main`, this PR contains only the missing notes. A merged PR cannot receive
another commit; use the branch selected in 2.1 for the remaining work.

#### 2.1. Select the corrective branch

**Already on your intended corrective branch?** Keep it and set
`RECOVERY_BRANCH=$(git --no-pager branch --show-current)`. Skip the creation block.
This also applies when you created the branch earlier in this recovery.

**Still on current `main`?** Run this block once:

```bash
git --no-pager status --short --branch
RECOVERY_BRANCH="fix/recover-$TAG"
git switch -c "$RECOVERY_BRANCH"
```

If that branch name already exists, inspect it and switch to it only if it is
the intended corrective branch; do not reset or overwrite it. Creating a branch
carries any notes already generated on `main` with it. Preserve local changes.

#### 2.2. Finish corrections and establish validation once

Finish any remaining source, recipe, or documentation edits on this branch.
For step 4, use the `tag` recipe from the
[current consumer template](../src/research_repo_tools/templates/justfile). It
forwards `--force` to `changelog tag`. If your copied Justfile needs that update,
include it in the corrective commit before generating notes. Step 3 validates the
merged commit with the updated recipe.

Use `just check` while editing. Benchmark consumers also decide whether fresh
acquisition is required using the
[benchmark recovery decision table](REUSING_BENCHMARKS.md#release-recovery-decisions).
Include required new benchmark outputs in this PR.

**This is the only full local CI/security checkpoint in step 2.** Use existing
successful results when their inputs and execution context still match. This
includes results from a merged corrective PR with an identical source tree.
Run affected checks for further edits. If valid results are missing, run the
missing commands below now; consumers use their corresponding required checks:

```bash
just ci
just security
```

Record the checks run or retained. **Generating the changelog in 2.3 does not
require repeating these commands.** Its required checks are in that subsection.

Choose the PR title now:

- **Generated notes only:** set `RECOVERY_TITLE="chore(release): prepare $TAG notes"`.
- **This branch contains corrections, committed or uncommitted:** set
  `RECOVERY_TITLE="fix(release): correct recovery for $TAG"`, or a more specific
  corrective title. Retain that title for the PR.

**No uncommitted corrections? Continue directly to 2.3.** Keep existing commits;
do not attempt an empty commit. This also covers the notes-only path.

<!-- rumdl-configure-file { "MD033": { "allowed-elements": ["details", "summary"] } } -->

<details>
<summary>Only if corrections still need committing: review and commit them</summary>

Run `just review` and inspect the result before committing. It includes the
branch's committed corrections and local changes. Require a completed review,
fix valid findings, and run their affected checks. Stop on an incomplete review
or paid-consent request and follow the repository's review policy.

Then run the following commands **one at a time**. `git add --patch` is interactive:
finish its hunk prompts before entering the next command. Select only reviewed
corrections; leave generated changelog changes for 2.4. Add reviewed new files
by their explicit paths because patch selection does not include untracked files.

```bash
git --no-pager diff
git add --patch
git --no-pager diff --cached --check
git --no-pager diff --cached
git commit -m "$RECOVERY_TITLE"
```

</details>

#### 2.3. Generate and check the changelog

**Run this entire block after any corrective commits.** The
`just changelog-release "$TAG"` command writes `CHANGELOG.md` and any configured
archives. An existing local tag delimits git-cliff history, so the block
temporarily removes only that local ref and restores it even if generation or
validation fails. The remote tag and draft stay in place.

```bash
(
  set -eu
  test "$(git --no-pager rev-parse "refs/tags/$TAG")" = "$OLD_TAG_OBJECT"
  restore_candidate_tag() {
    if ! git --no-pager rev-parse --verify "refs/tags/$TAG" >/dev/null 2>&1; then
      git update-ref "refs/tags/$TAG" "$OLD_TAG_OBJECT" ""
    fi
    test "$(git --no-pager rev-parse "refs/tags/$TAG")" = "$OLD_TAG_OBJECT"
  }
  trap restore_candidate_tag EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  git update-ref -d "refs/tags/$TAG" "$OLD_TAG_OBJECT" || exit "$?"
  just changelog-release "$TAG" || exit "$?"
  just changelog-check || exit "$?"
  just release-version-check || exit "$?"
)
```

If the shell was forcibly killed, inspect the local ref before continuing. When
it is absent, `git update-ref "refs/tags/$TAG" "$OLD_TAG_OBJECT" ""` restores it
only if it remains absent. Never overwrite an unexpected local object.

#### 2.4. Review and commit the generated notes

Review the generated changelog and any archives. The paths below are RRT's
configured outputs; consumers use their configured changelog and archive paths.
Do not hand-edit generated notes.

```bash
git --no-pager status --short
git --no-pager diff -- CHANGELOG.md docs/archives/changelog
just review
```

Inspect any new archive files listed by `status` as well; unstaged diffs do not
show untracked files. Confirm the release includes the intended corrections and
date. `just review` covers the complete branch and remaining local changes;
inspect its findings, not just its exit status. Resolve valid findings and
require a completed review before committing. Stop on an incomplete review or
paid-consent request. If review requires further corrective commits, run their
affected checks and repeat 2.3 so the notes include them.

**Generated notes changed?** Run the commit block below after reviewing every
output. **No generated changes?** Skip this block and continue to 2.5.

```bash
git add -- CHANGELOG.md
if [ -d docs/archives/changelog ]; then
  git add -- docs/archives/changelog
fi
git --no-pager diff --cached --check
git --no-pager diff --cached
git commit -m "chore(release): prepare $TAG notes"
git --no-pager status --short
```

The shared changelog template excludes this preparation title. Use it as the PR
title only when the PR contains generated notes alone, avoiding another refresh
just to record the notes-only PR. A PR containing corrections keeps its corrective title.

#### 2.5. Push once, then create or update the PR

Require all intended changes to be committed and the checks/review above to be
complete. First refresh `origin/main` and check whether this branch has any
commits left to merge:

```bash
(
  set -eu
  git fetch origin refs/heads/main:refs/remotes/origin/main
  RECOVERY_COMMITS=$(git --no-pager rev-list --count origin/main..HEAD)
  if [ "$RECOVERY_COMMITS" -eq 0 ]; then
    printf 'No commits to merge. Skip the rest of step 2.5 and continue to step 3.\n'
  else
    RECOVERY_HEAD=$(git --no-pager rev-parse HEAD)
    MERGED_COMMIT=$(gh api --method GET --paginate "repos/$REPO/pulls" \
      -f state=closed -f base=main -f "head=${REPO%%/*}:$RECOVERY_BRANCH" -f per_page=100 \
      --jq ".[] | select(.merged_at != null and .head.sha == \"$RECOVERY_HEAD\") | .merge_commit_sha")
    if [ -n "$MERGED_COMMIT" ]; then
      git --no-pager merge-base --is-ancestor "$MERGED_COMMIT" origin/main
      printf 'This branch head was already merged. Skip the rest of step 2.5 and continue to step 3.\n'
    else
      printf '%s commits to merge. Continue with the push and PR instructions below.\n' "$RECOVERY_COMMITS"
    fi
  fi
)
```

If a lookup or ancestry check fails, stop; do not infer that there is nothing
left to merge. Squash merging changes commit IDs, so a nonzero count alone does
not prove that another PR is needed. The block also checks for a merged PR with
this exact branch head and verifies its merge commit is on the refreshed `main`.

**Either skip message: continue directly to step 3**, skipping the push, both PR
blocks, and the automerge/watch commands below. Step 3 switches to and updates
`main`; keep the old branch intact. **The message says to continue with the push:
follow the remaining instructions below.**

Collect all results from any existing PR before a corrective push. Batch the
reviewed commits into this single push:

```bash
git push -u origin "$RECOVERY_BRANCH"
```

**Choose exactly one PR block below.** A previously merged or closed PR does
not count as an open PR for this work.

**No open PR for this branch:** create one. This command prompts for its description:

```bash
gh pr create --repo "$REPO" --base main --head "$RECOVERY_BRANCH" \
  --title "$RECOVERY_TITLE"
```

**An open PR already exists for this branch:** run this block instead of the
creation block. The first command sets its title; run the second interactively
and select **Body** to edit its description:

```bash
gh pr edit "$RECOVERY_BRANCH" --repo "$REPO" --title "$RECOVERY_TITLE"
gh pr edit "$RECOVERY_BRANCH" --repo "$REPO"
```

Describe the corrections and generated notes, record actual validation results
and pending native checks, and reference the release tracker without closing it.
**After either PR block**, enable squash automerge and watch the checks for RRT:

```bash
RECOVERY_PR=$(gh pr view "$RECOVERY_BRANCH" --repo "$REPO" --json url --jq .url)
gh pr merge "$RECOVERY_PR" --repo "$REPO" --auto --squash
gh pr checks "$RECOVERY_PR" --repo "$REPO" --watch
gh pr view "$RECOVERY_PR" --repo "$REPO" --json state,mergeCommit
```

Consumers use their required merge policy. Collect all checks and reviews before
batching any corrective push. Passing checks or enabling automerge does not mean
the PR has merged: require `state` to be `MERGED`, then continue to step 3 for the
new main commit's validation. Keep the publication freeze, old remote tag, and
old draft throughout this step.

### 3. Wait for evidence for the final commit

Synchronize a clean `main` checkout after the corrective PR merges, or after
step 2.5 confirms no commits remain or this branch head was already merged:

```bash
git switch main
git pull --ff-only
RELEASE_COMMIT=$(git --no-pager rev-parse HEAD)
printf 'RELEASE_COMMIT=%s\n' "$RELEASE_COMMIT"
```

Before accepting `RELEASE_COMMIT`, confirm the generated release notes cover the
final main history under the repository's changelog policy, including intervening
merges. If required entries are missing, stop before step 4: regenerate the notes
through a PR using step 2, merge it, then repeat this step's checkout selection
and complete final-main CI wait for the new commit.

Review any intervening changes before selecting this commit. Require its exact
successful **push-to-main** producer, all configured native/package checks, and
signed evidence from one complete attempt. Record the new run, attempt, immutable
artifact IDs, and digests. Earlier PR checks or evidence for the superseded
commit cannot satisfy this step. In RRT, follow
[the main CI wait](RELEASING.md#1-synchronize-main-and-wait-for-ci).

Benchmark consumers must also run
[Step 2 with a trusted installed runtime outside the observed checkout](REUSING_BENCHMARKS.md#step-2-freeze-final-release-evidence).
For reused measurements, supply the original independently retained acquisition
policy/digests and any publisher receipt. If step 2 of this recovery guide
required a new acquisition, use that acquisition's newly frozen handoff instead;
retain the superseded evidence separately. Save a **new** acceptance outside the
checkout and review its native selection. This verification does not measure
again or change the selected acquisition's provenance.

### 4. Replace the superseded draft and tag

**Follow 4.1–4.4 in order.** Creating the local tag, deleting the old draft,
and pushing the tag are separate operations. Only the push starts RRT's
preparation run that creates the new draft.

#### 4.1. Refresh machine-local dependency evidence

Before changing the local tag, check dependency provenance in the local
environment that will run `just tag`:

```bash
just release-dependencies
```

`just tag` also requires valid source-build receipts. Successful hosted CI does not
refresh machine-local receipts. Changes to RRT's Python code, dependency inputs, checkout
location, or interpreter/platform can invalidate those receipts even when the
dependency versions have not changed.

If the check reports missing or stale source-build receipts, rebuild the
configured dependencies and verify again:

```bash
just release-dependency-builds
just release-dependencies
```

Require success before continuing; investigate other failures instead of treating
them as stale receipts. Rebuilding local evidence alone does not require another
PR or main CI run. If tracked inputs need corrections, return to steps 2 and 3.

#### 4.2. Create or inspect the replacement local tag

Require the values recorded in steps 1 and 3 and a clean checkout at that exact
validated commit. `just tag "$TAG" --commit "$RELEASE_COMMIT" --force` runs the metadata,
dependency and release-note checks before replacing the **local** annotated tag.
It does not push or publish. The old local ref remains in place if those checks
fail; there is no delete-and-restore interval.

If you already created the replacement local tag, the block inspects it instead
of trying to create it again. Its commit must equal `RELEASE_COMMIT`; review its
annotation against `just release-notes "$TAG"` before accepting it. Stop on any
unexpected object or annotation. Use the argument-forwarding recipe from
[step 2.2](#22-finish-corrections-and-establish-validation-once). If the validated
checkout still has the older recipe, return to steps 2 and 3 before continuing.

```bash
(
  set -eu
  : "${TAG:?Restore TAG from step 1}" "${REPO:?Restore REPO from step 1}"
  : "${OLD_TAG_OBJECT:?Restore OLD_TAG_OBJECT from step 1}"
  : "${OLD_RELEASE_ID:?Restore OLD_RELEASE_ID from step 1}"
  : "${RELEASE_COMMIT:?Restore RELEASE_COMMIT from step 3}"
  test -z "$(git --no-pager status --porcelain)"
  test "$(git --no-pager rev-parse HEAD)" = "$RELEASE_COMMIT"
  CURRENT_TAG_OBJECT=$(git --no-pager rev-parse --verify "refs/tags/$TAG")
  if [ "$CURRENT_TAG_OBJECT" = "$OLD_TAG_OBJECT" ]; then
    just tag "$TAG" --commit "$RELEASE_COMMIT" --force
  else
    printf 'Inspecting the existing replacement local tag: %s\n' "$CURRENT_TAG_OBJECT"
  fi
  test "$(git --no-pager cat-file -t "refs/tags/$TAG")" = tag
  test "$(git --no-pager rev-parse "refs/tags/$TAG^{commit}")" = "$RELEASE_COMMIT"
  git --no-pager tag -l --format='%(contents)' "$TAG"
)
```

After that block succeeds and the annotation is reviewed, record the new object:

```bash
NEW_TAG_OBJECT=$(git --no-pager rev-parse --verify "refs/tags/$TAG")
printf 'NEW_TAG_OBJECT=%s\n' "$NEW_TAG_OBJECT"
```

This assignment only records the reviewed object's ID for the push. It does not
create another tag. Keep it with the earlier recovery values.

#### 4.3. Inspect and delete the superseded draft

Keep the publication freeze and confirm no preparation/publication writer is
running for this candidate. Recheck the remote ref, release listing and registry
without overwriting `OLD_*`. Require the same old remote object and either the
recorded draft or no draft. If you already deleted that draft, a successful empty
listing lets you continue; a failed lookup does not. A different draft stops
recovery. Review the displayed assets before running the deletion block.

```bash
(
  set -eu
  : "${OLD_TAG_OBJECT:?Restore OLD_TAG_OBJECT from step 1}"
  : "${OLD_RELEASE_ID:?Restore OLD_RELEASE_ID from step 1}"
  REMOTE_TAG=$(git --no-pager ls-remote --exit-code origin "refs/tags/$TAG")
  test "${REMOTE_TAG%%[[:space:]]*}" = "$OLD_TAG_OBJECT"
  CURRENT_RELEASE_ID=$(candidate_draft_id)
  test "$CURRENT_RELEASE_ID" = "$OLD_RELEASE_ID" || test "$CURRENT_RELEASE_ID" = none
  just release-registry "$TAG" --expect absent
  if [ "$CURRENT_RELEASE_ID" != none ]; then
    gh api "repos/$REPO/releases/$OLD_RELEASE_ID" \
      --jq '{id, tag_name, draft, published_at, immutable, assets: [.assets[] | {id, name, digest}]}'
  fi
)
```

After reviewing that output, recheck its unpublished draft state immediately
before deleting it. A failed lookup or a changed state stops this block:

```bash
(
  set -eu
  : "${OLD_RELEASE_ID:?Restore OLD_RELEASE_ID from step 1}"
  CURRENT_RELEASE_ID=$(candidate_draft_id)
  if [ "$CURRENT_RELEASE_ID" = none ]; then
    printf 'No old draft remains; continue to 4.4.\n'
  else
    test "$CURRENT_RELEASE_ID" = "$OLD_RELEASE_ID"
    RELEASE_IS_DRAFT=$(gh api "repos/$REPO/releases/$OLD_RELEASE_ID" \
      --jq ".tag_name == \"$TAG\" and .draft == true and .published_at == null and .immutable == false")
    test "$RELEASE_IS_DRAFT" = true
    gh api --method DELETE "repos/$REPO/releases/$OLD_RELEASE_ID"
  fi
)
```

Keep the publication freeze in force: this check and deletion are separate API
requests, so the check cannot eliminate the concurrent-publication race window.
Use the [release-ID deletion endpoint](https://docs.github.com/en/rest/releases/releases#delete-a-release).
Leave the remote tag in place until the guarded replacement push. In particular,
do not combine draft removal with an unconditional remote tag deletion.

#### 4.4. Push the replacement tag to start preparation

Use the existing replacement tag; do not run `just tag` again. Push the reviewed
new object only if the remote tag still equals the recorded old object:

```bash
(
  set -eu
  : "${OLD_TAG_OBJECT:?Restore OLD_TAG_OBJECT from step 1}"
  : "${NEW_TAG_OBJECT:?Restore NEW_TAG_OBJECT from step 4.2}"
  : "${RELEASE_COMMIT:?Restore RELEASE_COMMIT from step 3}"
  test -z "$(git --no-pager status --porcelain)"
  test "$(git --no-pager rev-parse HEAD)" = "$RELEASE_COMMIT"
  test "$(git --no-pager rev-parse "refs/tags/$TAG")" = "$NEW_TAG_OBJECT"
  test "$(git --no-pager rev-parse "refs/tags/$TAG^{commit}")" = "$RELEASE_COMMIT"
  git push --force-with-lease="refs/tags/$TAG:$OLD_TAG_OBJECT" \
    origin "$NEW_TAG_OBJECT:refs/tags/$TAG"
)
```

The explicit expected-object form of
[`--force-with-lease`](https://git-scm.com/docs/git-push#Documentation/git-push.txt---force-with-leaseltrefnamegtltexpectgt)
rejects a changed remote ref. If a command fails or its result is uncertain,
inspect both refs before retrying; do not rerun this block blindly or replace
the lease with `--force`. If the remote already equals `NEW_TAG_OBJECT`, the
push succeeded: continue to step 5. Pushing that unchanged tag again does not
start another preparation run. A rejected push does not replace the remote ref.

### 5. Verify the new candidate, then resume publication

Compare the new local object and commit with both remote ref lines:

```bash
git --no-pager rev-parse "refs/tags/$TAG" "refs/tags/$TAG^{commit}"
git --no-pager ls-remote --exit-code origin "refs/tags/$TAG" "refs/tags/$TAG^{}"
```

For RRT, the tag push starts a **new run of the existing `prepare-release.yml`
workflow**, which creates the draft and stages assets for the corrected commit.
The push itself does not synchronously create a draft; wait for that run to
finish. Consumers use the replacement procedure agreed
under [their writer's contract](#check-the-consumers-draft-writer). Watch the new
run for that tag and full commit; do not rerun the old preparation run, which
still names the old commit. If the expected trigger does not start, inspect
workflow/ref policy before proceeding.

Require a new draft ID, new target/creation receipts where used, and freshly
verified assets and promotion evidence. Never carry an old captured draft target
or upload receipt into this draft. Benchmark raw data may survive the replacement
when its reuse contract passes; its final acceptance and attachment must match
the new commit and draft. RRT itself has no release benchmark.

Return to the normal guide's draft review and publication steps. For RRT, start
with [retained evidence verification](RELEASING.md#4-verify-the-final-release-commit)
and continue through draft review. After the replacement passes these checks,
coordinate lifting the freeze and re-enable the publication workflows before
publishing the new draft. For RRT:

```bash
gh workflow enable publish.yml --repo "$REPO"
gh api "repos/$REPO/actions/workflows/publish.yml" --jq .state
```

Require `active` before publication. Keep the release tracker open until registry
publication and required post-publication verification succeed. Continue with
[publishing the reviewed GitHub Release](RELEASING.md#7-publish-the-reviewed-github-release),
then [approving the PyPI deployment](RELEASING.md#8-approve-the-pypi-deployment)
and completing registry, consumer, and applicable Zenodo verification.
