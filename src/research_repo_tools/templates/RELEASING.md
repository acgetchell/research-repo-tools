# Releasing

Prepare each `vX.Y.Z` release in a dedicated PR. After that PR is merged,
create the annotated tag and a draft GitHub release. Review and publish the
draft with `just release-publish`, then approve the registry deployment to
permit the package upload.

The changelog is generated for the target tag before the tag exists, so the
release process does not require a temporary local tag. Repeating
`changelog-release` with unchanged history and configuration reuses the prepared
citation date. The `declared` date policy permits preparation and tagging on
different UTC days when citation and changelog dates agree.

## Prepare the environment

Set the target tag once:

```bash
TAG=vX.Y.Z
```

Verify GitHub authentication and the repository remotes, then synchronize
`main`:

```bash
gh auth status
git --no-pager remote -v
git switch main
git pull --ff-only
```

Install or verify the declared development tools before running maintenance
recipes. Install uv, keep an exact published research-repo-tools pin in the
locked tooling group, and start with a clean working tree. Setup installs
user-level Just; use `just` without activating an environment:

```bash
just setup
```

Refresh tools and dependencies before creating the release branch:

```bash
just update
```

Review any tracked dependency or tool changes and land them separately before
continuing with the release PR, then synchronize `main` again. Dependency and
tool upgrades remain independently reviewable; `just update-version`
deliberately does not run `just update`. Ensure the declared git-cliff version
is installed and registry publishing and deployment approval are configured.

## Step 1: Prepare the release PR

Keep the release PR focused on version metadata, the generated changelog,
and release documentation. Major code changes should already be on `main`.

### 1. Create the release branch

```bash
git switch -c "release/$TAG"
```

### 2. Update release metadata

```bash
just update-version "$TAG"
```

The recipe discovers the previous published stable GitHub release and prepares
today's UTC date. Pass `--previous-release TAG` or `--date YYYY-MM-DD` to select
those inputs deliberately. Review the resulting metadata diff before continuing.

### 3. Generate the release changelog

```bash
just changelog-release "$TAG"
```

This generates `CHANGELOG.md` as though the target tag already existed,
archives completed minor series, and uses `date-released` from `CITATION.cff`.
The packaged configuration sets `changelog.date-source = "citation"`, so no
separate date variable is needed. Use `just changelog-preview --tag "$TAG"` for
a read-only preview with the same prepared date. Do not edit generated notes.

If the repository requires release performance evidence, run its configured
preparation, measurement, and README publication steps before validation.
Consumers retain their scientific workload and benchmark policies. The shared
commands are `just performance-prepare tooling/performance-release.toml`,
`just performance-release tooling/performance-release.toml`, and
`just performance-readme tooling/performance-release.toml`, in that order.
README retries consume retained results without repeating measurements.
Fetching tags requires explicit `--fetch-tags --allow-git-mutations` during
preparation.

### 4. Validate the release branch

```bash
just ci
just release-version-check
```

Consumers retain their packaging, feature, MSRV, platform and scientific checks.
Run additional registry dry-run checks here if they are not already part of
`just ci`. The final version check requires matching metadata and complete,
dated release notes.

### 5. Review, stage, and commit the release artifacts

Inspect all changes before staging:

```bash
git --no-pager status --short
git --no-pager diff
```

Stage only reviewed paths, including package metadata, lockfiles, `CITATION.cff`,
`CHANGELOG.md`, and generated archives. Do not stage unrelated work or the entire
`docs/` tree without inspecting it. Then inspect and commit the staged delta:

```bash
git --no-pager diff --cached

git commit -m "chore(release): release $TAG

- Bump version to $TAG
- Update citation and package metadata
- Generate the release changelog
- Update release documentation"
```

### 6. Push the branch and open the PR

```bash
git push -u origin "release/$TAG"
```

Use `chore(release): release $TAG` as the PR title and describe the PR as a
focused release preparation without feature work. Include validation results
and skipped checks. Resolve review findings before merging.

### Handling fixes found during preparation

Commit required corrections, regenerate notes, review and commit the generated
outputs separately, and repeat affected checks and the final release gate.
File issues for deferred work instead of hand-editing the generated changelog.
Retries preserve the declared release date unless the maintainer deliberately
advances it with `update-version` and regenerates the matching changelog.

### Resuming an unpublished release after another PR

A corrective PR can regenerate affected artifacts and validate them. Do not
repeat the version bump or dependency updates solely because another PR is
needed. After merge, resume Step 2 from the corrected clean `main` commit and
require its complete validation. Reconcile any existing unpublished tag, draft,
and superseded workflow runs through the repository's recovery procedure.
Never replace a published release; use a new version.

## Step 2: Publish after the PR is merged

### 1. Synchronize `main`

```bash
git switch main
git pull --ff-only
```

Use a clean checkout of the reviewed release commit. Require its configured
native checks and trusted main-push evidence before any tag-triggered promotion.

### 2. Create and verify the annotated tag

```bash
just tag "$TAG"
git --no-pager tag -l --format='%(contents)' "$TAG"
```

`just tag` first runs `release-version-check`, then builds the annotation from
the matching active or archived changelog section. Use `just tag-preview "$TAG"`
for an optional read-only preview. Never move a published tag.

### 3. Push the tag

```bash
git push origin "$TAG"
```

### 4. Verify the release commit

Require the repository's successful native checks, package producer, and
retained evidence for this exact commit in a complete trusted main-push attempt.
Use the repository's evidence workflow or adapter to verify its artifact policy.
PR, manual-dispatch and partial-rerun evidence cannot substitute for it.

### 5. Create the draft GitHub release

Let the repository's preparation workflow create the draft and required assets.
If no workflow creates a draft, create it in GitHub from the existing tag and
notes. Keep the title identical to the tag, including its leading `v`, and leave
it as a stable draft until asset preparation and review are complete.

### 6. Review the draft

Inspect the draft, successful preparation run, required assets, and generated
notes before publishing:

```bash
just release-notes "$TAG"
```

Complete any repository-specific benchmark or artifact attachment workflow.
Do not move the tag or publish while preparation is running.

### 7. Publish the reviewed draft and approve registry publication

From the clean tagged checkout, using your human GitHub CLI session, run:

```bash
just release-publish "$TAG"
```

This command authorizes GitHub Release publication. The shared gate checks the
checkout, metadata, tag, reviewed commit, CI and required assets. Approve the
resulting registry deployment in GitHub and wait for the upload workflow to
complete. Keep automated drafts unpublished until review.

### 8. Verify registry availability and downstream resolution

After the publishing workflow completes:

```bash
just release-verify "$TAG"
```

The recipe makes up to seven attempts at ten-second intervals for registry
visibility. Check installation from the exact published version in a fresh
consumer and record the release, workflow, artifact, and consumer evidence in
the release PR or tracking issue.

### Recovering a failed run

Inspect a failed run and exact registry version before retrying. Cargo can
report a timeout after a successful upload. Verify an existing publication
instead of uploading again. Registry lookup errors never prove absence.
Corrections to published contents require a new version. If the intended date
changes, prepare it with `just update-version "$TAG" --date YYYY-MM-DD`,
regenerate notes, and repeat validation and review before tagging.

### 9. Verify the Zenodo release metadata

If Zenodo integration is enabled, verify the new version-specific archive and
its citation metadata. The concept landing page alone is not evidence for the
new record. Record the release DOI without moving the published tag.

### 10. Remove the merged release branch

After registry, GitHub, asset, consumer, and applicable Zenodo verification
succeed:

```bash
git branch -d "release/$TAG"
git push origin --delete "release/$TAG"
```

## Configure Trusted Publishing before the next release

Configure the repository's registry publisher and required deployment approvals
before starting. Keep account setup in the maintainer's private checklist.
Workflow YAML does not configure these accounts. Re-read live settings before
changing them, preserving newer entries.
