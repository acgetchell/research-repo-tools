# Releasing research-repo-tools

Use this procedure for stable releases of the Python package. PyPI distributes
the package; GitHub hosts the source, review, CI, and release notes. Run the
command blocks in order from the repository root, keeping the release variables
in the same shell. The examples use a POSIX shell, such as Bash or Zsh; on
Windows, use Git Bash. Stop and resolve a failed command before continuing.

Git mutations and publication are maintainer actions. Assistants follow
[AGENTS.md](../AGENTS.md), including the
[validation restrictions](VALIDATING.md#validation-under-the-agent-git-policy).
Release history belongs in the generated [CHANGELOG.md](../CHANGELOG.md).

## Release workflow

1. Prepare a release PR with synchronized metadata and generated notes. Validate,
   review, and merge it into `main`.
2. Wait for the exact merged commit's successful main-push **Package checks**
   run, including `release-evidence`. It builds once, validates those files on
   Linux, macOS, Windows and through installed changelog consumers, then signs
   the original validation evidence. Tag that commit and push the tag.
   **Prepare GitHub release** retrieves those immutable artifacts and stages
   the same bytes with signed validation and promotion provenance. It does not
   rebuild distributions or rerun the test matrix.
3. Review the draft and publish it with `just release-publish`. This starts
   **Publish to PyPI**; it does not upload the package immediately.
4. After asset verification succeeds, approve the `pypi` deployment in GitHub.
   The workflow uploads the same validated files without rebuilding.
5. Verify the exact PyPI version, GitHub Release, and release assets.

The workflow definitions are [release preparation](../.github/workflows/prepare-release.yml)
and [package publication](../.github/workflows/publish.yml). The shared release
recipes also serve consumers, but this guide describes this package's PyPI
workflow. Consumers retain their own registry, packaging, and domain checks.

## 1. Prepare the environment

Complete [contributor setup](../CONTRIBUTING.md#development-environment), starting
with the declared uv version. Setup installs user-level Just and synchronizes
the locked development environment; no environment activation is needed. That
guide also covers the native tools required by the tests.

Changelog generation requires the git-cliff version declared in the root
Justfile. With Cargo on PATH, install it if it is missing or out of date:

```sh
just changelog-setup
```

GitHub CLI (`gh`) must be installed and authenticated as a human maintainer with
permission to push tags and publish releases. `gh attestation verify --help`
must succeed: release verification requires a CLI with artifact-attestation
support and source/signer digest and ref policy flags. Complete the private account-setup
task for PyPI trusted publishing and GitHub's `pypi` environment approvals before
starting. See [release configuration](CONFIGURING_GITHUB.md#release-configuration).
Committing workflow files does not configure these accounts or approvals.

Land substantive changes before preparing the release. Start with a clean working
tree, check the remote and published stable releases, then synchronize `main`:

```sh
gh auth status
git --no-pager remote -v
git --no-pager status --short
gh release list --exclude-drafts --exclude-pre-releases --limit 10
```

Confirm `origin` is `acgetchell/research-repo-tools` and the status output is empty
before continuing. Preserve any unfinished work separately.

```sh
git switch main
git pull --ff-only
just sync
```

Set the release inputs once. This example prepares `v0.2.3` after `v0.2.2` for
October 21, 2026:

```sh
VERSION=0.2.3
TAG="v$VERSION"
PREVIOUS_TAG=v0.2.2
RELEASE_DATE=2026-10-21
```

Replace the example values with the target version, actual previous published
stable tag, and intended UTC tagging date. This repository uses the default
`today` date policy: the changelog date must equal the current UTC date when
previewing or creating the tag. Allow time for review and CI on that date; if
preparation crosses into another UTC day, follow the date recovery guidance below.

If dependencies or tools need upgrading, run `just update`, review and validate
the changes, and land them separately. Release preparation does not upgrade
dependencies. Run `just help` for the complete command list.

## 2. Prepare and merge the release PR

### Update metadata and generate notes

Create the release branch, then prepare metadata before generating the changelog:

```sh
git switch -c "release/$TAG"
just release-update "$TAG" "$PREVIOUS_TAG" "$RELEASE_DATE"
just changelog-preview --tag "$TAG" --date "$RELEASE_DATE"
just changelog-release "$TAG" "$RELEASE_DATE"
```

`release-update` synchronizes the package version in `pyproject.toml` and its local
entry in `uv.lock`, along with applicable existing release metadata. The explicit
previous tag avoids GitHub release discovery. See
[metadata preparation](UPDATING_RELEASE_METADATA.md) for the shared contract.

`changelog-preview` prints prospective notes without writing them.
`changelog-release` writes `CHANGELOG.md` and archives completed minor series
under `docs/archives/changelog/` when applicable. Neither command creates a tag.
Both receive the same explicit date as metadata preparation.

Notes come from committed Git history. Staged or unstaged implementation changes
will not appear. If a substantive fix is needed, commit it, regenerate the notes,
and repeat the affected checks before review. Generate changelog content rather
than editing it by hand; see [changelog maintenance](GENERATING_CHANGELOGS.md).

### Validate the prepared release

```sh
just changelog-check
just release-check "$TAG"
just ci
just audit
```

`changelog-check` validates the root changelog and its archives. `release-check`
requires a canonical stable `vX.Y.Z` tag matching the package version,
synchronized metadata, and dated, nonempty notes with valid references. It does
not create a tag, check hosted CI, or publish anything.

`just ci` runs local checks and tests, builds the wheel and sdist, and checks
isolated installations of both. It covers only the current host and excludes the
hosted native setup checks. `just audit` separately checks locked Python
dependencies against online advisories; release preparation repeats that audit.
See [validation coverage](VALIDATING.md) for details.

### Review and submit the PR

Inspect the prepared files before staging:

```sh
git --no-pager status --short
git --no-pager diff
```

Stage only the reviewed paths with `git add`, including any new changelog archive.
Then inspect the staged diff, commit, and open the release PR:

```sh
git --no-pager diff --cached
git commit -m "chore(release): release $TAG"
git push -u origin "release/$TAG"
gh pr create --base main --head "release/$TAG" --title "chore(release): release $TAG"
```

Include validation results and any skipped checks in the PR description. Resolve
review findings and require all configured branch checks before merging manually.
The publication gate in [pyproject.toml](../pyproject.toml) requires successful
`check (ubuntu-24.04)`, `check (macos-15)`, `check (windows-2025)`, and
`changelog-integration` and `release-evidence` results on the release commit.
The latter runs only on trusted main pushes, after PR validation and merge. Local validation alone
does not satisfy these requirements.

## 3. Tag the reviewed release commit

After the PR merges, restore the same release variables if using a new shell.
Start with a clean working tree, then synchronize the reviewed release:

```sh
git switch main
git pull --ff-only
just sync
git --no-pager status --short
git --no-pager log -1 --format=fuller
```

The status output must be empty. Compare the full commit SHA with the merged
release PR and the successful **Package checks** run in GitHub. Record the
selected SHA and the main-push run ID/attempt in the release PR or tracking issue.
Require successful `build`, all three native checks, `changelog-integration`,
and `release-evidence` in that same attempt. Stop if the commit differs or any
required result is pending, skipped, failed, or belongs to another attempt.

```sh
RELEASE_COMMIT=$(git --no-pager rev-parse HEAD)
gh run list --workflow ci.yml --event push --branch main --commit "$RELEASE_COMMIT" --limit 20
```

Inspect the matching run in GitHub before tagging. Preparation independently
requires exactly one successful matching main-push run from this repository's
active `.github/workflows/ci.yml`. A workflow dispatch, PR run, fork, unrelated
commit or ambiguous successful runs cannot substitute for it. Partial reruns
are ineligible: use **Re-run all jobs** on the original main-push run so the
producer and every validator run together in the new attempt.

If `main` has advanced since the release PR, review the intervening changes and
refresh generated notes through a PR as needed. Validate the final contents and
require successful native checks for the selected commit before tagging it.

### Preview and create the annotated tag

```sh
just release-check "$TAG"
just release-notes "$TAG"
just release-tag-preview "$TAG"
```

Confirm the version, UTC date, notes, and proposed annotation. The preview runs
tag validation without changing Git state. If it reports a stale date, return to
metadata preparation before proceeding.

```sh
just release-tag "$TAG"
git --no-pager show --no-patch "$TAG"
```

`release-tag` creates a local annotated tag at the current commit; it does not
push or publish. Confirm the displayed commit matches the recorded SHA and the
annotation matches the preview. Oversized annotations link to the complete notes.
If the tag already exists or differs from the reviewed commit, stop and inspect
local and remote state. Never move a published tag.

### Push the tag and wait for preparation

```sh
git push origin "$TAG"
gh run list --workflow prepare-release.yml --limit 5
gh run watch --exit-status
```

Select the **Prepare GitHub release** run for this tag and commit when prompted;
if it has not appeared yet, repeat the listing. Wait for the complete workflow,
including staging, to succeed. It requires an unchanged tag whose full commit
belongs to `main`, checks metadata and generated notes, and audits dependencies.
It selects that commit's trusted successful main run, checks all required job
and step identities, and downloads the distribution and evidence artifacts by
immutable ID. It checks original ZIP digests, flat inventory, wheel/sdist
metadata, package version, file hashes and signed source association. It signs
promotion only after verifying the original main-validation signature.

The preparation log records the original run/attempt, distribution artifact ID
and ZIP digest. Its default pending-validation wait is bounded to 30 polls at
20-second intervals (under ten minutes); missing, failed, expired, ambiguous or
mismatched evidence fails with recovery instructions. There is no rebuild or
fallback to another commit. Let this workflow create the draft and assets; a
manually assembled release cannot replace its signed validation provenance.

### Artifact retention and recovery window

Both `distributions-ATTEMPT` and `validated-evidence-ATTEMPT` are retained for
30 days. Complete preparation and publication within that window; the verifier
also rechecks the selected live run/attempt and immutable artifact metadata.
The original manifest and signature are preserved inside the release's
`release-attestation.json`, along with signed promotion evidence, so the origin
and promoted file hashes remain independently verifiable after Actions retention
expires. Expiration does not justify substituting a new local build.

If either artifact is missing or expired, first inspect retention and access.
With access restored, rerun **all** jobs of the original **push to main** run
for the exact tagged commit:

```sh
MAIN_RUN_ID=REPLACE_WITH_THE_RECORDED_MAIN_PUSH_RUN_ID
gh run rerun "$MAIN_RUN_ID"
gh run watch "$MAIN_RUN_ID" --exit-status
```

Do not use `--failed`, `--job`, or `gh workflow run`: those cannot establish a
complete main-push production/validation attempt. Wait for `release-evidence`
and inspect the new artifact IDs before retrying preparation. If the original
main-push run itself is unavailable, stop and investigate; the workflow cannot
manufacture equivalent evidence from an unrelated run. For a staging failure,
follow the draft recovery procedure below before rerunning the recorded
preparation run with `gh run rerun PREPARATION_RUN_ID`.

## 4. Review the draft and publish

Stay in the clean checkout of the tagged commit. Inspect the draft and compare
its notes with the generated notes:

```sh
gh release view "$TAG" --json tagName,isDraft,isPrerelease,body,assets
just release-notes "$TAG"
```

Confirm the expected tag, `isDraft: true`, and `isPrerelease: false`. The draft
must have exactly these three nonempty uploaded assets, substituting `$VERSION`:

- `research_repo_tools-$VERSION-py3-none-any.whl`
- `research_repo_tools-$VERSION.tar.gz`
- `release-attestation.json`

GitHub's automatically provided source archives are separate from this uploaded
asset inventory. `release-attestation.json` retains the original main-validation
manifest and Sigstore bundle plus a second signed promotion predicate. Review
the selected main run/attempt and immutable artifact IDs/digests, the successful
preparation run, and the generated notes before approving publication.

Using the authenticated human GitHub CLI session, run:

```sh
just release-publish "$TAG"
gh run list --workflow publish.yml --limit 5
```

**`release-publish` is approval to publish the GitHub Release.** It checks the
clean checkout, metadata, remote tag and commit, protected-branch ancestry,
required CI checks, draft state, and required assets before publishing. Use the
human session so the release event can trigger the publication workflow.

Open the matching **Publish to PyPI** run in GitHub. Its `verify` job checks the
exact uploaded inventory and provider digests, then verifies both signatures.
Main validation must be signed by `ci.yml` at the exact source/signer commit and
`refs/heads/main`, with the original push run/attempt in its certificate.
Promotion must be signed by `prepare-release.yml` at that same commit and the
release tag ref, binding the retained validation evidence and promoted bytes.
Both reject self-hosted runners. The verifier rechecks live validation results,
artifact association, the remote tag and release asset identity before allowing
upload. After it succeeds, approve the pending `pypi` environment deployment in GitHub to allow
the upload. Follow the configured reviewer policy, then wait for completion:

```sh
gh run watch --exit-status
```

Select the publication run for this tag and commit. The upload uses PyPI trusted
publishing and the verified wheel and sdist; there is no local upload step.

## 5. Verify and finish

After the publication workflow completes, verify both registry and GitHub state:

```sh
just release-verify "$TAG" --attempts 7 --interval 10
```

The command requires a published stable GitHub Release with the required assets
and the exact version on PyPI. The attempts and interval allow time for registry
visibility; they never repeat an upload. A published GitHub Release alone does
not establish that PyPI publication succeeded.

In a fresh consuming project, follow the [installation instructions](../README.md#install-with-uv)
with the exact published version, complete setup, and run that consumer's focused
integration checks. Consumers retain their Rust, platform, and domain checks.

Record the version, full release commit, original main run/attempt, immutable
distribution/evidence artifact IDs and ZIP digests, promoted file hashes,
successful preparation/publication runs and verification results in the release
PR or tracking issue. After verification,
remove the merged release branch if it still exists:

```sh
git branch -d "release/$TAG"
git push origin --delete "release/$TAG"
```

## Recover a failed attempt

- **Metadata, notes, or validation failed:** fix the source, regenerate notes
  after substantive commits, and repeat the affected checks before review.
- **The UTC date changed before tagging:** set `RELEASE_DATE` to the new intended
  UTC tagging date and rerun `release-update`, `changelog-preview`, and
  `changelog-release` from step 2. The updater synchronizes an existing target
  heading before generation, avoiding conflicting declared dates. Review, commit,
  validate, and merge the changes before returning to step 3. Do not hand-edit
  generated dates or bypass the tag check.
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
  expected-old-ref guard, recreate its annotation through `just release-tag`,
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
