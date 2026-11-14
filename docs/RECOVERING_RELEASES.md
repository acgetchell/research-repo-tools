# Recovering an unfinished release

Use this guide when a release already has a tag, a draft, or retained evidence.
It applies to research-repo-tools and consumers of its release tooling. Keep the
repository's normal release guide for package-specific checks, workflow names,
benchmark commands, and publication approvals.

## Choose the recovery path

| Current state | Start here | What stays valid? |
| --- | --- | --- |
| The intended commit is unchanged; preparation timed out waiting for healthy main CI | Wait for the complete original main run, then resume under the owning workflow's recovery contract. RRT permits retrying the failed preparation job. | Keep the tag. Reuse successful, unexpired evidence from that complete attempt. |
| The intended commit is unchanged; draft creation or upload failed | Inspect the captured release ID, journal/receipt, and remote assets before retrying. An uncertain creation needs explicit reconciliation. | Preserve original receipts and accepted bytes. Follow the owning workflow's partial-draft recovery rules. |
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
gh workflow disable publish.yml --repo "$REPO"
gh api "repos/$REPO/actions/workflows/publish.yml" --jq .state
```

Require `disabled_manually`. Inspect queued and active publication runs for
`$TAG`, cancel those candidate runs, and confirm they have stopped before
proceeding. Leave runs for other tags unchanged. Disabling a workflow
does not prevent manual GitHub publication through the UI or API, so maintain
the coordinated freeze throughout replacement. Keep the preparation workflow
enabled for step 5. See [GitHub CLI workflow disable](https://cli.github.com/manual/gh_workflow_disable).

### 1. Record the existing candidate

Record the annotated tag object and the commit it names, then compare the remote
ref with that exact object:

```bash
OLD_TAG_OBJECT=$(git --no-pager rev-parse --verify "refs/tags/$TAG")
OLD_COMMIT=$(git --no-pager rev-parse --verify "refs/tags/$TAG^{commit}")
git --no-pager cat-file -t "$OLD_TAG_OBJECT"
git --no-pager ls-remote --exit-code origin "refs/tags/$TAG"
gh api --paginate "repos/$REPO/releases?per_page=100" \
  --jq ".[] | select(.tag_name == \"$TAG\") | {id, tag_name, draft, published_at, immutable}"
```

Require an annotated `tag`, matching local/remote object IDs, and either no
matching release or exactly the expected unpublished draft. Record its numeric
ID as `OLD_RELEASE_ID` when present. If a local tag is missing or differs,
reconcile it with the recorded remote candidate
before using this procedure. A draft without its expected remote tag needs the
owning writer's recovery first. Stop if any matching release is published or
immutable. Authenticated release listings include drafts only for users with
the necessary access; see the [GitHub releases API](https://docs.github.com/en/rest/releases/releases#list-releases).

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

Keep the remote tag and draft while preparing the correction. Start from current
`main` on a normal corrective branch. Retain the prepared version and declared
release date; do not rerun `just update` or the version bump solely to recover.
You can complete step 1's read-only inventory before committing. Put required
source, recipe, and documentation corrections in **one corrective PR**. Commit
those changes first so git-cliff can include their history, then regenerate and
commit the notes in that same PR. A separate documentation PR or another version
bump is unnecessary. If the source correction already merged, include only the
remaining recipe/documentation corrections and generated notes. Merge the whole
PR and wait for its final main CI before replacing the remote candidate.

#### Create the corrective branch

From the current `main` checkout, create the branch before staging or committing:

```bash
git --no-pager status --short --branch
RECOVERY_BRANCH="fix/recover-$TAG"
RECOVERY_TITLE="chore(release): prepare $TAG notes"
git switch -c "$RECOVERY_BRANCH"
```

If you already generated the notes on `main`, this carries those uncommitted
changes onto the new branch. Do not discard them or pull over local changes.
If you already have a corrective branch, keep it and set `RECOVERY_BRANCH` to
its name instead of creating another one.

#### Validate and commit any remaining corrections

**When all corrections are already on `main` and only generated notes remain,
set `RECOVERY_TITLE="chore(release): prepare $TAG notes"` and skip this subsection.**
If corrections are already committed on the existing
corrective branch, retain those commits and validate the branch below. When no
new source, recipe, or documentation changes remain, skip the staging and commit
block and continue to note regeneration. Otherwise finish those changes first.
For benchmark consumers, decide whether fresh Step 1 acquisition is required using
the
[benchmark recovery decision table](REUSING_BENCHMARKS.md#release-recovery-decisions).
Include any new benchmark outputs in the reviewed correction.

Use `just check` while editing. Once the corrections are ready, run RRT's local
validation below; consumers use their repository's corresponding required checks:

```bash
RECOVERY_TITLE="fix(release): correct recovery for $TAG"
just ci
just security
just review
```

`just review` covers the branch's committed corrections and local changes together.
Inspect the completed review and fix valid findings before committing. A review
that reports findings can still exit successfully. Stop on an incomplete review
or a paid-consent request; follow the repository's review policy before continuing.

Stage only new reviewed corrections in the interactive patch selection. Leave
generated changelog changes unstaged for the next subsection; add reviewed new
files by their explicit paths if needed:

```bash
git --no-pager diff
git add --patch
git --no-pager diff --cached --check
git --no-pager diff --cached
git commit -m "$RECOVERY_TITLE"
```

Choose a more specific corrective title when appropriate. Keep that title for
the PR so its substantive changes remain visible in release history. If notes
were generated before this commit, regenerate them below to include it.

#### Regenerate the notes

An existing local tag delimits git-cliff history. Temporarily remove **only that
local ref**, generate prospective notes, and restore the original object even if
generation fails. With `TAG` and `OLD_TAG_OBJECT` from step 1:

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
  just changelog-preview --tag "$TAG" || exit "$?"
  just changelog-release "$TAG" || exit "$?"
)
```

If the shell was forcibly killed, inspect the local ref before continuing. When
it is absent, `git update-ref "refs/tags/$TAG" "$OLD_TAG_OBJECT" ""` restores it
only if it remains absent. Never overwrite an unexpected local object.

#### Validate and commit the generated notes

Review the generated changelog and any archives. The paths below are RRT's
configured outputs; consumers use their configured changelog and archive paths.
Do not hand-edit generated notes.

```bash
git --no-pager status --short
git --no-pager diff -- CHANGELOG.md docs/archives/changelog
just changelog-check
just release-version-check
```

Inspect any new archive files listed by `status` as well; unstaged diffs do not
show untracked files.

For a notes-only PR that skipped the earlier validation subsection, run these now:

```bash
just ci
just security
```

If those checks already passed for the corrective commit and only generated
notes changed afterward, retain those results and run the note checks above
plus any additional checks required by the repository. Further source, recipe,
or documentation edits require their affected checks before proceeding.

Review the complete branch and remaining local changes together:

```bash
just review
```

Resolve valid findings and require a completed review before committing. If
generation produced no changes, skip the notes commit. Otherwise, after
reviewing every generated output, stage and commit them:

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
title only when the PR contains generated notes alone, avoiding another refresh just to record the
notes-only PR. A PR containing corrections keeps its corrective title.

#### Push the branch and merge the PR

Require all intended changes to be committed. Batch the reviewed commits into
one push, then open the PR; `gh pr create` prompts for its description. If this
branch already has a PR, push to it and update its description. For a PR containing
corrections, retain a specific corrective title that still describes the changes;
replace an outdated notes-only preparation title with the chosen `RECOVERY_TITLE`.
For a notes-only PR, use the preparation title. Do not run `gh pr create` again:

```bash
git push -u origin "$RECOVERY_BRANCH"
gh pr create --repo "$REPO" --base main --head "$RECOVERY_BRANCH" \
  --title "$RECOVERY_TITLE"
```

Describe the corrections and generated notes, record actual validation results
and pending native checks, and reference the release tracker without closing it.
For RRT, enable squash automerge and watch the checks:

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

Synchronize a clean `main` checkout after the corrective PR merges:

```bash
git switch main
git pull --ff-only
RELEASE_COMMIT=$(git --no-pager rev-parse HEAD)
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

First create and inspect the replacement **local** tag while the remote tag and
draft remain intact. Require a clean checkout at the validated final commit.
The exit trap restores the old local tag if creation fails before a new ref
exists; it never overwrites another object:

```bash
(
  set -eu
  test -z "$(git --no-pager status --porcelain)"
  test "$(git --no-pager rev-parse HEAD)" = "$RELEASE_COMMIT"
  test "$(git --no-pager rev-parse "refs/tags/$TAG")" = "$OLD_TAG_OBJECT"
  restore_missing_tag() {
    if ! git --no-pager rev-parse --verify "refs/tags/$TAG" >/dev/null 2>&1; then
      git update-ref "refs/tags/$TAG" "$OLD_TAG_OBJECT" ""
    fi
  }
  trap restore_missing_tag EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  git update-ref -d "refs/tags/$TAG" "$OLD_TAG_OBJECT" || exit "$?"
  just tag "$TAG" || exit "$?"
  test "$(git --no-pager rev-parse "refs/tags/$TAG^{commit}")" = "$RELEASE_COMMIT"
  git --no-pager tag -l --format='%(contents)' "$TAG"
)
```

After that block succeeds and the annotation is reviewed, record the new object:

```bash
NEW_TAG_OBJECT=$(git --no-pager rev-parse --verify "refs/tags/$TAG")
```

Immediately before changing the remote candidate, repeat step 1's remote ref,
all-release, and exact-registry checks. Keep the recorded `OLD_*` values; compare
fresh remote observations against them. The local tag now intentionally differs.
Require the same old remote object and the same unpublished draft (or continued
absence if no draft existed). Confirm no preparation/publication writer is still
running. Stop if another operator changed the candidate or published it. The Git
lease below protects the tag ref; it cannot prevent concurrent GitHub publication.

If there is no draft, skip its deletion and continue to tag replacement below.
Otherwise set the recorded draft ID, inspect its assets, and delete that exact
draft only after confirming it remains the superseded unpublished candidate:

```bash
OLD_RELEASE_ID=REPLACE_WITH_THE_RECORDED_DRAFT_ID
gh api "repos/$REPO/releases/$OLD_RELEASE_ID" \
  --jq '{id, tag_name, draft, published_at, immutable, assets: [.assets[] | {id, name, digest}]}'
```

After reviewing that output, recheck its unpublished draft state immediately
before deleting it. A failed lookup or a changed state stops this block:

```bash
(
  set -eu
  RELEASE_IS_DRAFT=$(gh api "repos/$REPO/releases/$OLD_RELEASE_ID" \
    --jq '.draft == true and .published_at == null and .immutable == false')
  test "$RELEASE_IS_DRAFT" = true
  gh api --method DELETE "repos/$REPO/releases/$OLD_RELEASE_ID"
)
```

Keep the publication freeze in force: this check and deletion are separate API
requests, so the check cannot eliminate the concurrent-publication race window.
Use the [release-ID deletion endpoint](https://docs.github.com/en/rest/releases/releases#delete-a-release).
Leave the remote tag in place until the guarded replacement push. In particular,
do not combine draft removal with an unconditional remote tag deletion.

Push the reviewed new object only if the remote tag still equals the old object:

```bash
(
  set -eu
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
the lease with `--force`. A rejected push does not replace the remote ref.

### 5. Verify the new candidate, then resume publication

Compare the new local object and commit with both remote ref lines:

```bash
git --no-pager rev-parse "refs/tags/$TAG" "refs/tags/$TAG^{commit}"
git --no-pager ls-remote --exit-code origin "refs/tags/$TAG" "refs/tags/$TAG^{}"
```

For RRT, the tag push starts a new preparation run to create the draft and stage
assets for the corrected commit. Consumers use the replacement procedure agreed
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
with [retained evidence verification](RELEASING.md#4-verify-the-release-commit)
and continue through draft review. After the replacement passes these checks,
coordinate lifting the freeze and re-enable the publication workflows before
publishing the new draft. For RRT:

```bash
gh workflow enable publish.yml --repo "$REPO"
gh api "repos/$REPO/actions/workflows/publish.yml" --jq .state
```

Require `active` before publication. Keep the release tracker open until registry
publication and required post-publication verification succeed.
