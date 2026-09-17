# Configuring GitHub

Source and issues live at
[acgetchell/research-repo-tools](https://github.com/acgetchell/research-repo-tools).
Consumers will install released packages from PyPI. Local wheels are for pilot
evaluation and installation checks; see [publishing](PUBLISHING.md).

## Repository controls

The baseline follows Delaunay and la-stack, adapted to this Python package:

- Pull requests, one approval, resolved review threads, and up-to-date required
  checks on the default branch; deletion and force pushes are blocked.
- The same administrator bypass as the reference repositories permits initial
  setup and maintainer recovery.
- Required checks cover all three package-check platforms, changelog integration,
  CodeQL for Python and Actions, zizmor, the Python dependency audit, and the
  CodeRabbit status. The initial CodeRabbit rule uses its status name, matching
  MCMC; GitHub rejects its App binding until the App has repository access.
- Read-only default workflow tokens; write permissions are limited to specific
  security-upload and Dependabot jobs, plus OIDC identity in the Codecov upload
  and PyPI publishing jobs. Actions cannot approve pull requests.
- Selected Actions only, with full commit SHA pinning required. Dependabot updates
  GitHub Actions and the uv lockfile weekly, with separate security-update groups.
- Dependabot alerts/security updates, secret scanning, push protection, and private
  vulnerability reporting are enabled through repository settings.

The checked-in payloads in `.github/settings/` are the desired API configuration.
The Actions policy deliberately requires SHA pins in addition to the reference
repositories' selected-action policy. Rust-only Clippy/Cargo scans do not apply
to this package. Codacy's project-specific Delaunay scan is not a required check
here; CodeQL, Ruff, ty, and zizmor cover the currently configured analysis.

## Apply or reconcile GitHub settings

Run from the checkout with an administrator-authenticated `gh` CLI. These
commands manage repository metadata, not local Git state:

```sh
gh api --method PUT repos/acgetchell/research-repo-tools/vulnerability-alerts
gh api --method PUT repos/acgetchell/research-repo-tools/automated-security-fixes
gh api --method PUT repos/acgetchell/research-repo-tools/private-vulnerability-reporting
gh api --method PATCH repos/acgetchell/research-repo-tools --input .github/settings/repository.json
gh api --method PUT repos/acgetchell/research-repo-tools/actions/permissions --input .github/settings/actions.json
gh api --method PUT repos/acgetchell/research-repo-tools/actions/permissions/selected-actions --input .github/settings/allowed-actions.json
gh api --method PUT repos/acgetchell/research-repo-tools/actions/permissions/workflow --input .github/settings/workflow-permissions.json
```

Find the existing `main` ruleset before changing it:

```sh
gh api repos/acgetchell/research-repo-tools/rulesets --jq '.[] | {id,name,enforcement}'
```

If it exists, replace `RULESET_ID` below and update it:

```sh
gh api --method PUT repos/acgetchell/research-repo-tools/rulesets/RULESET_ID --input .github/settings/main-ruleset.json
```

Only if it does not exist, create it:

```sh
gh api --method POST repos/acgetchell/research-repo-tools/rulesets --input .github/settings/main-ruleset.json
```

Required check names must remain synchronized with workflow job names. Verify
native check results on the first PR; local validation does not establish that
the hosted services are connected or passing.

## Codecov

The Linux package check collects Python branch and subprocess coverage with
`just coverage`; macOS and Windows use `just test`. CI calls the reusable
`.github/workflows/codecov.yml` upload workflow, which has no independent push or
pull-request trigger and does not rerun tests. It uses the SHA-pinned Codecov
action and GitHub OIDC, so no `CODECOV_TOKEN` secret is needed. Forks use the action's public
tokenless support; Dependabot uses a prefixed unprotected branch for tokenless
uploads because its GitHub token cannot request OIDC credentials.

Enable `research-repo-tools` in the existing
[Codecov account](https://app.codecov.io/gh/acgetchell/research-repo-tools), and
ensure the Codecov GitHub App has access to this repository for PR checks. The
selected-Actions allowlist includes `codecov/codecov-action@*`; full SHA pinning
remains required. Apply the allowlist command above when reconciling settings.

The checked-in `.codecov.yml` starts with advisory project and patch statuses and
disables bot comments. Coverage is visible without becoming a new merge gate
before the first `main` baseline exists. Revisit thresholds after reviewing that
baseline. The upload job reports upload failures, while coverage thresholds are
not added to the branch ruleset. Reports remain available as Actions artifacts
even when the service cannot accept an upload.

## CodeRabbit and Dependabot

### App access and review credentials

Grant the existing CodeRabbit GitHub App installation access to this repository
through [installed GitHub Apps](https://github.com/settings/installations).
The repository's `.coderabbit.yaml` enables reviews, approval on resolved findings,
and the legacy `CodeRabbit` status used by the branch rule.
After the App is connected, add `"integration_id": 347564` to its entry in
`.github/settings/main-ruleset.json` and update the existing ruleset to bind that
status to CodeRabbit's identity, as Delaunay does.

Configure `CODERABBIT_REVIEW_TOKEN` as a **Dependabot secret**, using an acgetchell
token that can read the repository/PR metadata and create PR conversation comments.
For a fine-grained token, grant this repository Metadata read, Issues write, and
Pull requests read. The CLI prompts for the value without putting it in shell
history:

```sh
gh secret set CODERABBIT_REVIEW_TOKEN --app dependabot --repo acgetchell/research-repo-tools
```

An existing token may need its selected-repository access updated. GitHub does not
expose stored secret values, so a secret in another repository cannot be copied
out through the API.

The automation handles same-repository Dependabot PRs, requests one CodeRabbit
review per head, waits for that exact head's approval and required checks, and
enables squash auto-merge with a head-commit guard. It never checks out PR code.
Missing credentials, review, or required checks prevent completion.

## Release configuration

The repository is pushed and normal changes go through pull requests. The
maintainer performs Git mutations under [AGENTS.md](../AGENTS.md).

The desired Actions allowlist includes `pypa/gh-action-pypi-publish@*` for the
tagged-release workflow; apply that payload before the first release tag. The
`pypi` environment protections and PyPI Trusted Publisher are separate account
settings. Follow [Publishing to PyPI](PUBLISHING.md) to configure them and verify
the first release. Committing workflow YAML does not configure those accounts
or publish distributions.

## Follow-up work

- [First PyPI release and Trusted Publishing](https://github.com/acgetchell/research-repo-tools/issues/1).
- [External Rust and Python tool installation](https://github.com/acgetchell/research-repo-tools/issues/2).
- [Shared Jupyter notebook infrastructure](https://github.com/acgetchell/research-repo-tools/issues/3).
- [MCMC changelog pilot](https://github.com/acgetchell/markov-chain-monte-carlo/issues/157),
  after its current work is complete; durable adoption requires the first PyPI release.
