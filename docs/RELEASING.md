# Releasing research-repo-tools

PyPI is the distribution source for consumers. GitHub hosts source, issues,
review, and CI. Consumers pin the published package in their development
dependencies and lock it with uv; sibling checkouts and local wheels are for
development only. A public GitHub repository is not required by PyPI.

Use the repeat-release procedure below for each new version. Account and Trusted
Publisher setup is a one-time prerequisite. Release history belongs in the
generated [CHANGELOG.md](../CHANGELOG.md).

## Release workflow

The same built wheel and sdist move through validation, a draft GitHub Release,
and PyPI. Neither draft creation nor PyPI publication rebuilds them.

1. Push a canonical stable `vX.Y.Z` tag after the release PR is merged.
   [prepare-release.yml](../.github/workflows/prepare-release.yml) checks that its
   commit belongs to protected `main`, validates the version and generated notes,
   and audits locked dependencies.
2. Preparation calls the reusable package checks at the same source commit. They
   build once and validate the same wheel and sdist on Linux, macOS, and Windows,
   including isolated installations and native setup. The git-cliff integration
   job must also pass.
3. Only after those checks pass, preparation signs the distributions with
   [GitHub artifact attestations](https://github.com/actions/attest), creates a
   **draft** GitHub Release from the tag annotation, and attaches the wheel,
   sdist, and `release-attestation.json` verification bundle.
4. Review the completed draft and publish it. This starts
   [publish.yml](../.github/workflows/publish.yml) on `release: published`.
   Draft and prerelease events cannot upload to PyPI.
5. Publication downloads that release's assets by ID and verifies their signed
   provenance with GitHub CLI: the owning repository, preparation workflow,
   source tag, source commit, signer commit, and GitHub-hosted runner identity
   must match. Missing, extra, substituted, or unverifiable assets fail closed.
   The tag must still identify the event commit, which must belong to `main`.
6. The verified distributions are captured in an immutable Actions artifact.
   After approval of the protected `pypi` environment, the upload job downloads
   that artifact by ID and uploads it using Trusted Publishing and attestations.

The preparation workflow has no `pypi` environment. Only its draft-staging job
has release-write and signing permissions. Validation jobs retain their existing
permissions; their Codecov OIDC job is skipped for release tags. The publication
verification job has read-only repository access. The final PyPI job receives
only `id-token: write`, has no source checkout or package installation step, and
never executes the downloaded package. The PyPI publisher remains `publish.yml`
with environment `pypi`; no new API token or publisher registration is needed
when those settings already exist.

The draft's durable assets include the verification bundle, so publication does
not depend on retaining the preparation run's temporary Actions artifact. The
verified snapshot used by the approval-gated upload expires after seven days;
approve within that window or rerun publication verification to create a new
snapshot. Required PR check names remain unchanged.

## One-time publishing setup

Complete these account settings after the preparation PR passes CodeRabbit and
required CI and is merged. Repository files describe the intended configuration;
they do not apply it automatically.

1. Reconcile the selected-Actions policy using
   [GitHub setup](CONFIGURING_GITHUB.md#apply-or-reconcile-github-settings). The desired
   `allowed-actions.json` adds `pypa/gh-action-pypi-publish@*`; full SHA pinning
   remains required. The workflow uses GitHub's exact-commit
   [self-repository syntax](https://github.blog/changelog/2026-07-30-reference-same-repository-actions-with-self-repository-syntax/)
   for its reusable CI call. The pinned actionlint version needs the narrowly
   documented exception in `.github/actionlint.yaml` until it supports that syntax.
2. In GitHub **Settings → Environments**, create `pypi`. Require approval by
   `acgetchell`, disable administrator bypass of environment protection, and
   select deployment tags matching `v*`, with no branch deployment rule.
   For a sole maintainer, allow self-review so the person pushing the tag can
   approve the deployment. Adding a second reviewer permits disabling self-review.
   Creating the name in YAML alone does not configure these protections.
3. Sign in to the intended PyPI owner account, verify its email and two-factor
   authentication, and check availability/ownership of `research-repo-tools`.
   If it does not exist, use **Publishing → Add a new pending publisher** with:

   | Field | Value |
   | --- | --- |
   | PyPI project | `research-repo-tools` |
   | GitHub owner | `acgetchell` |
   | Repository | `research-repo-tools` |
   | Workflow filename | `publish.yml` |
   | Environment | `pypi` |

   If the project already belongs to this account, add the same publisher in its
   Publishing settings. If someone else owns the name, resolve that before tagging.
   A [pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
   creates the project on the first successful upload; it does not reserve the name.

## Prepare the environment

Initialize the development environment with uv as described in
[Contributing](../CONTRIBUTING.md#development-environment); this installs the
user-level Just command. Routine commands use `just` without activation.
GitHub CLI must be authenticated, and git-cliff must be available for generation.
The Git mutation commands below are for the maintainer to run manually, as
required by [AGENTS.md](../AGENTS.md).

Set the release inputs once. This example prepares `0.1.1` after `v0.1.0`:

```sh
VERSION=0.1.1
TAG="v$VERSION"
PREVIOUS_TAG=v0.1.0
RELEASE_DATE=YYYY-MM-DD
```

Replace the example values with the target version, actual previous published
tag, and intended UTC release date. Explicit inputs keep preparation reproducible
and avoid requiring a GitHub release lookup during metadata updates.

Verify authentication and remotes, synchronize `main`, and sync the environment:

```sh
gh auth status
git --no-pager remote -v
git switch main
git pull --ff-only
just sync
```

If dependency or tool upgrades are needed for this release, run `just update`
and review, validate, and land those changes separately before preparing the
release branch. Release metadata updates do not upgrade dependencies. Major code
changes should also already be committed and merged into `main`.

## Prepare the release PR

### Create the release branch

```sh
git switch -c "release/$TAG"
```

Keep this PR focused on release metadata, generated notes, and release
documentation. Runtime and CLI versions come from installed distribution
metadata, whose source is `project.version` in `pyproject.toml`; there is no
separate version constant.

### Update metadata and generate the changelog

After substantive changes are committed, run these commands in order:

```sh
just release-update "$VERSION" "$PREVIOUS_TAG" "$RELEASE_DATE"
just changelog-preview --tag "$TAG" --date "$RELEASE_DATE"
just changelog-release "$TAG" "$RELEASE_DATE"
```

`just release-update` wraps the shared `research-repo-tools release update`
command. It synchronizes `pyproject.toml`, the local package entry in `uv.lock`,
and applicable citation metadata before generating notes. It does not generate
notes, upgrade dependencies, tag, or publish. See
[Shared release behavior](UPDATING_RELEASE_METADATA.md) for its contract.
For a consumer's initial release, use the explicit first-release mode described
in [Shared release behavior](UPDATING_RELEASE_METADATA.md#first-release-preparation).
This package already has published history and must use a real predecessor.

Changelog generation uses committed history as though the target tag already
existed; no temporary tag is needed. It keeps the active minor series in
`CHANGELOG.md` and archives completed series under `docs/archives/changelog/`.

Review and commit the changed metadata and generated changelog; never hand-write
changelog entries. An earlier draft cannot include later or uncommitted changes.
Refresh notes after substantive implementation commits. If the intended release
date changes before publication, rerun `just release-update` with the same target
and previous tag but the new date, then regenerate and validate the notes.
Generation preserves declared dates, so changing only `changelog-release`'s date
would conflict with the existing declaration.

### Validate the release artifacts

```sh
just changelog-check
just release-check "$TAG"
just ci
just audit
```

`changelog-check` strictly validates the root and archives. `release-check`
requires the target version and dated, nonempty generated notes to match the
package metadata. `just ci` builds local artifacts and checks isolated wheel
and sdist installations; `just check-dist` validates existing artifacts without
rebuilding. `just audit` checks locked dependencies against advisory data.

### Review, commit, and submit the release PR

Inspect the working tree before staging:

```sh
git --no-pager status --short
git --no-pager diff
```

Expected changes include `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, and any
generated archives or applicable citation metadata. Stage only reviewed paths;
do not include unrelated changes or local `dist/` build artifacts. Then inspect
the staged diff and create the release commit:

```sh
git --no-pager diff --cached
git commit -m "chore(release): release $TAG"
git push -u origin "release/$TAG"
gh pr create --base main --head "release/$TAG" --title "chore(release): release $TAG"
```

Include validation results in the PR description. Resolve CodeRabbit findings
and require passing native CI before merging.

If a substantive fix is needed during preparation, commit it, regenerate the
changelog, and rerun the release checks before merge. If the UTC publication day
changes, update `RELEASE_DATE`, rerun the metadata and changelog steps, and
revalidate. Do not edit generated notes by hand or move an already published tag.

## Publish after review and account setup

Use a clean checkout of the reviewed `main` commit with final generated notes:

```sh
git switch main
git pull --ff-only
git --no-pager status --short
just sync
```

Keep the release variables from preparation, or restore the same values in a
new shell. Preview and inspect the annotation, then create and push the tag:

```sh
just release-check "$TAG"
just tag-preview "$TAG"
just tag-release "$TAG"
git --no-pager tag -l --format='%(contents)' "$TAG"
git push origin "$TAG"
```

The shared tag command requires the heading's date to equal today's UTC date.
If it differs, update the declared date with `just release-update`, regenerate,
and review the updated metadata and notes through a PR before tagging.

### Review and publish the validated GitHub Release

Pushing the tag starts **Prepare GitHub release**, not the PyPI upload. Wait for
all preparation jobs to succeed, then inspect the draft:

```sh
gh release view "$TAG" --json tagName,isDraft,isPrerelease,body,assets
```

Expect a draft, a stable tag, the reviewed release notes, and exactly these assets
(with the chosen version in the filenames):

- `research_repo_tools-X.Y.Z-py3-none-any.whl`
- `research_repo_tools-X.Y.Z.tar.gz`
- `release-attestation.json`

GitHub's automatically generated source ZIP/tarball is separate from these
uploaded assets and is not used for PyPI. Do not upload a locally rebuilt wheel
or sdist over the validated draft assets.

After reviewing the draft and successful preparation run, publish it as yourself
through the GitHub UI or authenticated GitHub CLI:

```sh
gh release edit "$TAG" --draft=false
gh release view "$TAG"
```

This human publication triggers **Publish to PyPI**. Do not automate publication
with the preparation job's `GITHUB_TOKEN`: events created by that token do not
normally start another workflow. The workflow-created release must remain a
draft until reviewed.

### Approve and verify the PyPI upload

Inspect the **Publish to PyPI** run and its successful asset-verification job,
then approve the `pypi` environment deployment. Wait for the upload job to succeed
before checking PyPI. A published GitHub Release alone does not establish that
the PyPI upload succeeded. No local upload command is needed.

### Recover a failed attempt

Preparation never overwrites assets or edits a published release. A rerun may
resume an existing empty draft. If draft upload partially succeeded, inspect the
incomplete draft, remove it while it is still unpublished, and rerun preparation.
If a fresh build is needed, rerun the complete preparation workflow so the new
bytes receive the same validation. Never publish a partial draft.

If PyPI verification or upload fails, diagnose the failure and rerun that
publication workflow; do not delete/recreate the published GitHub Release or
move its tag. Downloaded bytes must still pass the original signed provenance.
If either distribution reached PyPI, inspect the files and run logs before
retrying: uploaded filenames cannot be replaced, and the workflow deliberately
does not skip existing files. A content correction requires a new version.
See [PyPI's Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
for publisher-identity failures.

## Verify the published package

After PyPI lists both distributions, run in a new disposable directory outside
this checkout, substituting the version just published:

```sh
uv init --bare --python 3.14
uv add --dev --index-url https://pypi.org/simple "research-repo-tools==0.1.1"
uv run --locked research-repo-tools --version
uv run --locked research-repo-tools --help
uv run --locked research-repo-tools templates cliff.toml --owner example --repository consumer
uv run --locked python - <<'PY'
from research_repo_tools import __version__
from research_repo_tools.cli import main

print(__version__)
raise SystemExit(main(["templates", "CHANGELOG.md"]))
PY
```

Confirm `uv.lock` resolves the package from PyPI and the commands work without a
source checkout, local wheel, or editable/path source. Also inspect the release's
file hashes and attestations on PyPI. In that disposable fixture, follow the
[toolchain guide](INSTALLING.md) to configure the tooling group and run setup from the
installed package. Record the tag/run, published version, and clean-install/setup
results in the release PR or release-tracking issue.

## Finish the release

Confirm the published GitHub Release's tag and notes match the verified PyPI
package. The GitHub Release also supplies history for future metadata updates
that omit an explicit previous tag. Record the preparation run, publication run,
version, and clean-install results in the release PR or release-tracking issue,
then close the completed release milestone.

After successful verification, remove the merged release branch if it still
exists locally or remotely:

```sh
git branch -d "release/$TAG"
git push origin --delete "release/$TAG"
```

## Adopt after publication

Publication is required before consumer adoption. Track each migration in its
consumer repository; these migrations are not release acceptance criteria.

In an existing consumer, use `uv add --group tooling "research-repo-tools==0.1.1"`, include
that group from `dev`, remove
any temporary local source override, and commit its manifest and lockfile after
validation. Thin just recipes or Python wrappers call the [supported API](api.md).
For upgrades, choose the next published version explicitly with the same command,
review its changelog, and run the consumer's own checks before merging.
