# Releasing

Use this checklist after the maintainer has configured registry publishing and
deployment approval. Keep account setup in the maintainer's private checklist.
Install uv, run repository setup to install user-level Just, and use `just`
without activating an environment. Keep an exact published research-repo-tools
pin in the locked tooling group.

Publishing the GitHub Release starts the package-upload workflow. That workflow
checks the release and waits for deployment approval before uploading. Verification
then confirms that both the GitHub Release and registry version exist.

## Prepare

Set `TAG=vX.Y.Z`, `PREVIOUS_TAG` to the previous published stable release, and
`RELEASE_DATE` to the intended UTC date. Prepare on a release branch after
substantive commits have landed. Run these commands in order:

```sh
just release-update "$TAG" "$PREVIOUS_TAG" "$RELEASE_DATE"
just changelog-preview --tag "$TAG" --date "$RELEASE_DATE"
just changelog-release "$TAG" "$RELEASE_DATE"
just changelog-check
just release-check "$TAG"
just ci
```

Review and commit the changes, then submit and merge the release PR manually.
Require the configured native platform/package checks on the release commit.
Consumers retain their feature, MSRV, platform and scientific checks.

## Tag

After merge, use a clean checkout of the reviewed commit. Preview, create, and
push the tag manually:

```sh
just release-check "$TAG"
just release-tag-preview "$TAG"
just release-tag "$TAG"
git push origin "$TAG"
```

The annotation contains the release notes. Tag creation requires today's UTC
release date unless the repository declares a reviewed-date policy. Never move
published tags. Let the preparation workflow create the draft and required assets;
if no workflow creates a draft, create it in GitHub using the existing tag and notes.

## Publish

Review the completed draft, assets and successful preparation/validation run.
Use an authenticated human GitHub CLI session in the clean tagged checkout:

```sh
just release-notes "$TAG"
just release-publish "$TAG"
```

`release-publish` checks the draft, version, tag, reviewed commit, CI and required
assets, then publishes the GitHub Release. Approve the resulting deployment in
GitHub to allow the registry upload. Use a human GitHub CLI session for this step;
keep automated drafts unpublished until review.

## Verify

After the publication workflow completes, verify both registry and GitHub state:

```sh
just release-verify "$TAG" --attempts 7 --interval 10
```

Inspect a failed run and registry state before retrying. Cargo can report a timeout
after a successful upload. An existing version blocks another upload; verify the
original run instead. Registry lookup errors never prove absence. A content
correction requires a new version; do not follow a successful workflow with a
second manual upload.
