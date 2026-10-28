# Releasing research-repo-tools

Prepare each `vX.Y.Z` release in a dedicated PR. After that PR is merged,
wait for its complete main CI run, then create and push the annotated tag.
The tag push starts `Prepare GitHub release`, which creates the draft and
attaches the validated wheel, sdist, and signed evidence. Review and publish
the draft with `just release-publish`. Publishing the GitHub release starts
`Publish to PyPI`; approve its `pypi` deployment to permit the package upload.

The changelog is generated for the target tag before the tag exists, so the
release process does not require a temporary local tag.

Release recipes are content-idempotent where their inputs permit it. Repeating
`update-version` on the same UTC date produces no file changes. Rerunning
`update-version` on a later UTC date intentionally advances the declared release
date. Crossing midnight alone requires no update: the `declared` date policy
allows preparation and tagging on different UTC days when citation and changelog
dates agree. With unchanged history and configuration, repeating
`changelog-release` reuses the declared citation date even after UTC midnight.

## Contents

- [Reusable CI evidence](#reusable-ci-evidence)
- [Prepare the environment](#prepare-the-environment)
- [Step 1: Prepare the release PR](#step-1-prepare-the-release-pr)
- [Step 2: Publish after the PR is merged](#step-2-publish-after-the-pr-is-merged)
- [Configure or verify Trusted Publishing](#configure-trusted-publishing-before-the-next-release)

## Reusable CI evidence

After a protected `main` push, `Package checks` builds the wheel and sdist once,
validates those files on Linux, macOS, and Windows and through installed changelog
consumers, then signs the original validation evidence in `release-evidence`.
CI retains `distributions-ATTEMPT` and `validated-evidence-ATTEMPT` for 30 days.

`Prepare GitHub release` verifies this retained evidence for the tagged commit.
It requires the exact successful main-push run, all required jobs and steps from
the same attempt, and nonexpired artifacts retrieved by immutable ID. It validates
ZIP digests and inventories, distribution metadata and hashes, source commit,
version, and the original validation signature without rebuilding. PR,
manual-dispatch and partial-rerun results cannot replace main-push evidence.

The publishing workflow additionally requires a matching published release event,
verifies the original main-validation and promotion signatures, and rechecks live
validation results and artifact identities before deployment approval and upload.
Local validation cannot substitute for these workflow gates.

The release commands share Delaunay's operator-facing roles:

| Recipe | Purpose |
| --- | --- |
| `changelog-preview [--tag TAG] [--date DATE]` | Preview generation without publishing files |
| `changelog-release TAG [DATE]` | Generate prospective notes using the prepared citation date by default |
| `publish-check` | Check the current built wheel and sdist with strict Twine validation |
| `release-evidence` | Verify the clean checkout's retained signed main CI evidence without rebuilding |
| `release-notes TAG` | Print the generated notes for the tag and GitHub release |
| `release-publish TAG` | Check the reviewed draft and approve GitHub publication |
| `release-verify TAG` | Verify GitHub assets and the exact PyPI version |
| `release-version-check` | Require synchronized metadata and matching final changelog and citation dates |
| `tag TAG` | Create the annotated tag after the final version check |
| `update-version TAG [ARGS...]` | Prepare synchronized release metadata |

This package has no release benchmark or README performance snapshot to generate.
Its `publish-check` validates the built wheel and sdist for PyPI using pinned
Twine with warnings treated as errors. It runs inside `just ci` after the build
and is also available separately. `release-evidence` inspects the retained
signed distributions locally using the same verifier as automated preparation.
Draft creation and promotion signing remain in the preparation workflow here.
Explicit `release-evidence COMMAND...` arguments still expose the shared
operations with explicit policy inputs, described in
[release evidence](VERIFYING_RELEASE_EVIDENCE.md).

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
recipes. Setup installs user-level Just and synchronizes the development
environment; see [development commands](../CONTRIBUTING.md#development-environment)
for uv and native-tool prerequisites. Use Bash or Zsh, or Git Bash on Windows,
and start with a clean working tree:

```bash
just setup
```

Refresh exact direct Python development-tool pins, lockfiles, and the uv pin
before creating the release branch:

```bash
just update
```

`just update` upgrades uv through its installation owner before updating
Python dependencies. It advances exact direct dev pins, refreshes the full
Python lock, synchronizes dev, and audits the final lock. Failures stop later
steps; completed updates remain for review and retry.

Review any tracked dependency or tool changes and land them separately before
continuing with the release PR, then synchronize `main` again. Dependency and
tool upgrades remain independently reviewable; `just update-version`
deliberately does not run `just update`.

`update-version` uses published, non-draft, non-prerelease GitHub Releases as the
authoritative stable release history. Changelog generation requires the
Justfile's declared git-cliff version; install it with `just changelog-setup` if
needed. Complete [Trusted Publishing setup](#configure-trusted-publishing-before-the-next-release)
before proceeding. Git mutations and publication are maintainer actions;
assistants follow [AGENTS.md](../AGENTS.md).

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

The recipe infers the previous published stable release and atomically
synchronizes:

- `pyproject.toml` and the local package entry in `uv.lock`;
- `CITATION.cff` version and the current UTC `date-released`; and
- the target changelog heading date when that generated heading already exists.

The complete edit is planned before any file is replaced and caught failures
roll changed files back. The recipe preserves existing DOI metadata; it does not
add or rotate a version-specific DOI. It does not generate the changelog or
upgrade dependencies. Pass `--previous-release TAG` to select an explicitly
reviewed predecessor without GitHub discovery.

Review the resulting metadata diff before continuing.

### 3. Generate the release changelog

```bash
just changelog-release "$TAG"
```

This generates `CHANGELOG.md` as though the target tag already existed,
archives completed minor series under `docs/archives/changelog/`, and
uses `date-released` already recorded in `CITATION.cff`. No `RELEASE_DATE`
shell variable is needed. With this default-date path, the citation version must
match the target tag and the date must be valid `YYYY-MM-DD`; invalid metadata
stops generation before files change.

Use `just changelog-preview --tag "$TAG"` for a read-only preview with the same
date default. An explicit date is still available as the second argument to
`changelog-release`, or as `--date YYYY-MM-DD` for the preview. An explicit
date bypasses citation lookup and its version check; the shared generator still
validates the supplied date. To advance the declared date deliberately, run
`just update-version "$TAG" --date YYYY-MM-DD` before regenerating. Run the final
version check to verify the heading matches package metadata and the citation
date. Review the generated changelog and archive changes; do not edit them manually.

### 4. Validate the release branch

```bash
just ci
just release-version-check
just publish-check
```

`just ci` includes lockfile, Justfile, Python, workflow, test, package-build,
PyPI metadata, and isolated wheel/sdist installation checks. `release-version-check` invokes
the strict `research-repo-tools release check --final-release` gate, requiring
exactly one current changelog heading whose date matches `CITATION.cff`.
`publish-check` requires exactly the current version's wheel and sdist in
`dist/`, verifies their package name and version, and runs `twine check --strict`.
It catches distribution metadata and description warnings without rebuilding,
rerunning tests, or uploading. The explicit command repeats only this quick
check against the artifacts already built by `just ci`.

Use `just publish-check` alone to recheck existing artifacts after a successful
full run. If they are missing or for an older version, run
`just build publish-check`. Source changes still require their affected checks
and fresh artifacts before release.

Local validation covers only the current host; the hosted native checks remain
required. See [validation coverage](VALIDATING.md), including the
[agent Git policy](VALIDATING.md#validation-under-the-agent-git-policy).

### 5. Review, stage, and commit the release artifacts

Inspect all changes before staging:

```bash
git --no-pager status --short
git --no-pager diff
```

Expected release artifacts include package metadata and lockfiles,
`CITATION.cff`, `CHANGELOG.md`, and generated files under
`docs/archives/changelog/`. Stage only reviewed paths; do not stage unrelated
work or the entire `docs/` tree without inspecting it.

Then inspect and commit the staged release delta:

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
and skipped checks. Resolve review findings and require the configured protected
branch checks before merging.

### Handling fixes found during preparation

For a critical fix that must be included, make and commit the fix. Rerun
`just changelog-release "$TAG"`, review and stage only the regenerated release
outputs, commit that update separately, and rerun the affected checks and final
release gate.

For a non-critical fix, file an issue and defer it. Do not hand-edit the
generated changelog to add a known-issue note.

Retries preserve the declared release date unless the maintainer deliberately
advances it with `update-version` and regenerates the matching changelog.

### Resuming an unpublished release after another PR

A corrective PR can include the affected preparation steps: regenerate the
changelog and validate. Do not repeat the version bump or dependency updates
solely because another PR is needed. Once the corrected artifacts merge, resume
Step 2 from the new clean `main` commit and require its successful main-push
validation.

If the tag or draft already exists, follow the
[unpublished-candidate recovery procedure](#other-preparation-and-publication-failures).
This package's tag push starts signed artifact promotion, so cancel superseded
preparation runs and reconcile their evidence before replacing an unpublished
candidate. Never use this recovery path for a published release; publish a new
version instead.

## Step 2: Publish after the PR is merged

### 1. Synchronize `main` and wait for CI

```bash
git switch main
git pull --ff-only
```

Use a clean checkout of the reviewed release commit. If `main` has advanced,
review the intervening changes and refresh generated notes through a PR as
needed. Find the main-push `Package checks` run for this exact commit:

```bash
RELEASE_COMMIT=$(git --no-pager rev-parse HEAD)
gh run list --workflow ci.yml --event push --branch main --commit "$RELEASE_COMMIT" --limit 20
```

Set `MAIN_RUN_ID` to the matching run's ID from that listing, then wait for it:

```bash
MAIN_RUN_ID=REPLACE_WITH_THE_MATCHING_MAIN_PUSH_RUN_ID
gh run watch "$MAIN_RUN_ID" --exit-status
```

Continue only after the complete run succeeds, including all native package
checks and the `release-evidence` job. If the run has not appeared, repeat the
listing; if it fails, follow the [recovery guidance](#recovering-a-failed-run).
Keep this checkout at the verified commit through tagging and publication.

The `release-evidence` job is inside the `Package checks` workflow in `ci.yml`.
It depends on `build`, every platform in `check`, and `changelog-integration`,
so it starts only after they succeed and may not appear in the jobs list while
they are running. It signs and retains the validation evidence. The local
`just release-evidence` recipe in step 4 inspects that evidence after the tag push.

### 2. Create and verify the annotated tag

```bash
just tag "$TAG"
git --no-pager tag -l --format='%(contents)' "$TAG"
```

`just tag` first runs `release-version-check`, then builds the annotation from
the matching active or archived changelog section. For release notes larger than
125 kB of UTF-8 text, the annotation points to that section instead of embedding it.
Use `just tag-preview "$TAG"` for an optional read-only preview. Confirm the tag
resolves to the reviewed release commit; never move a published tag.

### 3. Push the tag

After the main CI wait in step 1 succeeds, push the tag. This immediately starts
`Prepare GitHub release`; step 5 watches the workflow already started here.

```bash
git push origin "$TAG"
```

### 4. Verify the release commit

```bash
just release-evidence
```

The no-argument recipe derives the stable package version and full commit from
the clean repository-root checkout. It requires the already-pushed tag to resolve
to that commit, downloads the retained distributions and signed evidence into
temporary storage, and verifies their original ZIP digests, inventories, package
metadata, file hashes, main-run signature, and live run/attempt association.
It rechecks the checkout before returning and leaves local `dist/` unchanged.
It neither rebuilds nor creates, signs, uploads, or publishes release assets.

Require successful `build`, `check (ubuntu-24.04)`, `check (macos-15)`,
`check (windows-2025)`, `changelog-integration`, and `release-evidence` jobs for
this exact commit in the same attempt. The publishing policy in `pyproject.toml`
binds its required checks to GitHub Actions app ID `15368`. PR review remains a
protected-branch requirement; it does not replace merged `main` evidence.
Missing, pending, cancelled or failed required jobs block preparation.

Record the printed JSON's full commit, run ID/attempt, artifact identities, and
file hashes in the release PR or tracking issue. Pending checks cause an immediate
failure; wait for main CI to finish, then rerun `just release-evidence`.
The preparation workflow independently requires exactly one successful matching
run from this repository's active `ci.yml`. Manual dispatch and partial reruns
cannot supply promotion evidence. If the original main-push run failed, rerun all its jobs within
GitHub's 30-day rerun window. If no eligible run exists, land a new reviewed
commit that triggers main-push CI; do not move an already published tag.

### 5. Wait for the draft GitHub release

The tag push starts `Prepare GitHub release`, which creates the draft after
verifying and signing the retained distributions. Wait for that workflow:

```bash
gh run list --workflow prepare-release.yml --event push --branch "$TAG" --commit "$RELEASE_COMMIT" --limit 20
```

Set `PREPARATION_RUN_ID` to the matching run's ID from that listing:

```bash
PREPARATION_RUN_ID=REPLACE_WITH_THE_MATCHING_PREPARATION_RUN_ID
gh run watch "$PREPARATION_RUN_ID" --exit-status
```

These commands inspect the workflow started by step 3; they do not start another
run. If it has not appeared, repeat the listing. If it already succeeded,
continue to draft review. Otherwise, wait for the complete workflow, including
staging, to succeed.
Its pending-validation wait is bounded to 30 polls at 20-second intervals;
missing, failed, expired, ambiguous or mismatched evidence fails with recovery
instructions. For a wait timeout, follow
[preparation timeout recovery](#preparation-timed-out-waiting-for-main-ci).
It never rebuilds or falls back to another commit.

Let this workflow create the draft and assets. A manually created draft cannot
replace its signed provenance. Keep the release title identical to the tag,
including its leading `v`, and leave it as a stable draft until review.

### 6. Review the draft

Open the repository's [Releases page](https://github.com/acgetchell/research-repo-tools/releases).
Inspect the draft, generated notes, and assets:

```bash
gh release view "$TAG" --json tagName,name,isDraft,isPrerelease,body,assets
just release-notes "$TAG"
```

Confirm `isDraft: true`, `isPrerelease: false`, the expected tag and title, and
notes matching `just release-notes` (or its changelog link for a long annotation).
The draft must contain exactly these three nonempty uploaded assets, with
`VERSION` replaced by `$TAG` without the leading `v`:

- `research_repo_tools-VERSION-py3-none-any.whl`
- `research_repo_tools-VERSION.tar.gz`
- `release-attestation.json`

GitHub's automatically provided source archives are separate from this inventory.
The attestation retains the original main-validation manifest and Sigstore bundle
plus a signed promotion predicate. Review the original main run/attempt,
immutable artifact IDs and ZIP digests, promoted file hashes, and successful
preparation run. Do not edit the draft, move the tag, or publish while preparation
is running. Runs for the same tag are serialized without cancelling an active run.

### 7. Publish the reviewed draft and approve PyPI publication

From the clean tagged checkout, using your human GitHub CLI session, run:

```bash
just release-publish "$TAG"
gh run list --workflow publish.yml --event release --branch "$TAG" --commit "$RELEASE_COMMIT" --limit 20
```

This command authorizes GitHub Release publication. The shared gate checks the
clean checkout, stable tag/package metadata, remote tag commit, protected default
branch ancestry, successful exact-commit checks and required assets. Human
authentication is essential: publishing with a workflow's `GITHUB_TOKEN` does
not trigger the separate `release: published` workflow.

Record the matching run's ID as `PUBLISH_RUN_ID` and open it:

```bash
PUBLISH_RUN_ID=REPLACE_WITH_THE_MATCHING_PUBLISH_RUN_ID
gh run view "$PUBLISH_RUN_ID" --web
```

If it has not appeared, repeat the filtered listing. Its `verify` job checks the
exact uploaded inventory and provider digests, then verifies both signatures.
Main validation must be signed by `ci.yml` at the exact source/signer commit and `refs/heads/main`,
with the original push run/attempt in its certificate. Promotion must be signed
by `prepare-release.yml` at that same commit and the release tag ref, binding the
retained evidence and promoted bytes. Both reject self-hosted runners.

The verifier rechecks live validation results, artifact association, the remote
tag and release asset identity before allowing upload. After `verify` succeeds
and `publish` is waiting for approval, approve the `pypi` environment deployment
in GitHub. Follow the configured reviewer policy and wait for completion:

```bash
gh run watch "$PUBLISH_RUN_ID" --exit-status
```

The upload uses PyPI trusted publishing and the verified wheel and sdist;
there is no local upload step, rebuild, or second native test matrix at release time.

### 8. Verify registry availability and downstream resolution

After the publishing workflow completes:

```bash
just release-verify "$TAG"
```

This requires the published stable GitHub release, its required assets, and the
exact version on PyPI with wheel and sdist upload evidence. It retries proven
registry absence seven times at ten-second intervals, never an upload. A
published GitHub release alone is insufficient.

Check a fresh consuming project using the
[installation instructions](../README.md#install-with-uv) with the exact
published version. Complete setup and run that consumer's focused integration
checks. Consumers retain their Rust, platform, and domain checks.

Preserve the output and record the tag, full commit, original main run/attempt,
immutable distribution/evidence artifact IDs and ZIP digests, promoted file
hashes, preparation run, publishing run, and consumer results in the release PR
or tracking issue. Keep that issue open until publication and consumer
verification succeed.

### Recovering a failed run

#### Preparation timed out waiting for main CI

If the tag was pushed before main CI finished and preparation failed only while
waiting for that run, keep the existing tag. Pushing an unchanged tag again does
not trigger another preparation run. Using the run IDs recorded in steps 1 and 5,
wait for the complete main-push run to succeed, then retry the failed preparation
job and watch it:

```bash
gh run watch "$MAIN_RUN_ID" --exit-status &&
  gh run rerun "$PREPARATION_RUN_ID" --failed &&
  gh run watch "$PREPARATION_RUN_ID" --exit-status
```

The successful main CI attempt and its artifacts are reused. If main CI itself
failed, use the full main-run recovery below. This preparation-only timeout occurs
before draft creation or upload; other failures require inspection before retrying.

#### Artifact retention and recovery window

Both `distributions-ATTEMPT` and `validated-evidence-ATTEMPT` are retained for
30 days. Complete preparation and publication within that window; the verifier
also rechecks the selected live run/attempt and immutable artifact metadata.
The original manifest and signature are preserved inside the release's
`release-attestation.json`, along with signed promotion evidence, so the origin
and promoted file hashes remain independently verifiable after Actions retention
expires. Expiration does not justify substituting a new local build.

Tag well before the retention deadline. If either artifact is missing or expired,
first inspect retention and access. GitHub permits workflow reruns only within
30 days of the original run. Within that window and with access restored,
rerun **all** jobs of the original **push to main** run for the exact tagged commit:

```bash
MAIN_RUN_ID=REPLACE_WITH_THE_RECORDED_MAIN_PUSH_RUN_ID
gh run rerun "$MAIN_RUN_ID"
gh run watch "$MAIN_RUN_ID" --exit-status
```

For this main CI evidence recovery, do not use `--failed`, `--job`, or
`gh workflow run`: those cannot establish a complete main-push
production/validation attempt. Wait for `release-evidence`
and inspect the new artifact IDs before retrying preparation. If the original
main-push run itself is unavailable or is older than the 30-day rerun window,
stop: rerunning it cannot recover the evidence. Land a new reviewed commit on
`main` through normal native CI, regenerate notes with the owning workflow, and
follow the explicitly authorized unpublished-candidate correction procedure
below. This creates a new candidate with its own complete main-push validation;
it never silently substitutes another commit for the existing tag. Preserve any
published release and use a new version if correcting published contents.
The workflow cannot manufacture equivalent evidence from an unrelated run. For a staging failure,
follow the draft recovery procedure below before rerunning the recorded
preparation run with `gh run rerun PREPARATION_RUN_ID`.

#### Other preparation and publication failures

- **Metadata, notes, or validation failed:** fix the source, regenerate notes
  after substantive commits, and repeat the affected checks before review.
- **The intended release date changed:** crossing UTC midnight alone requires no
  change under the `declared` policy. To choose a different date deliberately, run
  `just update-version "$TAG" --date YYYY-MM-DD`, then repeat changelog generation
  and validation from Step 1. The updater synchronizes an existing target heading
  before generation, avoiding conflicting dates. Review, commit, and merge the
  changes before returning to Step 2. Do not hand-edit generated dates.
- **A tag exists or its push was rejected:** inspect the local tag, remote tag,
  GitHub Release, and PyPI state before continuing. Do not force-push a published
  tag. A correction to published package contents requires a new version.
- **Preparation or asset staging failed:** inspect the failed workflow job in
  GitHub. Staging can resume an empty draft, but refuses any draft that already
  has assets. Inspect partial uploads; if the draft is incomplete, a maintainer
  must remove that incomplete draft before retrying preparation for the unchanged
  tag. Existing assets are never overwritten. Keep a complete draft for review.
- **The candidate is still unpublished and needs a source correction:** only
  with explicit maintainer authorization, cancel superseded preparation runs
  and confirm their final state before proceeding. Check both the exact PyPI
  version and all GitHub Releases, including drafts; lookup or access errors are
  not proof of absence. If either is published, stop and preserve it. Merge the
  correction and workflow-generated notes through the normal native CI and
  review gates, retaining the intended package version. Validate the corrected
  commit in a complete trusted main-push attempt. Record the old and corrected
  full SHAs and tag object IDs. Remove only a reviewed superseded unpublished
  draft and its assets, then reconcile the unpublished tag with an explicit
  expected-old-ref guard, recreate its annotation through `just tag`,
  and verify the remote tag resolves to the corrected commit before preparation.
  Never reuse superseded assets, validation, or signed evidence. Do not move a
  published tag, release, or registry artifact.

  An existing local candidate tag already delimits git-cliff history. While
  regenerating notes for an explicitly authorized unpublished correction,
  record its tag object ID, temporarily remove only that local ref with
  `git update-ref -d "refs/tags/$TAG" "$OLD_TAG_OBJECT"`, and restore that exact
  object with an exit trap. Run the ordinary `changelog-preview` and
  `changelog-release` commands during that interval. Keep the remote ref
  unchanged until the corrected reviewed commit passes main validation. This
  avoids duplicate prospective headings without hand-editing generated notes.
- **Publication is blocked:** check the reported checkout, tag, CI, or asset
  mismatch. If the workflow is waiting for approval, approve the `pypi`
  deployment in GitHub. Provenance failures require investigation of the original
  preparation run; do not substitute locally rebuilt files.
- **The upload failed or verification timed out:** inspect the original run and
  PyPI's exact version and files before retrying. An error or timeout can occur
  after an upload was accepted, and registry lookup errors never prove absence.
  Verify an existing publication instead of uploading it again. Do not follow a
  successful workflow with a second manual upload.

### 9. Verify the Zenodo release metadata

If Zenodo integration is enabled, open the new version-specific record after
it archives the GitHub release. Check that it identifies the exact release and
that its publication date, authors, ORCID, and license agree with `CITATION.cff`.
The all-versions concept landing page alone is not evidence for the new record.
Correct a mismatch before declaring the release complete.

Record the version DOI in the release PR or tracking issue. Use that DOI for
this release and the concept DOI for project-wide references. Add DOI links in
a follow-up documentation change once the record exists; do not move the
published tag to insert a newly assigned DOI.

### 10. Remove the merged release branch

After PyPI, GitHub, release-asset, consumer, and applicable Zenodo verification
succeed:

```bash
git branch -d "release/$TAG"
git push origin --delete "release/$TAG"
```

## Configure Trusted Publishing before the next release

Before starting a release, verify this exact PyPI trusted publisher identity:

| Field | Value |
| --- | --- |
| Publisher | GitHub |
| Repository owner | `acgetchell` |
| Repository name | `research-repo-tools` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

Use only the workflow filename, without `.github/workflows/`. Verify GitHub's
`pypi` environment and required maintainer-review policy, and that the human
GitHub CLI session can publish the reviewed draft and approve deployment.
`gh attestation verify --help` must support the source/signer digest and ref
policy flags used by the workflows.

Workflow YAML does not apply account configuration. Keep account-specific
registration and approval choices in the maintainer's private setup task; follow
[release configuration](CONFIGURING_GITHUB.md#release-configuration). Re-read live
settings before changing them, preserving newer entries. Do not start a second
upload to test setup after a successful publication.

For a citable archive, enable this repository in
[Zenodo's GitHub integration](https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content)
before publishing the GitHub Release. The integration assigns DOIs after
publication and cannot reserve them in advance. Keep `CITATION.cff` valid without
a DOI until an archive exists; see [Zenodo's DOI guidance](https://zenodo.org/help/versioning).
