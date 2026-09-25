# Automating Dependabot approvals

The reusable `.github/workflows/dependabot-approve.yml` workflow provides one
approval implementation for consumers. Each repository supplies a small caller,
an explicit dependency/file allowlist, and its GitHub settings. This workflow is
versioned by Git commit, independently of the Python package and PyPI releases.

## Approval contract

The workflow runs only for `pull_request_target` events on an exact caller
repository, for a same-repository Dependabot PR targeting its default branch.
The trusted base branch owns both the caller policy and workflow definition. No
step checks out PR code, installs PR dependencies, runs PR scripts, or receives
personal access tokens. It uses only the repository's `GITHUB_TOKEN` and a
SHA-pinned `dependabot/fetch-metadata` action.

Automatic approval requires all of the following:

- The PR is open and ready for review, at the event's exact head commit.
- There is exactly one commit, authored by `dependabot[bot]`, committed by GitHub's
  `web-flow` identity, with a verified signature. The committer check rejects
  personal signatures on commits claiming Dependabot authorship. Additional
  commits require manual review because the metadata action
  reads only the first commit. A normal Dependabot rebase can produce a new
  eligible single-commit head; a maintainer merge commit is not eligible.
- Every dependency named in the update metadata is allowlisted for the same
  `uv` or `cargo` ecosystem, and every update is a patch upgrade. Old and new
  versions must have three numeric release components, retain major/minor, and
  increase patch. Missing metadata, prereleases, downgrades, minor/major updates,
  and other ecosystems require manual review, including GitHub Actions updates.
- Every changed file is an existing, modified, explicitly allowlisted dependency
  file. Added, removed, renamed, or unlisted files are ineligible. The complete
  paginated file list must match GitHub's reported changed-file count.

The dependency-name allowlist covers the dependencies Dependabot identifies as
update targets. It does not assert that every transitive lockfile change is a
patch update; the full resolution still receives the repository's tests and
vulnerability scans. Grouped PRs are approved only when every metadata entry
passes. Existing mixed update groups may therefore require manual review.

The approval records the exact commit ID. The workflow checks that head again
before posting, avoids duplicate current bot approvals, and enables native
squash auto-merge with `--match-head-commit`. GitHub enforces required approvals,
current checks, resolved threads, and stale-approval dismissal. No administrator
merge or bypass is requested. API failures stop processing. Ineligible updates
receive neither approval nor an auto-merge request from this workflow.

## Repository setup

Use active rulesets requiring at least one approval, dismissal of stale reviews,
resolved review threads, and strict up-to-date status checks. The workflow queries
the effective rules and refuses approval if these protections are absent; legacy
branch protection alone is not supported by this pilot. The shared-tools settings
payload enables stale-approval dismissal without removing any required check.

Enable **Settings → General → Pull Requests → Allow auto-merge** and
**Allow squash merging** in each consumer repository. These are separate from
the Actions approval permission. The pilot's `.github/settings/repository.json`
enables both merge settings.

Enable **Settings →
Actions → General → Workflow permissions → Allow GitHub Actions to create and
approve pull requests**, while retaining **Read repository contents and packages
permissions** as the default. Only the caller and approval job request
`contents: write` and `pull-requests: write`.

For repositories that allow selected actions, add `dependabot/fetch-metadata@*`
and the shared workflow path
`acgetchell/research-repo-tools/.github/workflows/dependabot-approve.yml@*` to the
allowlist. Retain full SHA pinning and all existing entries. The shared-tools
repository's own local call needs only the metadata action entry; its desired
settings payloads are in `.github/settings/`.

Create a caller similar to the following. Replace `REVIEWED_COMMIT_SHA` with the
full commit that contains the reviewed workflow; it is intentionally not a
floating branch or an assumed PyPI release tag. Replace the repository and
allowlists with the consumer's policy.

```yaml
name: Dependabot approval and auto-merge
on:
  pull_request_target:
    types: [opened, reopened, ready_for_review, synchronize]
    branches: [main]
permissions: {}
concurrency:
  group: dependabot-auto-merge-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  approve-and-enable-auto-merge:
    permissions:
      contents: write
      pull-requests: write
    uses: acgetchell/research-repo-tools/.github/workflows/dependabot-approve.yml@REVIEWED_COMMIT_SHA
    with:
      repository: owner/repository
      policy: >-
        {"uv": {
          "dependencies": ["pytest", "ruff"],
          "files": ["pyproject.toml", "uv.lock"]
        }, "cargo": {
          "dependencies": ["serde", "serde_json"],
          "files": ["Cargo.toml", "Cargo.lock"]
        }}
```

Use exact dependency names from Dependabot metadata and exact repository-relative
file paths. If the default branch differs, change `branches` in the caller. Do
not forward secrets or expose inputs through PR titles, comments, or labels.
Review policy changes as ordinary code changes. Dependabot can maintain the
external workflow's SHA pin through its GitHub Actions ecosystem updater.

The shared-tools pilot uses a local reusable-workflow reference so the caller and
implementation are tested at the same commit. Keep the approval workflow job
optional in branch rules: it intentionally skips ordinary PRs. The existing
CodeRabbit status may remain required, but it does not supply this workflow's
approval and a failure still blocks merging.

## Pilot and rollout

Merge the workflow and caller into the default branch before expecting
`pull_request_target` to use them. Apply the repository and two Actions settings
payloads and update the existing ruleset using
the commands in [Configuring GitHub](CONFIGURING_GITHUB.md). A future eligible
Dependabot PR, reopen, or synchronization event can then exercise the workflow.
An old run uses its original workflow revision; merely rerunning it is not a
substitute for a new event after deployment.

`research-repo-tools` checks GitHub Actions dependencies on Saturdays at 03:00
and uv dependencies at 05:00, in `America/Los_Angeles`. The uv run is the first
potential approval test under this policy. Cooldowns, absent patch releases,
mixed groups, or an updater/runtime mismatch may mean no eligible PR appears.
Do not relax the policy solely to manufacture a passing pilot.

Verify an actual `github-actions[bot]` approval on the current head, native
merge only after required checks pass, and continued blocking for ineligible
updates. Then pin that tested workflow commit in other repositories. Their
Actions settings and allowlists must be applied separately; installing a newer
Python package does not configure GitHub.

A merge performed with `GITHUB_TOKEN` may not trigger downstream push workflows.
Manually dispatch `ci.yml` using the default-branch name (for example,
`gh workflow run ci.yml --ref main`), then verify that the run's `headSha` equals
the merged commit before accepting its results. GitHub accepts a branch or tag
for dispatch, not a raw commit SHA. If the branch has advanced, that run checks
the newer branch state; it does not establish validation of the earlier merge.
If automatic post-merge workflows become a requirement, plan a separately
scoped GitHub App credential; this approval workflow does not introduce one.
