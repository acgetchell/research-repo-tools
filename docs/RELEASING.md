# Releasing research-repo-tools

## Contents

1. [Set the release tag](#step-1-set-the-release-tag)
2. [Prepare the release PR](#step-2-prepare-the-release-pr)
   - [Update metadata and generate notes](#1-prepare-the-release-branch-and-metadata)
   - [Run the release checks](#2-run-the-release-checks)
   - [Review and open the PR](#3-review-and-open-the-pr)
3. [Publish after the PR is merged](#step-3-publish-after-the-pr-is-merged)
   - [Wait for main CI](#1-synchronize-main-and-wait-for-ci)
   - [Create and push the annotated tag](#2-create-and-push-the-annotated-tag)
   - [Save evidence and capture the draft](#3-save-the-signed-evidence-and-capture-the-draft)
   - [Review the draft](#4-review-the-draft)
   - [Publish the GitHub Release](#5-publish-the-github-release)
   - [Open the run and approve publishing to PyPI](#6-open-the-run-and-approve-publishing-to-pypi)
   - [Verify the published package](#7-verify-the-published-package)

**Resuming a release?** [Choose a recovery path](RECOVERING_RELEASES.md#choose-the-recovery-path).
See also [reusable CI evidence](#reusable-ci-evidence).

## Step 1: Set the release tag

Replace `X.Y.Z` with the version you intend to release and run this in Bash or zsh.

```bash
TAG=vX.Y.Z
```

Use this shell variable throughout the release. The annotated Git tag is created
after the PR merges. Start from a clean, up-to-date `main` with the
[development environment](../CONTRIBUTING.md#development-environment) and a working
`gh auth status`. Use `just changelog-setup` if the pinned generator is missing.
Land dependency and tool upgrades separately.

## Step 2: Prepare the release PR

### 1. Prepare the release branch and metadata

```bash
git switch -c "release/$TAG"
just update-version "$TAG"
just changelog-release "$TAG"
```

Review the version in `pyproject.toml`, `uv.lock` and `CITATION.cff`. Check the
top changelog entry and any rotated archives. Generation reuses the prepared
citation date, including on a later day. Review the README quickstart and
[upgrade examples](UPDATING_DEPENDENCIES.md#shared-python-adoption) against the new version.
Keep historical and minimum-version examples where they still apply.

### 2. Run the release checks

```bash
just release-dependency-builds
just release-dependencies
just release-version-check
```

These check the source-built dependency receipts, published upstreams, package
version and dated notes. Stage only the reviewed release files. Use the commit
subject below so later changelog generation skips the preparation commit.

```bash
git --no-pager diff --cached
git commit -m "chore(release): prepare $TAG"
```

### 3. Review and open the PR

Run the push gates on the final release commit. `just ci` includes strict
distribution metadata and isolated wheel and sdist installation checks.

```bash
just security
just ci
just review origin/main
```

After addressing findings, follow [preparation corrections](RECOVERING_RELEASES.md#preparation-corrections)
and repeat the gates. Once they pass, push the branch and open the PR.

```bash
git push -u origin "release/$TAG" &&
  gh pr create --base main --head "release/$TAG" --title "chore(release): release $TAG"
```

Include the gate results in the PR description. Wait for required checks and
reviews before merging.

## Step 3: Publish after the PR is merged

### 1. Synchronize main and wait for CI

From a clean checkout, update your local `main` after the release PR merges.

```bash
git switch main && git pull --ff-only origin main
```

In **Actions → Package checks**, open the push to `main` for the release PR you
just merged. Wait for **Success**, including Linux, macOS, Windows and the
`release-evidence` job. When the commit shown on the run matches your local
checkout, continue to tagging. If your pull brought in a later commit,
[check out the release commit](RECOVERING_RELEASES.md#check-out-the-release-commit).
If the run is missing or ambiguous, [inspect the original runs](RECOVERING_RELEASES.md#inspect-the-original-runs).

### 2. Create and push the annotated tag

```bash
just tag "$TAG"
```

Confirm the printed SHA and annotation, then push just that tag to start **Prepare GitHub release**.

```bash
git push origin "refs/tags/$TAG"
```

### 3. Save the signed evidence and capture the draft

**Save the evidence after pushing the tag.** From the clean tagged checkout,
run this as soon as the tag is on GitHub. It reads the signed distributions
from the successful **Package checks** run on `main`, even while draft preparation is running.

```bash
just release-evidence
```

Keep the printed summary in `../release-handoffs/research-repo-tools/<tag>/`.
It records the commit, main run and attempt, artifact IDs and digests, and
SHA-256 of each file. If there is one open issue titled
`release: validate and publish research-repo-tools <tag>`, the recipe also posts
the summary there. Use `just release-evidence ISSUE_NUMBER` for another tracker.

**Confirm preparation in GitHub.** Open
[Actions → Prepare GitHub release](https://github.com/acgetchell/research-repo-tools/actions/workflows/prepare-release.yml)
and select the run triggered by your tag push. Once that run shows **Success**,
capture the prepared draft from the same checkout.

```bash
just release-draft "$TAG"
```

The recipe rechecks the original tag-push run, verifies the draft ID, tag, title
and commit, and prints the retained `target.json` path. Keep it with the
evidence. Publish and verify both use this target.

### 4. Review the draft

Open the draft for your tag in [GitHub Releases](https://github.com/acgetchell/research-repo-tools/releases).
Read the title and notes as they will appear to users. Under **Assets**, look for the
wheel (`.whl`), source distribution (`.tar.gz`) and `release-attestation.json`.
If the draft looks right, continue to step 5. [Command-line draft checks](RECOVERING_RELEASES.md#inspect-a-draft-from-the-command-line)

### 5. Publish the GitHub Release

From the clean tagged checkout and a human GitHub CLI session, publish the reviewed draft.

```bash
just release-publish "$TAG"
```

The initial check verifies the release metadata, required CI checks and captured
draft. The final check repeats those checks immediately before publishing the
GitHub Release. Its publication triggers the separate PyPI workflow.

### 6. Open the run and approve publishing to PyPI

1. In [Actions → Publish to PyPI](https://github.com/acgetchell/research-repo-tools/actions/workflows/publish.yml),
   open the run for the release you just published. Wait for `verify` to pass and `publish` to request approval.
2. Click **Review deployments**, select **pypi**, then click **Approve and deploy**.
3. Wait for `publish` to succeed before checking the files on PyPI.

### 7. Verify the published package

```bash
just release-verify "$TAG"
```

Confirm the exact wheel and sdist downloaded from PyPI match the signed
filenames and hashes, then install the exact version in a fresh consumer and run
its focused checks. [Commands for verifying PyPI files and a fresh install](RECOVERING_RELEASES.md#verify-pypi-files-and-a-fresh-install)

Record the publication run, attempt and consumer results with the handoff.
If Zenodo is enabled, check the version record's date, authors, ORCID and license
against `CITATION.cff` and record its DOI. Then remove the merged release branch locally and on GitHub.

## Reusable CI evidence

The complete main-push attempt signs the distribution and native validation
records. Tag preparation signs promotion of the same files. PyPI verification
checks both signatures and the live artifact identities before approval.
[Evidence contracts](VERIFYING_RELEASE_EVIDENCE.md)
