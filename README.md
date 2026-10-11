# research-repo-tools

[![PyPI version][pypi-badge]][pypi]
[![License][license-badge]][license]
[![CI][ci-badge]][ci-workflow]
[![CodeQL][codeql-badge]][codeql-workflow]
[![zizmor][zizmor-badge]][zizmor-workflow]
[![Codecov][codecov-badge]][codecov-dashboard]
[![Audit dependencies][audit-badge]][audit-workflow]

Shared development and maintenance tooling for Rust research repositories using
Python scripting and Jupyter notebooks. Each
capability has one implementation and one set of contracts, with tests grouped
by capability. Shared toolchain setup supports changelog handling and the
maintenance core; further workflows require a clear shared contract.

This package consolidates shared utilities from `la-stack`, `delaunay`,
`markov-chain-monte-carlo`, and `causal-triangulations`.
Its goal is to simplify that stack around a correct, performant, orthogonal,
and simple toolchain, keeping consuming repositories focused on Rust.

**uv is required.** Install it using [Astral's instructions](https://docs.astral.sh/uv/getting-started/installation/)
and make it available on PATH. This package uses uv to obtain Python 3.14+ and
Python tooling. Git and platform build prerequisites are listed in the
[toolchain guide][toolchain]. See [release status and publishing][publishing]
and the [supported CLI and Python interfaces][api].

## Install with uv

Users install pinned releases directly from PyPI in their own projects.
**Consumers never need to clone research-repo-tools.** Cloning this repository is
for developing or fixing the package.

Once the desired version is published, the consuming project's maintainer adds
it to a `tooling` dependency group, replacing `X.Y.Z` with that version:

```sh
uv add --group tooling --no-sync "research-repo-tools==X.Y.Z"
```

Include `tooling` in `dev`, retaining the project's other development dependencies:

```toml
[dependency-groups]
tooling = ["research-repo-tools==X.Y.Z"]
dev = [{include-group = "tooling"}]
```

Follow the [toolchain guide][toolchain] to declare uv, Python, and Rust tool
versions, merge the [consumer justfile template][just-template], and commit the
manifest and refreshed lockfile. The `uv add --no-sync` command records the
dependency without installing or synchronizing it. The setup invocation below
installs research-repo-tools before running setup, with no installation hooks
or generated scripts.

Just recipes require `sh` on PATH. On Windows, expose Git for Windows' `bin`
directory as described in the [toolchain guide][toolchain]; setup checks this
prerequisite before installing tools.

In a configured consumer checkout, run this once on Linux, macOS, or Windows:

```sh
uv run --locked --managed-python --only-group tooling research-repo-tools setup
```

Setup installs the declared tools, installs a pinned user-level Just through uv,
configures PATH, and synchronizes the locked Python environment with managed
Cargo available for native builds. Open a new terminal if PATH changed.
From then on, use `just help` and `just <recipe>` without environment activation.
Recipes select the locked project environment internally. Package contributors
use the setup command described in [CONTRIBUTING.md][contributing].

## Common workflows

| Capability | Commands | Contract |
| --- | --- | --- |
| Actions | `actions allowlist`, `update` | [Strict consumer allowlists and opt-in commit pin updates](#actions-allowlists-and-opt-in-pin-updates) |
| Changelog | `changelog archive`, `check`, `generate`, `normalize`, `notes`, `tag` | Root `CHANGELOG.md`; completed minor series in `docs/archives/changelog/` |
| CI environment | `ci export` | Validate all single-line values before appending a GitHub environment command file |
| Coverage | `coverage report` | Cobertura summaries with deduplicated source lines |
| Dependencies | `deps check-uv`, `update-python`, `update-tools`, `update-uv` | Exact development pins; canonical Cargo SemVer; stable uv pins |
| Documentation | `docs check-lines` | Existing explicit-path Markdown policy: 160 characters with table exemptions |
| File selection | `files check-lines`, `list`, `run` | Configurable raw line gate, tracked/nonignored inputs, exclusions and portable argument batching |
| Notebooks | `notebooks advise`, `check`, `clear`, `execute`, `group`, `inspect`, `launch`, `lint`, `reset`, `sync` | Read-only review, locked JupyterLab, native Ruff/ty checks, execution reports, and explicit restoration |
| Papers | `papers check`, `normalize`, `source-date` | Explicit UTC dates, optional PDF text/geometry checks and validated metadata normalization |
| Performance | `performance assets`, `baseline`, `compare`, `convert`, `export`, `extract`, `fetch`, `host`, `measure`, `profile`, `promote`, `publish`, `release-draft`, `release-upload`, `render`, `verify` | Complete configured measurement, retained evidence, release assets and publication |
| Python | `python check`, `fix`, `typecheck` | Complete tracked/nonignored inventory; native Ruff/ty policy; explicit fixes only |
| Releases | `release check`, `gate`, `publish`, `registry`, `update`, `verify` | Metadata preparation, reviewed publication gates and exact registry/GitHub verification |
| Review | `review branch`, `review uncommitted` | Opt-in CodeRabbit review with verified default base and streamed findings |
| SARIF | `sarif split` | Consumer-selected drivers/namespaces, corrected rule indices and complete output generations |
| Security | `security osv`, `secrets` | Managed OSV/Gitleaks, explicit inputs, full history, redacted native reports |
| Semgrep | `semgrep check-fixtures`, `scan` | Validate consumer rules, explicit inventory, reports and fixture expectations |
| Setup | `setup` | Require uv; install user Just and declared tools; sync the locked environment |
| Tectonic dependencies | `tectonic discover`, `export` | Read-only pkg-config/vcpkg discovery; consumer-owned provisioning |
| Templates | `templates NAME` | Shared changelog, release guide/OIDC workflow, just, TOML, and rumdl resources |
| Toolchain | `toolchain adopt`, `check`, `clean`, `export`, `python-check`, `python-tools-check`, `run`, `sync`, `sync-binaries`, `upgrade` | Exact declarations; managed installations and cleanup; verified execution and checked CI export |
| Validation | `validation cargo-examples`, `cargo-metadata`, `require`, `run` | Runtime Cargo example discovery, bounded live execution, package preflight and configured assertions |
| Workflow security | `zizmor check` | One declared scanner/persona, token discovery, explicit offline or required-online audits |

### Cargo examples

With the published release containing this capability pinned, define a consumer
plan such as `tooling/examples.toml`. Cargo discovers binary examples, including
nested and explicit targets. Consumers own feature choices, selection, budgets
and scientific assertions:

```toml
schema = 1
profile = "release"
timeout = 600
build-timeout = 1800

[examples.diagnostics]
features = ["diagnostics"]
expect = ["consumer-defined success marker"]
```

Omit `include` to run every discovered binary example. Ordinary examples build
once with default features; feature overrides build and run separately. Copy
your existing scientific markers into per-example `expect` arrays unchanged.
Add a thin consumer recipe and run `just examples`:

```just
examples:
    uv run --locked --group dev research-repo-tools toolchain run -- research-repo-tools validation cargo-examples tooling/examples.toml
```

Output is inherited by default. `expect` opts stdout into live byte forwarding
and literal matching; the child's buffering may change and temporary disk use
scales with output. Stdin and stderr remain inherited. See the
[CLI/configuration/Python contract][cargo-examples-api] and
[migration from static validation plans][cargo-examples-migration].

### Just recipes

For routine workflows, merge the [consumer justfile template][just-template] into
the consuming repository's justfile. These thin recipes invoke its locked package
version. After initialization, use `just` directly.
Run `just help`, `just help-workflows`, or bare `just` to list commands and their
arguments in lexicographic order.

| Consumer recipe | Purpose |
| --- | --- |
| `just changelog` | Generate, normalize, and archive completed minor series |
| `just changelog-archive` | Archive existing notes without regenerating history |
| `just changelog-check` | Validate the whole root changelog and all archives without writing |
| `just changelog-preview` | Validate the generated root and archives without writing; print the root candidate |
| `just changelog-release TAG DATE` | Generate a prospective release with an explicit `YYYY-MM-DD` date |
| `just changelog-unreleased TAG DATE` | Alias for `changelog-release` |
| `just check` | Run Python, Semgrep fixture, and zizmor validation; extend with consumer domain gates |
| `just ci` | Run the same canonical consumer gate as local validation |
| `just clean [ARGS...]` | Preview obsolete package-owned installs; `--apply` removes them; repeat `--keep-root PATH` to retain other consumers' pins |
| `just files COMMAND...` | Check raw line limits, list selected files or run a command over them |
| `just help` | List recipes and arguments in lexicographic order, with aliases inline |
| `just help-workflows` | Alias for `help` |
| `just notebook-advise FILE... [--strict]` | Report configured review warnings, optionally failing on them |
| `just notebook-check FILE...` | Validate notebook structure, cell IDs, and output policy |
| `just notebook-clear FILE...` | Deliberately clear generated notebook state |
| `just notebook-execute FILE...` | Execute selected notebooks and write results and reports |
| `just notebook-inspect FILE... [--json] [--no-preview]` | Inventory cells and repair problems without generating IDs or loading Jupyter |
| `just notebook-launch [--browser \| --no-browser] [--scratch-dir PATH]` | Launch locked JupyterLab with private session caches |
| `just notebook-lint FILE...` | Check structure, output policy, Python syntax, Ruff rules/formatting, and ty types |
| `just notebook-reset [PATH...] [--revision REF] [--apply]` | Preview source restoration and declared cleanup; explicitly apply with `--apply` |
| `just notebook-sync` | Synchronize locked notebook dependencies and the project kernel |
| `just papers COMMAND...` | Read explicit source dates, check PDFs or normalize their metadata |
| `just performance COMMAND...` | Compare Criterion samples, handle assets, and verify, render, or publish retained evidence |
| `just python-check` | Check lint, formatting, and types for all tracked and nonignored Python, including fixtures |
| `just python-fix` | Apply configured Ruff fixes and formatting to the same complete inventory |
| `just python-typecheck` | Run native ty without modifying source |
| `just release-check TAG` | Check final release metadata and generated notes |
| `just release-first TAG DATE` | Prepare a first release after checking published stable history |
| `just release-notes TAG` | Print release notes from the root changelog or an archive |
| `just release-publish TAG` | Approve a validated draft GitHub Release and trigger its registry workflow |
| `just release-tag TAG` | Create a local annotated tag from validated release notes |
| `just release-tag-preview TAG` | Inspect the release annotation without changing Git state |
| `just release-update TAG PREVIOUS DATE` | Prepare metadata with an explicit predecessor and date |
| `just release-verify TAG [ARGS...]` | Verify the exact stable registry version and GitHub Release/assets |
| `just review [base]` | Review branch and local changes; default to verified `origin/main` |
| `just review-uncommitted` | Review staged, unstaged, and non-ignored untracked changes |
| `just security-osv LOCKFILE...` | Audit explicitly selected uv.lock/Cargo.lock files |
| `just security-secrets [ARGS...]` | Scan full reachable history and current tracked/nonignored files |
| `just semgrep-check` | Validate the consumer's Semgrep rules and fixtures |
| `just setup` | Install and verify declared tools, then synchronize the Python environment |
| `just shared-python-plan VERSION` | Preview mandatory Python-minimum and optional tool-profile adoption outside the old environment |
| `just shared-python-update VERSION` | Apply the migration and recreate the locked environment and notebook kernel |
| `just tectonic COMMAND...` | Discover or export existing native dependency environments |
| `just tools-check` | Check installed tools and versions without installing them |
| `just tools-export` | Verify tools and append their environment to `GITHUB_ENV` |
| `just tools-python-check` | Check inherited Python tool declarations, lock, and executable versions without changes |
| `just tools-sync-binaries` | Install and verify pinned release binaries without package synchronization or dependency builds |
| `just update` | Upgrade tools, then Cargo and Python dependencies and the development environment |
| `just update-actions [ARGS...]` | Opt-in Actions pin update; use `--dry-run` or `--check` to preview the selected scope |
| `just update-cargo-dependencies` | Upgrade root Cargo requirements (including incompatible releases) and lock resolution; skip projects without a root Cargo.toml |
| `just update-cargo-tools` | Upgrade declared Cargo tools and release binaries; publish verified TOML pins |
| `just update-dependencies` | Run the Cargo and Python dependency workflows |
| `just update-python-dependencies` | Update direct dev pins, upgrade the full Python lock, and synchronize dev |
| `just update-python-deps` | Alias for `update-python-dependencies` |
| `just update-tools` | Upgrade uv and managed tools without repeating shell configuration |
| `just update-uv` | Upgrade uv through its installation owner and reconcile its pin |
| `just validate CONFIGURATION [NAME...]` | Check configured example outputs |
| `just workflow-allowlist-check` | Check steps and reusable workflows against strict consumer selected-actions settings |
| `just zizmor-check [ARGS...]` | Run local workflow audits; accept `--offline`, `--require-online`, and `--format sarif` |

To preview a prospective release, run
`just changelog-preview --tag v1.2.3 --date YYYY-MM-DD`.

### Authenticated release installation in GitHub Actions

`toolchain sync-binaries` installs the exact release-binary pins declared by the
consumer (dprint, gitleaks, osv-scanner, and rumdl). It checks warm caches and repairs damaged
executables through the same SHA-256-verified installer as setup. It does not
install Python, Rust, Cargo tools, Just, or project dependencies.

Install the locked tooling package first, then authenticate binary installation
in a separate step. This example uses an existing uv installation and the
consumer's committed manifest and lockfile:

```yaml
- name: Install locked tooling package
  run: uv sync --locked --managed-python --only-group tooling
- name: Install pinned release binaries
  env:
    GITHUB_TOKEN: ${{ github.token }}
  run: uv run --locked --no-sync --no-python-downloads python -I -X utf8 -m research_repo_tools toolchain sync-binaries
- name: Set up development tools and dependencies
  run: uv run --locked --managed-python --only-group tooling research-repo-tools setup
```

The credential-bearing step uses the already installed package without
synchronizing its environment. Release metadata requests authenticate with the
first nonempty `GITHUB_TOKEN`, then `GH_TOKEN`; asset downloads carry no lookup
credential. Setup commands and all toolchain version probes receive neither
variable, and handled release-installation diagnostics redact both values.
An explicit `toolchain run -- COMMAND ...` retains the caller's credentials for
that command. Other native build and package-index settings continue to pass
through. After setup, use `just tools-sync-binaries` for binary-only repair.

To reclaim obsolete managed Cargo tools, Rust toolchains, release binaries, and
Python interpreters, use the shared `clean` recipe. Include every other consumer
whose installed pins you want to retain:

```sh
just clean --keep-root ../la-stack
just clean --apply --keep-root ../la-stack
```

The default is a preview. Cleanup preserves current declarations, newer versions,
unknown tools, other hosts, and Python interpreters referenced by the selected
consumers' environments or uv tools. It only removes installations inside the
package-owned store; user-wide Cargo, rustup and uv Python installs, project
`.venv`, build artifacts, and dependency caches remain intact. See the
[cleanup contract](https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md#cleaning-obsolete-installations) for retention
rules and Windows guidance. Consumers copy the packaged recipe and pin the shared
package; the cleanup implementation lives here.

Follow the [toolchain guide][toolchain] for declarations, first-time setup, and
strictly read-only tool checks.

### Dependabot approval and auto-merge

The [shared GitHub workflow][dependabot] can approve Python, Rust, and GitHub
Actions updates permitted by each repository's Dependabot configuration, including
minor and major updates. Native auto-merge waits for required reviews and checks.
Each repository owns its Dependabot configuration, dependency-file allowlist,
Actions approval setting, and merge rules. Consumers
pin the reusable workflow to a reviewed Git commit; no Python package upgrade is
required. `research-repo-tools` is the first pilot consumer.

This workflow supersedes CodeRabbit approval polling and the
`CODERABBIT_REVIEW_TOKEN` personal access token requirement. It uses
`GITHUB_TOKEN` for approval and auto-merge. Any required CodeRabbit status
remains an independent merge gate.

### Workflow security and complete Python validation

The shared Python commands target v0.1.8; adopt an exact released pin. Merge the
packaged `python-validation.toml` policy into your pyproject and retain the full
existing Ruff configuration. It enables missing
annotations (ANN001/002/003/201/202/204/205/206), TC, and UP037. The `python-check`
recipe discovers `*.py` and `*.pyi` throughout the repository, including support
code, tests, and negative Semgrep fixtures. Use exact per-file/rule exceptions
for intentional violations. Extend `check` with existing Rust/domain tests and
native notebook linting; keep `semgrep-check` in the canonical local and CI gate.

The commands require an already synchronized development environment. Checks
never install or upgrade tools. `python check` runs Ruff lint, Ruff format checks,
and ty; `python typecheck` runs ty alone. Both disable automatic source changes.
`python fix` applies configured Ruff fixes followed by formatting; lint failures
from the fix pass still propagate. Native settings, nested configuration, and
precise exceptions remain active. Empty inventories require no validators;
opted-in declaration and lock checks still apply. Missing tools fail before
validation starts. All batches run and the first native failure
status propagates. Use `just setup` to prepare the locked environment.

Declare one zizmor scanner pin, either `zizmor==1.30.1` in the consumer's dev
dependency group or `zizmor = "1.30.1"` in its existing managed Cargo toolchain.
Keep the choice in that one repository-owned declaration. Configure an explicit
persona in pyproject.toml:

```toml
[tool.research-repo-tools.zizmor]
persona = "regular"
timeout = 300
```

After [setup][toolchain], run:

```sh
just python-check
just zizmor-check
just ci
```

Local auditing uses `ZIZMOR_GITHUB_TOKEN`, then `GH_TOKEN`, then authenticated
`gh` discovery. It reports an offline fallback when authentication is absent.
Use `just zizmor-check --offline` for deliberate offline checking, or
`just zizmor-check --require-online` in CI. Scanner errors propagate and never
trigger an offline retry. Token values are not printed or stored by the wrapper.

The packaged `zizmor.yml` workflow runs the finding gate and then produces SARIF
with the same pin/persona. SARIF output alone does not fail on findings; the
plain gate does. Fork and Dependabot PRs retain audits and skip privileged upload.
The installed
[validation and migration guide](https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/VALIDATING_WORKFLOWS.md)
documents authentication, permissions, Python 3.14 annotations, exact negative
fixture exceptions, notebook wiring, and consumer CodeRabbit settings. Extract it
with `templates VALIDATING_WORKFLOWS.md`; `templates python-validation.toml` and
`templates zizmor.yml` expose the corresponding reusable examples.

### CodeRabbit review

Install and authenticate the [CodeRabbit CLI](https://docs.coderabbit.ai/cli)
explicitly and ensure `coderabbit` is on PATH. The package does not install it,
authenticate, enable usage credits, or retry reviews automatically. These recipes
are opt-in and remain outside `just check` and `just ci`. Agents must have an
explicit maintainer request before invoking a live review.

```sh
just review
just review main
just review-uncommitted
```

`just review` includes committed branch changes plus staged, unstaged, and
non-ignored untracked files. Before invoking CodeRabbit, it compares the local
`origin/main` commit with the live `origin` main branch. Missing or stale refs
stop with fetch guidance; failed or malformed remote lookups also stop review.
The wrapper never fetches or changes Git state. Verification applies at invocation,
not throughout a long review. An explicit local base such as `main` is checked
locally without a remote query. Uncommitted review skips the base check entirely.

The configured consumer root must contain `AGENTS.md` and exactly one of
`.coderabbit.yaml` or `.coderabbit.yml`; missing files, directories in place of
files, or both configuration names are errors. Both files are passed as additional
instructions. Output uses CodeRabbit's structured `--agent` mode and streams directly
to the terminal without the usual five-minute subprocess timeout. Nonzero exit
statuses propagate, and interruption returns 130. Service or authentication errors
are unavailable reviews, not clean results. Verify findings and suggested changes
against current code before acting on them.

### Workflow examples

```sh
just changelog-archive
just changelog-preview --tag v1.2.3 --date 2026-09-07
just help
just release-check v1.2.3
just release-notes v1.2.3
just tools-check
just update
```

`just update` upgrades uv first. Standalone uv installations use
`uv self update`; Homebrew installations use `brew upgrade uv`; verified `uv tool`
installations use their native tool upgrader with the verified existing interpreter and
requirement constraints. Other installation owners must update uv themselves.
The updater records the resulting stable version
in `pyproject.toml`. This changes the shared
user installation; other projects with different exact uv pins must be reconciled
before their commands will run. Review the generated changes before committing.
`just setup` installs declared versions without upgrading them.

The underlying CLI remains available for integrations and custom recipes; see
[supported interfaces][api].

### Reviewed registry publication

Use the shared [release procedure](docs/RELEASING.md) and packaged `RELEASING.md`
template for the same ordered commands in Python and Rust repositories.
`just release-publish TAG` approves a validated draft GitHub Release; the consumer's
reviewed workflow performs the registry upload after environment approval.
`just release-verify TAG --attempts 7 --interval 10` checks both the stable GitHub
Release/assets and the exact PyPI or crates.io version. Registry lookup failures
never imply absence or authorize another upload.

The packaged `publishing.toml` and `publish-crates.yml` templates define the shared
policy and official crates.io Trusted Publishing/OIDC action wiring. Consumers
declare required exact-commit checks and assets and retain native Cargo selection,
features, MSRV, dependency order and deployment settings. See the
[release checklist](docs/RELEASING.md), [API](docs/api.md#reviewed-registry-publication-api),
and [migration](docs/release-policy-migration.md#shared-release-commands). Adopt the exact published v0.1.8
package before consumer deployment. Publisher registration and live uploads are
separate adoption steps recorded in the maintainer's private setup task.

`toolchain run` uses a managed `CARGO_HOME`; an ordinary local `cargo login` may
configure a different home. The OIDC workflow supplies its temporary token through
the upload step's environment. Review consumer credential-provider overrides for
that flow.

To retain authored dependency release-note links while using the common changelog
template, set `changelog.dependency-bodies = "preserve"` in the package configuration
and remove the copied `cliff-config`. The default `"concise"` keeps short dependency
summaries; both policies retain the common grouping and full breaking descriptions.

### Shared Python adoption

Every managed consumer automatically inherits the exact installed release's
published `Requires-Python` minimum, currently Python 3.14. This mandatory
language-feature guard applies to installable applications and dependency-only
projects. It does not raise the current baseline. No opt-in is required, and
development inheritance controls cannot disable the guard.

Optionally mirror the release's selected development interpreter as well:

```toml
[tool.research-repo-tools.toolchain]
inherit-python = true
```

The installed release's support minimum and selected development minor are
separate authorities. Adoption intersects `project.requires-python` with the
published minimum, preserving stricter lower bounds, upper bounds, and exclusions.
Incompatible ranges fail for review before resolution or publication. Installable
consumers also retain and reconcile uv constraints on the shared tooling groups.
With `inherit-python = true`, `.python-version` mirrors the selected development
minor. Otherwise a compatible local selector remains; an obsolete or missing
selector advances to the shared selection. A stricter range excluding that
selection needs a compatible local selector or an explicit compatibility decision.
Ruff and ty
infer targets from project metadata. Consumer lint rules, fixture exceptions,
notebook policy, and deliberate lower targets remain consumer decisions.

After the target version is published, use the packaged standalone bootstrap:

```sh
research-repo-tools templates python-bootstrap.py --output python-bootstrap.py
# Merge the matching published Justfile recipes into the consumer's Justfile.
just shared-python-plan 0.1.8
just shared-python-update 0.1.8
just python-check
```

These recipes run the exact target package outside the old project environment,
using the packaged standard-library-only `python-bootstrap.py` beside the Justfile.
Copy both templates when installing this contract. The helper runs on older
uv-supported Python, reads the exact target release's PyPI `Requires-Python`, then
requests a compatible managed interpreter for the isolated target package. The
executing package's installed metadata remains the support authority. An obsolete
selector, lock, environment or Python on PATH cannot control startup. Preview
resolves and installs a temporary candidate; it may download Python/packages
and populate caches, but leaves consumer files and `.venv` unchanged. Apply
updates all direct shared-package pins (including extras), application metadata,
`uv.lock`, the environment, and the configured notebook group's project kernel.
Resolution and candidate installation must succeed before publication.

For offline bootstrap or local wheel/sdist evaluation, the helper accepts
`--metadata-file PATH` containing saved exact-release PyPI JSON. Supply the local
artifact registry through uv's `UV_FIND_LINKS`; target name/version are verified,
and the installed distribution still enforces its own metadata before adoption.

A caught failure restores original files and the prior environment. Recovery
errors identify the retained backup; do not delete it before recovering. Close
processes using `.venv` on Windows before applying. This is not a crash-atomic
or concurrent-writer transaction. Include every required source in the tracked
or nonignored inventory; external local path dependencies need a separately
reviewed migration. `just python-check` detects mirror drift without repairing
it or downloading an interpreter. Ordinary Python/toolchain checks and setup
reject insufficient application metadata even with `inherit-python = false`
or no setting. Routine checks do not advance versions or rewrite declarations.

The metadata change is a public compatibility change: rebuild and validate the
consumer wheel and sdist after adoption, then publish those artifacts through
the consumer's reviewed release workflow. Their `Requires-Python` rejects older
interpreters before execution, including installation without tooling groups or
research-repo-tools. Run the ordinary guard in CI before consumer builds.
An implementation merge does not publish this capability; consumer upgrades
require an exact PyPI release containing it. See the [migration guide][migration].

### Shared Python tool versions

The optional `python-tools` extra is the single Ruff, ty, and pytest version
authority for each exact shared release. The initial profile targets v0.1.8 and
uses Ruff 0.16.9, ty 0.0.84, and pytest 9.1.1. Base installations do not include
these development tools. Opt in independently of `inherit-python`:

```toml
[tool.research-repo-tools.toolchain]
inherit-python-tools = true
```

Keep your current exact shared-package pin in `tooling` and include that group
from `dev`. After v0.1.8 is published, the same standalone adoption recipes
prepare the new extra, declarations, lock, and environment:

```sh
just shared-python-plan 0.1.8
just shared-python-update 0.1.8
just tools-python-check
just python-check
```

Preview prints concrete manifest, selector (when inherited), and lock diffs after
resolving and installing a private candidate. Adoption adds `python-tools` to all
shared-package group requirements, retaining other extras such as `notebooks`.
It converts old simple exact Ruff/ty/pytest pins in dependency groups into
unversioned requirements, so the installed release owns their versions. Compatible
ranges can also be retired; conflicting ranges, conditional requirements, URLs,
extra requests, and uv source/constraint overrides fail before publication.
Public runtime or optional dependencies on these tools require a deliberate
consumer decision: adoption does not rewrite them. Other dependencies, native
lint/type settings, precise fixture exceptions, and notebook packages stay local.

Tool inheritance retains compatible `.python-version` and native Python targets;
the mandatory application minimum still applies. uv constrains the groups
containing the shared package to its runtime requirement while retaining local
restrictions. An incompatible selected interpreter or runtime range fails
explicitly. Both forms of inheritance use the
same candidate verification and recoverable apply process described above.

`just tools-python-check` checks declarations, locked versions, and the actual
executables selected by PATH. `just python-check`, `just python-typecheck`, and
managed tool checks enforce the profile too. They never synchronize or upgrade.
`just setup` synchronizes the existing lock. `just update-python-dependencies`
updates unrelated development pins and the lock while retaining the exact shared
release and inherited versions. Upgrade the profile only by adopting another exact
shared release. Tools outside Ruff/ty/pytest remain consumer-owned.

To opt out, disable `inherit-python-tools`, remove only `python-tools` from each
shared-package requirement, and choose consumer-owned tool requirements in the
appropriate groups. Keep any `notebooks` extra and `inherit-python` setting you
still need. Run `just update-python-dependencies`, then `just setup` and your
validation gates. Disabling the check alone does not remove the extra's pins.
Consumer rollout is tracked in [#30](https://github.com/acgetchell/research-repo-tools/issues/30);
retain downstream orchestration until the published pin passes its focused checks.

### Dependency and secret scanning

Declare the optional release binaries and run setup:

```toml
[tool.research-repo-tools.toolchain.binaries]
gitleaks = "8.30.1"
osv-scanner = "2.6.0"
```

```sh
just setup
just tools-check
just security-osv uv.lock Cargo.lock
just security-secrets
```

Supply only the lockfiles the consumer actually owns. Use `just files` to inspect
tracked and nonignored inputs for repositories containing several packages.
OSV checks each explicit supported lockfile with Go and Rust call analysis
disabled; it does not execute dependency build scripts. Advisory queries need
network access. Missing inputs, uncovered sources, scanner failures, malformed
or missing reports, and findings all block the gate.

Gitleaks checks all reachable Git history and a private snapshot of current
tracked/nonignored files, including uncommitted new files. Full history is
required (`fetch-depth: 0` in Actions). Environment/build directories are excluded
from the working snapshot. Selected symlinks and submodules fail explicitly;
scan submodules as separate repositories. Native history scanning covers patches,
not unreachable objects, nested repositories, archive contents or binary blobs.
Extra working-tree exclusions are explicit `--exclude` arguments.

Reports go to `target/security`: numbered OSV JSON/SARIF pairs and Gitleaks
history/working pairs. Findings retain native rule IDs and locations. Gitleaks
uses complete native secret redaction; surrounding match text and commit messages
are also removed before reports are published. Inline `gitleaks:allow` and
ambient `.gitleaksignore` bypasses are disabled. Consumer `.gitleaks.toml` policy
is honored; use `--scanner-config PATH` for another reviewed configuration.
Raw scanner logs are not echoed because they can include sensitive metadata.
Validated findings are printed by default: OSV identifies the lockfile,
package/version and vulnerability; Gitleaks identifies the rule and location
separately for history and the working tree. Semgrep prints the rule and source
location. Each scan prints a finding count and published report paths, with
findings listed once across JSON and SARIF. Terminal output omits descriptions
that could interpolate source values, snippets, adjacent matches and commit
messages. Keep the aggregate `just security` recipe and scan policy local.
The first nonzero scanner status is returned; invalid successful reports return
1, and timeouts return 124. Checks never install or select an ambient scanner.

The standalone binaries require neither a paid GitHub security product nor a
Gitleaks Action license. Consumers own exceptions, schedules, Actions permissions,
and whether SARIF uploads are available for their repositories. See the
[migration map](https://github.com/acgetchell/research-repo-tools/blob/main/docs/shared-capability-migration.md) before deleting local tools.

### First releases and Rust documentation scans

Declare `cargo-deny = "0.20.2"` in the managed Cargo table to run the exact
`cargo deny` through the shared toolchain. Keep `deny.toml` policy local.

`just release-first TAG DATE` permits an Unreleased-only changelog while preparing
the first stable release. It verifies that published stable release history is
empty; failed GitHub queries never count as empty history. Draft/prerelease
records are not predecessors. For an explicitly reviewed offline first release,
invoke the CLI with `release update TAG --first-release --offline --date DATE`.
Previous-tag selectors require a real predecessor and final validation still
requires a dated release heading. No synthetic `v0.0.0` predecessor is inserted.

Consumers can wrap `semgrep scan --include '*.rs' --include '*.py' --include '*.md'
--rust-docs` in their own `just semgrep-scan` recipe. Keep rules and scope local.
The shared scan checks native errors, exact coverage and agreement of active
JSON/SARIF findings from the same native invocation. It scans batches of at most
100 files, also bounded by portable command length. Defaults are one job,
120 seconds per rule/target, a 300-second process timeout, disabled inline
`nosem`, and aggregate `semgrep.json`/`semgrep.sarif` reports. The aggregate
SARIF contains one run with the stable `semgrep` category. Set consumer policy
explicitly when reviewed suppressions should apply:

```toml
[tool.research-repo-tools.semgrep]
config = "semgrep.yaml"
fixtures = "tests/semgrep"
batch-size = 100
inline-suppressions = true
jobs = 1
report-category = "semgrep-repository-rules"
report-layout = "aggregate"
target-timeout = 120
timeout = 600
```

Matching CLI overrides are `--batch-size`, `--inline-suppressions` /
`--no-inline-suppressions`, `--jobs`, `--report-category`, `--report-layout`
and `--target-timeout`. `numbered` layout writes one JSON/SARIF pair per batch.
Each layout owns the entire output directory: successful generations remove
all prior members, including reports from larger inventories or other layouts.
Missing/malformed reports, scan errors in JSON, incomplete coverage and timeouts
publish an empty generation. Generation or filesystem failures preserve or
restore the previous directory, with recovery backups retained if rollback fails.
Use a dedicated output directory. Native failures and active findings block;
accepted inline suppressions remain marked in SARIF and do not count as active
findings. Explicit file arguments include Python/Rust tests normally ignored by
Semgrep; deliberately excluded fixtures remain consumer policy. Rule path filters
remain active, and skipped required inputs fail the coverage gate.

Rust fences in
Markdown and line/block rustdoc comments preserve source line numbers and hidden
`# ` lines. Macro-generated docs and `#[doc = ...]` attributes are outside this
adapter's scope. `semgrep check-fixtures --rust-docs` adapts annotated Markdown
fixtures to the existing shared assertion checker; count-based expectations use
a separate fixture gate. Findings and fixture mismatches remain blocking.

### SARIF selection and complete figure publication

Declare exact driver names and rule-ID namespace prefixes. An empty prefix list
keeps all rules for that driver; unlisted drivers are omitted. No Codacy or
repository-specific selection is built into the package.

```toml
[tool.research-repo-tools.sarif]
category-prefix = "codacy"

[tool.research-repo-tools.sarif.drivers]
"Opengrep (reported by Codacy)" = ["project."]
ruff = []
```

A consumer recipe can invoke `sarif split input.sarif --output target/sarif`
and upload that dedicated directory. Keep the input outside the output directory;
overlapping source paths and portable case/Unicode aliases are rejected. Repeated driver names get distinct stable
categories; unrelated metadata is retained and indexed driver rule references
are corrected. Empty generations remove stale output files. Invalid/non-finite
input fails before publication. Extension rule references are rejected; see the
[supported SARIF API](https://github.com/acgetchell/research-repo-tools/blob/main/docs/api.md#sarif-api) for the precise subset.
Add `--github-output "$GITHUB_OUTPUT"` to append `SARIF_DIRECTORY`,
`SARIF_HAS_UPLOADABLE_RUNS` and `SARIF_RUN_COUNT` after publication through the
existing checked `ci.export_environment` API. Upload permissions, conditions and
categories remain consumer-owned.

Use the same directory primitive for a complete scientific figure set:

```python
from pathlib import Path
from research_repo_tools.files import publish_directory

with publish_directory(Path("figures/validation")) as candidate:
    render_figures(candidate)  # Consumer-owned rendering and figure names.
    validate_figures(candidate)  # Consumer-owned scientific assertions.
```

Only after generation and validation finish does the directory replace the
previous figure set. Caught commit failures restore the original tree; incomplete
rollback reports a retained recovery directory. See the
[consumer deletion map](https://github.com/acgetchell/research-repo-tools/blob/main/docs/shared-capability-migration.md) before removing
working implementations; adoption requires an exact published release.

### Dependency and tool updates

`just update` runs `update-tools` before `update-dependencies`. Every recipe stops
at its first failed command; completed steps remain applied for review and retry.
The aggregate is not a transaction across package managers. Dependency-only
recipes use the installed, declared toolchain and leave uv and Cargo tool pins
unchanged. Run setup first when adopting or changing tool declarations.
Update launchers synchronize only `tooling` and retain installed consumer
dependencies, so they can start before a native consumer is ready to rebuild.

`just update-python-dependencies` advances exact direct `dev` pins, upgrades the
entire lock resolution within the resulting manifest constraints, and explicitly
synchronizes `dev`, even with `default-groups = []`. The final sync runs with
checked managed tools so native Python builds can find Rust. Included groups,
including the pinned shared package in `tooling`, retain their declared constraints.
The older `update-python-deps` spelling is an alias for this complete workflow.

For Rust projects, pin `cargo-edit` to an exact version in the managed Cargo tool
table (for example, `cargo-edit = "0.13.13"`) and keep the compiler pinned in
`rust-toolchain.toml`, then run `just setup` to install and verify the declared tool.
For both `cargo upgrade` and direct `cargo-upgrade` invocations, `toolchain run`
rejects missing or non-exact `cargo-edit` declarations even when an unmanaged copy
is on PATH. The default Cargo recipe upgrades
requirements with incompatible releases allowed, then updates `Cargo.lock`.
It skips Cargo when the root has no `Cargo.toml`, keeping Python-only consumers
supported. Additional resolution roots and coupled dependency exclusions belong
in the consumer's `update-cargo-dependencies` recipe. When merging the template,
preserve those decisions; the aggregate calls that recipe by name. For example:

```just
update-cargo-dependencies:
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- \
        cargo upgrade --incompatible allow --exclude coupled-a --exclude coupled-b
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- \
        cargo upgrade --manifest-path "fixtures/extra root/Cargo.toml" --incompatible allow
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo update
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo update --manifest-path "fixtures/extra root/Cargo.toml"
```

Exclusions apply to requirement upgrades; lock resolution still follows all
declared constraints. Keep the required order of these commands and retain
consumer checks for coupled dependencies and additional manifests. Review both
manifests and lockfiles after updating. See [update adoption][update-adoption]
for superseded policies and consumer integration requirements.

#### User-installed tool updates

A machine-configuration repository can use the same dependency commands while
owning its user-wide Cargo installations. Keep the shared package's exact pin in
the `tooling` group, included by `dev`, and map only the Just pins the repository
owns:

```toml
[tool.research-repo-tools.deps.tools]
nextest_version = "cargo-nextest"
uv_version = "uv"
```

Merge these thin recipes into the repository's existing update workflow:

```just
# Validate the selected uv executable's stable X.Y.Z version output.
check-uv:
    uv run --locked --only-group tooling --inexact research-repo-tools deps check-uv

# Upgrade user-installed Cargo packages before reconciling the owned Just pins.
update-cargo-tools:
    cargo install-update --all --locked
    uv run --locked --only-group tooling --inexact research-repo-tools deps update-tools

# Advance direct dev pins, then refresh the entire lock and synchronize dev.
update-python-dependencies:
    uv run --locked --only-group tooling --inexact research-repo-tools deps update-python
    uv lock --upgrade
    uv sync --locked --group dev
```

`cargo install-update` requires the separately installed `cargo-update` package.
Its `--all` scope covers the user's Cargo installations; `--locked` retains each
tool's published dependency resolution. A failed Cargo upgrade stops before pin
reconciliation. Successfully upgraded tools remain installed and can be reconciled
on retry. `deps update-tools` reads `cargo install --list` and the selected uv
version, validates every mapped assignment, and updates only those Just pins.
It does not install tools; `--dry-run` previews the pin changes.

`deps check-uv` validates stable version syntax; it does not install uv or assert
equality with a Just pin. Python pin updates preserve intentional ranges, markers,
extras, and included-group pins. A universal resolution requiring multiple
versions for one direct exact pin fails before changing the manifest or lock.
Even when no direct pin changes, the recipe still upgrades the full lock and
synchronizes `dev`.

Shell bootstrap, Homebrew, stow, and machine-wide update ordering remain consumer
policy. The standard research-project template continues to use isolated managed
Cargo installations through `toolchain upgrade`.

#### Mixed installation owners on Linux and macOS

Declare Homebrew ownership explicitly for mapped user tools. Tools without an
owner entry retain the verified Cargo-inventory contract; a working PATH binary
does not establish ownership. The selected executable must resolve into the
formula prefix and agree with Homebrew's installed inventory before reconciliation:

```toml
[tool.research-repo-tools.deps.tools]
nextest_version = "cargo-nextest"
format_version = "dprint"

[tool.research-repo-tools.deps.tool-owners]
cargo-nextest = "cargo"
dprint = "homebrew"
```

For prebuilt dprint and rumdl, migrate their pins to the managed release-binary
table and remove the corresponding Cargo declaration or Just variable. The
managed store verifies upstream release URLs, SHA-256 digests, and executable
versions before publishing pins. Just remains supplied by the shared package's
exact `rust-just` dependency; remove competing Just pins when adopting this model.

```toml
[tool.research-repo-tools.toolchain.binaries]
dprint = "0.60.1"
rumdl = "0.2.78"
```

Both tools support x86_64/aarch64 macOS and glibc Linux, plus x86_64 Windows;
dprint also supports Windows ARM64. Missing release assets or checksums fail.
Use the explicit Cargo declaration for rumdl on Windows ARM64.

Keep a machine's Homebrew/Brewfile policy conditional in its own recipe. This
example composes the portable stages without invoking shell setup:

```just
update: update-platform update-tools update-dependencies

update-platform:
    if [ "$(uname -s)" = Darwin ]; then brew update && brew upgrade && brew bundle; else echo "Skipped Homebrew/Brewfile stage: macOS policy only"; fi

update-tools: update-uv update-cargo-tools

update-uv *args:
    uv run --no-config --no-sync --no-python-downloads research-repo-tools deps update-uv "$@"
```

Linux needs no Homebrew. Retain the existing dependency recipes, Cargo exclusions,
and additional manifests. User-wide Cargo upgrades remain an explicit consumer
choice. Updating never installs Homebrew or invokes shell/bootstrap setup.
`just update-uv --dry-run` identifies the selected installation owner and operation
and previews project-pin reconciliation without upgrading or writing. The native
owner determines the available upgrade within its retained constraints;
`toolchain upgrade --dry-run` previews exact managed Cargo/prebuilt targets, and
`deps update-tools --dry-run` previews verified user-tool pin reconciliation.
Unknown ownership and missing tools fail. After a package-manager failure,
completed installations remain; dependent recipes stop. Repair the reported cause
and retry. There is no transaction across managers.

### Actions allowlists and opt-in pin updates

The consumer's committed GitHub selected-actions payload is the sole allowlist.
Use the strict payload with `github_owned_allowed: false`, `verified_allowed:
false`, and `patterns_allowed` containing exact `owner/repo[/path]@*` identities.
Wildcards in identities, owner-wide exemptions, duplicate JSON fields, and other
policy shapes fail. Repository identity matching ignores case; nested paths match
exactly. A repository entry does not also approve its subpaths.

Merge the packaged `workflow-allowlist-check` recipe, pointing it at that existing
policy, then add it to the consumer's check/CI gate:

```just
workflow-allowlist-check:
    uv run --locked --group dev research-repo-tools actions allowlist --policy .github/settings/allowed-actions.json .github/workflows
```

```sh
just workflow-allowlist-check
```

The checker reads explicit files or YAML directories and inspects both
`jobs.<id>.uses` calls and every step's `uses`. Quoted/folded scalars and aliases
are supported; alias findings point to the anchor. Duplicate/merge mappings and
recursive aliases fail with file/line diagnostics. Local `./` actions and
`docker://` containers are outside this external policy. Keep actionlint for
workflow schema and zizmor for full-SHA pinning/security; no repository settings
are changed. Consumers can delete their generic YAML allowlist checker after
pinning a published version with this capability and validating their own policy.

Action updates require separate, explicit opt-in targets in
`.github/action-updates.toml` (also available as the `action-updates.toml` template):

```toml
[actions]
"actions/checkout" = "latest"
"owner/repo/subpath" = "v2.1.0"
```

`latest` selects GitHub's published stable release and permits major upgrades.
An explicit stable version tag restricts the target. GitHub CLI (`gh`) is required
for read-only release/tag lookup, including private repositories. Annotated tags
are peeled to full commit SHAs. Existing selected references must already use
full SHAs and ordinary version comments, such as `# v1.2.3`; the updater retains
subpaths, quote style, trailing comment text, other workflow bytes, and line endings.
Unsupported scalar forms, aliases when rewriting workflows, missing targets, or lookup
failures abort before publication. References outside the selected identities
remain unchanged and appear as skipped in the report.

Merge the packaged `update-actions` recipe and review the report before applying:

```sh
just update-actions --dry-run
just update-actions --check
just update-actions
```

Preview/check reports include action identity, location, old/new full SHAs, and
old/new version comments. `--check` returns nonzero if updates are needed;
`--dry-run` returns zero for a valid preview. Unchanged targets are no-ops.
To include this stage in deliberate maintenance, opt in by changing the aggregate
to `update: update-tools update-dependencies update-actions`. Actions compatibility
then checks the final tool and Python dependency pins. Keep Dependabot's
scheduled PR review policy in the consumer; this command neither enables nor
disables it. Review overlapping proposals before merging.

Prefer the shared direct zizmor scanner, with one scanner pin. A retained
`zizmorcore/zizmor-action` fails maintenance unless its upstream support inventory
is explicitly checked, including when that wrapper is outside the update targets:

```toml
[compatibility."zizmorcore/zizmor-action"]
path = "support/versions"
tool = "zizmor"
```

The tool version comes from its existing exact Cargo/prebuilt, Python-group, or
mapped Just declaration. Conflicting authorities fail. The inventory at the
candidate or retained SHA must contain that version in the first column of a
plain version list (optional opaque columns, comments/blank lines and `latest`
are allowed; duplicate or unsupported entries fail). This avoids an embedded compatibility
table. Other wrappers may opt into the same contract. This detects absent version
support, not every possible runtime incompatibility; retain focused consumer CI.

`changelog tag TAG` creates a local annotated tag when explicitly invoked.
`--dry-run` previews it. Generation needs git-cliff; tagging needs Git. Optional
formatting needs rumdl, fixture validation needs Semgrep, and dependency
updates need uv or Cargo. These executables are required only by the commands
that invoke them. The CLI validates existing GitHub draft releases and uploads
verified assets; `performance release-upload --publish` explicitly publishes the draft.
This repository's tagged-release workflow publishes the tooling package to PyPI.

`just changelog` generates and normalizes history, then moves completed minor
series to `docs/archives/changelog/`. The root file keeps Unreleased, the newest
minor series, and archive links. The same [changelog recipes][changelog] are
available here and in the consumer justfile template.

External-tool installation is explicit through `setup`, `toolchain sync`, or
`toolchain upgrade`; managed execution selects the checked versions.
Scientific benchmarks, broader evidence schemas, plotting, and deployment workflows
remain outside this package. Scientific algorithms,
case inventories, repository rules, and custom release commands stay in their
consuming repositories.

### Notebooks

For notebook work, add `research-repo-tools[notebooks]==X.Y.Z` at the same version
as the tooling pin to a `notebook` dependency group. Keep analysis libraries in
that group, and declare Ruff 0.16.8 or newer and ty 0.0.82 or newer in `dev` for
linting. Refresh the lockfile, then use `just notebook-sync`. It registers a
kernel in the project environment. Maintenance-only users need no Jupyter packages.

```sh
just notebook-sync
just notebook-lint notebooks/analysis.ipynb
just notebook-execute notebooks/analysis.ipynb
```

Execution leaves source files untouched and writes executed notebooks and JSON
reports under `target/notebooks`, preserving root-relative paths. Consumers own
fast/slow selections, input preparation, scientific assertions, and figure
destinations. See the [notebook contract](https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_NOTEBOOKS.md) for output
policy, failure reports, and environment configuration. Linting uses the locked
project's Ruff and ty with the consumer's configuration and preserves cell IDs
in diagnostics. `notebook-check` provides structure and output checks alone.

The ID-pattern, JupyterLab launch, and source-reset interfaces target **v0.1.8**.
Adopt an exact published PyPI version containing them before retiring consumer
implementations. Merge their recipes from the packaged template after publication.

For lowercase kebab-case cell IDs, opt into a full-match spelling policy:

```toml
[tool.research-repo-tools.notebooks]
id-pattern = '[a-z0-9]+(?:-[a-z0-9]+)*'
```

`just notebook-lint FILE...` applies this pattern to every existing cell ID.
`--id-pattern PATTERN` overrides it for one invocation. Presence, uniqueness,
ASCII spelling, and the nbformat 1–64 character limit still apply. Without a
pattern, the broad nbformat contract is preserved. Descriptive-ID advice remains
an independent gate through `just notebook-advise FILE... --strict`. Neither
command generates, repairs, or renumbers IDs.

For interactive work, declare JupyterLab in the consumer's notebook dependency
group, refresh the lockfile, and run `just notebook-sync`. Configure browser and
scratch storage in the consumer manifest:

```toml
[tool.research-repo-tools.notebooks.lab]
browser = false
scratch-dir = "target/jupyter"

[tool.research-repo-tools.notebooks.reset]
sources = ["notebooks"]
scratch = ["target/notebooks", "target/jupyter"]
checkpoints = ["notebooks/.ipynb_checkpoints"]
```

```sh
just notebook-launch --browser
just notebook-reset
just notebook-reset --apply
```

Launch selects the locked managed Python, `dev`, and configured notebook group.
Its private Jupyter, IPython, and Matplotlib session state lives beneath the
selected scratch directory and is removed when the server exits. Browser opening
defaults to false; `--browser` and `--no-browser` override configuration.

Reset previews the exact restore and deletion map. `--apply` explicitly approves
both working-tree restoration and declared cleanup. Source entries are literal
files or directories selecting tracked `.ipynb` files, including deleted working
files. The default restore source is the index; `--revision REF` selects a pinned
Git tree. The index and other working files are preserved. Cleanup deletes only
the declared scratch files/directories and explicitly named checkpoint directories;
it never discovers checkpoint directories recursively. Unsafe paths, source aliases,
tracked cleanup targets, links, and junctions are rejected before restoration.
See [the workflow contract](docs/RUNNING_NOTEBOOKS.md#interactive-launch-and-explicit-reset)
for failure behavior, supported paths, and the consumer deletion map.

Read-only review commands are available from `0.1.5`. Merge their recipes from
the packaged template when updating an older consumer. Inspection uses only the base installation
and tolerates older nbformat 4 files and cell fields awaiting repair:

```sh
just notebook-inspect notebooks/analysis.ipynb
just notebook-inspect notebooks/analysis.ipynb --json --no-preview
```

Previews expose up to 80 characters of cell source; use `--no-preview` for
sensitive notebooks. Inspection never prints stored outputs or metadata, generates
IDs, executes cells, or rewrites notebooks. It reports structural problems but
does not certify validity. See the [inspection schema](https://github.com/acgetchell/research-repo-tools/blob/main/docs/notebook-inspection.md).

Advisory policy is opt-in through a separate command, run alongside native lint:

```sh
just notebook-advise notebooks/analysis.ipynb
just notebook-advise notebooks/analysis.ipynb --strict
```

Without configuration, advice warns only about IDs that look generated or positional.
Configure additional policy in the consumer's `pyproject.toml`, for example:

```toml
[tool.research-repo-tools.notebooks.advice]
descriptive-ids = true
ruff-rules = ["ANN001", "ANN201", "BLE001", "TID251"]
strict = false
subprocess-timeout = true

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"pandas" = {msg = "Prefer Polars for this project"}
"csv" = {msg = "Prefer Polars for this project"}
```

Ruff supplies annotations, broad-exception, and configured import warnings;
library preferences remain consumer policy. The optional timeout heuristic covers
direct calls spelled `subprocess.run`, `call`, `check_call`, or `check_output`
without an explicit non-`None` timeout. It skips entire cells that cannot be parsed
as plain Python, including magics. See
[advisory behavior and limits](https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_NOTEBOOKS.md#review-advisories).

#### Notebook dependency installation policy

Consumers can opt into a blocking source policy with:

```toml
[tool.research-repo-tools.notebooks]
prohibit-installs = true
```

`just notebook-lint FILE...` then rejects literal dependency-changing pip, uv,
conda, and mamba commands in line magics, `!` commands, `%%bash`, `%%sh`, and
`%%script bash/sh` cells. It also checks literal `subprocess` calls and
`os.system` commands in Python cells, including after line magics and inside
`%%capture`, `%%debug`, `%%prun`, `%%time`, and `%%timeit` cells.
Diagnostics retain notebook paths,
cell IDs, and original source line numbers. The policy never runs cells or
changes their source. Python strings and comments containing installation
examples are not commands.

This is a source convention, not a sandbox: dynamically assembled commands,
aliases, wrapper functions, arbitrary cell magics, and shell heredoc semantics
are outside its contract. Keep dependency installation in the project setup;
put quoted Python examples in strings or Markdown. Review unusual shell cells
manually; the consumer can disable this policy and retain its own stricter gate
if its syntax is outside the supported set. Native Ruff and ty remain responsible
for Python syntax and type checks.

### Raw text line limits

These interfaces target v0.1.8. Adopt an exact published version containing them;
an upstream merge does not update consumer pins. The consumer declares selection
and the positive limit in its `pyproject.toml`:

```toml
[tool.research-repo-tools.text]
line-limit = 160
include = ["*.md"]
exclude = ["CHANGELOG.md", "docs/archive/**", "docs/archives/changelog/**"]
```

```sh
just files check-lines
just files check-lines --limit 100 --include '*.txt' --exclude 'generated/**'
```

Each supplied option replaces its configured value; repeated include/exclude
options form that override's list. Shared selection uses Git pathspec includes
and case-sensitive POSIX glob excludes, fails on discovery errors, and checks
every selected raw line. Fences, tables, URLs, tabs and trailing spaces count.
Lengths are Unicode code points, independent of locale, rather than bytes or
display columns. LF/CRLF/CR endings do not count; final lines without a newline
do. Diagnostics are `file:one-based-line: line length N exceeds LIMIT` on stderr.
Violations return 1, a clean or empty inventory returns 0, and read/selection or
configuration errors return 1. The gate never formats source. The separate
`docs check-lines FILE...` preserves its established fixed 160 limit and table
exemption. Use `files check-lines` for a strict raw-source policy.

### Reproducible papers and native dependency discovery

PDF operations are opt-in. Add `research-repo-tools[papers]==X.Y.Z` to the
consumer's tooling group, using an exact published version containing the
interfaces, refresh its lock and run setup. Source-date and dependency discovery
work with the base package. Keep content, figures, bibliography, source/output
locations, fixed dates and refresh decisions in the consumer.

```toml
[tool.research-repo-tools.papers.documents.example]
tex = "papers/example.tex"
pdf = "target/papers/example.pdf"
identity = "papers/example.tex"
min-pages = 1
require-text = ["Example title", "REFERENCES"]
forbid-text = ['\today']
# reference = "papers/example.pdf" # Enable retained-artifact equivalence.
```

The source must contain exactly one uncommented explicit `\date{July 6, 2026}`
in the canonical English form. Parsing uses a fixed month map and UTC midnight,
with no wall clock or locale dependency. A consumer build recipe can use:

```sh
export SOURCE_DATE_EPOCH="$(just papers source-date --paper example)"
# Consumer recipe compiles into its declared build directory using that environment.
just papers normalize --paper example
just papers check --paper example
just papers check --paper example --reference papers/example.pdf
# Explicit refresh intent, after consumer scientific and build checks:
just papers normalize --paper example --output papers/example.pdf
```

Literal-path commands are also supported: `papers source-date SOURCE.tex`,
`papers check BUILT.pdf --min-pages 1 --require-text TITLE --reference RETAINED.pdf`,
and `papers normalize BUILT.pdf --tex SOURCE.tex --identity STABLE_NAME`.
CLI options override named policy. Normalization defaults to the input PDF;
`--output` explicitly selects a separate destination. Every input and candidate
is validated before replacement, and caught publication failures preserve prior
artifacts using the shared transaction contract. Exclude concurrent writers.

The supported normalization profile is Tectonic's uncompressed XMP timestamps,
document/instance UUIDs and 16-byte trailer IDs. Same-width edits preserve xref
offsets, and the complete candidate must retain page text and geometry. Any
Info creation/modification dates must already match the explicit UTC epoch,
including when stored in compressed objects. Missing fields, unsupported profiles,
encrypted or malformed PDFs fail without replacing output. Structural equivalence
compares per-page text, media/crop boxes, rotation and units; it allows different
native PDF bytes and does not certify rendering or scientific meaning.

Native discovery never installs host packages or modifies process environment:

```sh
just tectonic discover
just tectonic discover --format shell
just tectonic export --file target/tectonic.env
```

Linux/macOS resolve `freetype2`, `graphite2`, `icu-uc`, `libpng` and `zlib` with
pkg-config; Linux also requires `fontconfig` and `openssl`. Existing
`PKG_CONFIG_PATH`, `PKG_CONFIG_LIBDIR` and `PKG_CONFIG_SYSROOT_DIR` are retained;
repeat `--prefix PATH` to discover existing `lib/pkgconfig` and `share/pkgconfig`.
macOS also discovers existing Homebrew metadata and SDK shims. Windows requires
`TECTONIC_DEP_BACKEND=vcpkg`, `VCPKG_ROOT` and an existing
`installed/VCPKGRS_TRIPLET` directory (default `x64-windows-static-md`). Discovery
checks locations/resolution; consumers still verify library versions and ABI.
JSON is the default output on every platform; shell output is POSIX syntax.
Windows callers can apply JSON assignments with PowerShell:

```powershell
$native = just tectonic discover | ConvertFrom-Json
$native.PSObject.Properties | ForEach-Object { Set-Item "Env:$($_.Name)" $_.Value }
```

`tectonic export` appends validated UTF-8 LF assignments to the explicit file or
`GITHUB_ENV`. Missing prerequisites and probe errors return nonzero and publish
no assignments. Provision hosts explicitly in consumer setup/CI before discovery.
See the [API contracts](docs/api.md#paper-date-pdf-and-native-discovery-api) and
[consumer deletion maps](docs/shared-capability-migration.md#paper-and-raw-line-adoption).

### Performance evidence

These APIs and recipes require a published release newer than `0.1.2` containing
them. Merge the packaged `performance` recipe into the consumer justfile, then
compare existing Criterion trees without executing benchmarks:

```sh
just performance compare target/baseline/criterion target/current/criterion --output target/comparison.json
```

Both samples default to `new`, the statistic to `median`, and the unit to `ns`.
Use `--baseline-sample NAME`, `--current-sample NAME`, `--statistic mean`, or
`--unit` when the measurement harness requires them. The JSON retains both full
samples, including added and missing benchmarks. The CLI rejects comparisons
with no common benchmarks. It does not infer statistical significance.
Place `--output` outside both Criterion input roots, including path aliases.

Use Python to attach provenance captured by the consumer at measurement time:

```python
from pathlib import Path

from research_repo_tools.criterion import COMPARISON_SCHEMA, parse_comparison, render_comparison
from research_repo_tools.evidence import Evidence, Provenance, compare_provenance, publish_evidence

def retain_comparison(payload: bytes, baseline: Provenance, current: Provenance) -> None:
    comparison = parse_comparison(payload)
    compatibility = compare_provenance(
        baseline, current, fields=("harness_sha256", "context.cpu", "context.rustc"),
    )
    if not compatibility.compatible:
        raise ValueError(f"Measurement context differs or is unknown: {compatibility.differences}")
    retained = Evidence(payload, COMPARISON_SCHEMA, (("baseline", baseline), ("current", current)))
    publish_evidence(
        retained, Path("evidence/comparison.json"), Path("evidence/manifest.json"),
        reports={Path("PERFORMANCE.md"): render_comparison(comparison).encode("utf-8")},
        immutable=(Path("evidence/comparison.json"), Path("evidence/manifest.json")),
    )
```

Choose compatibility fields and scientific acceptance rules in the consumer.
Provenance records the full source commit, optional exact source and harness
digests, and explicit context. Unknown values fail a requested compatibility
check. Render or verify retained evidence independently of measurement:

```sh
just performance verify evidence/comparison.json evidence/manifest.json
just performance render evidence/comparison.json evidence/manifest.json --output PERFORMANCE.md
```

`verify` checks envelope structure and the exact payload digest; consumers still
validate their own payload schemas. `render` additionally parses the shared
comparison schema. Domain reports can keep their existing renderer and use the
shared comparison results and publication transaction.

For published assets, use `just performance fetch HTTPS_URL ASSET --sha256 DIGEST`
with an independently recorded digest, then
`just performance extract ASSET ABSENT_DIRECTORY --sha256 DIGEST`.
The extraction parent must exist. See the [performance API](https://github.com/acgetchell/research-repo-tools/blob/main/docs/performance-api.md)
for limits and [retained-evidence migration](https://github.com/acgetchell/research-repo-tools/blob/main/docs/performance-migration.md) before
replacing consumer helpers or changing artifact formats.

### Document publication

Publish a selected timing table and optional SVG into an existing marked section:

```sh
just performance publish publication.toml --preview
just performance publish publication.toml
just performance publish publication.toml --check
```

Preview prints validated candidate diffs without writing. Check exits `1` for
stale or invalid outputs and `0` when current. Default mode updates the document
and figure in one rollback-capable transaction. All modes verify the retained
payload hash, selected measurements, configured provenance, and release/report
references. No measurements or network retrieval run. This capability requires
a published release newer than `0.1.2` containing it.

Place exactly one ordered `<!-- PERFORMANCE:BEGIN -->` and
`<!-- PERFORMANCE:END -->` pair in the document. Configure shared comparison
evidence and explicit benchmark selection in `publication.toml`:

```toml
schema = 1
document = "README.md"
payload = "evidence/comparison.json"
manifest = "evidence/manifest.json"
begin = "<!-- PERFORMANCE:BEGIN -->"
end = "<!-- PERFORMANCE:END -->"
unit = "ns"
svg = "figures/timings.svg" # Omit for table-only publication.
rows = [{benchmark = "step/2", label = "Two-dimensional step"}]
links = [
    {label = "Report and measurement context", path = "PERFORMANCE.md"},
    {label = "Retained measurements", path = "evidence/comparison.json"},
    {label = "Provenance", path = "evidence/manifest.json"},
]

[provenance.baseline]
revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" # Replace with expected commit.
release = "v1.0.0"

[provenance.current]
revision = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" # Replace with expected commit.
release = "v1.1.0"

[[references]]
path = "Cargo.toml"
pattern = '^version = "(?P<value>[^"]+)"\r?$'
source = "version"

[[references]]
path = "PERFORMANCE.md"
pattern = '^Baseline: (?P<value>[^\r\n]+)'
source = "previous-tag"

[[references]]
path = "PERFORMANCE.md"
pattern = '^Current: (?P<value>[^\r\n]+)'
source = "tag"
```

Use the consumer's actual package/report paths and precise capture patterns.
This example expects `version = "1.1.0"` and report lines `Baseline: v1.0.0`
and `Current: v1.1.0`. Record each source's release in its evidence
`Provenance.context`, for example `(("release", "v1.1.0"),)`. Maintain expected
revisions independently of the files being checked. Add `source-sha256`,
`harness-sha256`, or a `context` table to a provenance table when those identities
are required. Unknown or mismatched values fail.

Rows follow configuration order; each must have both baseline and current data.
The unit must match the recorded unit. Optional `baseline-label` and
`current-label` change displayed headings without changing release expectations.
The dependency-free SVG shows point ratios; scientific commentary stays in the
consumer. Bytes outside the marker interior, including historical links and
line endings, are preserved.

Links default to document-relative paths. To publish tagged GitHub links, set
top-level `repository = "owner/repository"`. The current release tag must exist
locally and contain the exact retained, referenced, linked, and generated figure
bytes. To assert that a measured source itself is a tagged commit, also set
`verify-tag = true` in that source's provenance table. These are read-only local
Git checks; fetching release assets remains a separate explicit operation.

Historical CSV/JSON formats can use declarative conversion into shared companions
at new paths. See the [publication API](https://github.com/acgetchell/research-repo-tools/blob/main/docs/publication-api.md) and
[migration guide](https://github.com/acgetchell/research-repo-tools/blob/main/docs/publication-migration.md). Scientific prose remains consumer-owned.

### Configured benchmark workflows

Configured workflows around the retained-evidence APIs are available from v0.1.4.
Pin an exact published version containing the capabilities your consumer uses.
Start with the packaged `benchmark.toml`, `examples.toml`, and
`performance-report.toml` templates, selecting your actual workloads and paths.
For example, a measurement configuration contains:

```toml
schema = 1
command = ["cargo", "bench", "--locked", "--bench", "timings"]
sources = ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "src/**/*.rs", "benches/timings.rs"]
harness = ["benches/timings.rs"]
sample = "new"
statistic = "median"
unit = "ns"
compatible = ["context.os", "context.architecture", "context.cpu"]

[probes]
rustc = ["rustc", "--version", "--verbose"]

[dependencies]
criterion = "Cargo.lock"
```

Use the template's `just performance` wrapper. These are alternative operations:

```sh
just performance compare target/criterion target/criterion --baseline-sample last --format markdown
just performance measure benchmark.toml --mode current-vs-latest --allow-git-mutations --payload target/local.json --manifest target/local.evidence.json
just performance measure benchmark.toml v1.2.0 v1.1.0 --allow-git-mutations --payload target/release.json --manifest target/release.evidence.json
just performance assets --repository owner/project --asset-template 'project-{tag}-baseline.tar.gz' \
  --payload target/releases.json --manifest target/releases.evidence.json
```

Measurement explicitly permits temporary Git worktrees and runs trusted consumer
code. It needs existing local tags and a verified benchmark toolchain. Use a
consumer recipe with `toolchain run -- research-repo-tools performance measure`
when tools live under managed paths. Omitting tags infers the package release:
prospective releases use working files, and published versions use their tagged
source and predecessor. Publication chronology is the default; `--order version`
selects numeric order. Asset comparison performs no measurement. Add
`--legacy-configuration legacy-baseline.toml` only for declared historical layouts.

A report configuration contains:

```toml
schema = 1
current = "docs/PERFORMANCE.md"
archive = "docs/archive/performance"
title = "Benchmark timings"
prose-file = "docs/performance-interpretation.md"
```

After measurement, promote, rerender offline, then check or publish the configured
README section:

```sh
just performance promote performance-report.toml --payload target/release.json --manifest target/release.evidence.json
just performance promote performance-report.toml --check
just performance publish performance-publication.toml --check
just performance publish performance-publication.toml
```

Promotion retains full JSON evidence and CSV, archives the prior report, rebases
evidence links, and refreshes the archive index. `--preview` shows planned diffs.
Future-release README preparation requires explicit `tag-policy="prepare"`,
`repository`, both current inventories and matching working-tree fingerprints.
Any existing tag must still contain exact referenced and generated bytes.
Historical conversion writes new paths and preserves original files and hashes;
see [migration](https://github.com/acgetchell/research-repo-tools/blob/main/docs/performance-migration.md) and the
[configuration/API contracts](https://github.com/acgetchell/research-repo-tools/blob/main/docs/workflow-api.md).

### Common harness and complete runs

The schema-2 measurement/report contracts and host capture are intended for
v0.1.8; adopt an exact published release containing them before deleting working
consumer tooling. Use the packaged `common-benchmark.toml`,
`complete-report.toml`, and `profiling.toml` as scaffolds, editing their inventories
and commands to match the consumer. See the [contracts](https://github.com/acgetchell/research-repo-tools/blob/main/docs/complete-run-api.md)
and [adoption map](https://github.com/acgetchell/research-repo-tools/blob/main/docs/performance-migration.md#common-harness-and-host-adoption).

Schema-2 measurements capture the selected current harness once, including
declared lockfiles and toolchain files, and apply it to both isolated revisions.
Every phase declares a command, an independent preflight gate, its expected
Criterion `full_id` inventory, and optional environment overrides. Both gates
must pass before either phase is timed. Configure 100 samples, both mean and
median, and 0.95 confidence for that completeness policy; smaller or different
policies must be explicitly declared. Commands must actually configure Criterion
to emit those samples and intervals. The shared reader verifies the output.

Named report series select rows from a recorded phase. For example, a reference
measured with the baseline is declared with `phase = "baseline"` even when
displayed alongside the current library. Its provenance always names that phase.
All measured cases, original raw Criterion bytes and required statistics remain
in retained evidence, including cases omitted from report tables.

After configuring the templates, run these steps in order:

```sh
just performance measure common-benchmark.toml v1.2.0 v1.1.0 --allow-git-mutations \
  --payload target/complete-run.json --manifest target/complete-evidence.json
just performance promote complete-report.toml \
  --payload target/complete-run.json --manifest target/complete-evidence.json
just performance promote complete-report.toml --check
```

Keep the configured archive outside scratch directories such as `target`.
Promotion writes immutable content-derived run directories, a validated latest
pointer, an index and the current report together. Distinct measurements of the
same release pair get distinct identities; identical retries reuse the existing
run. Same-label local comparisons can be retained. Omitting both input paths
rerenders the validated latest run after scratch cleanup. Explicit missing or
partial inputs fail; they never select a different run as a fallback.

Dimension plots remain consumer renderers over the retained run API. The shared
tables support arbitrary named series; axis semantics, workload coordinates,
scientific interpretation and release eligibility remain consumer decisions.

### Host and profiling metadata

These commands write explicit UTF-8 JSON outputs under the consumer root:

```sh
just performance host --output target/host.json
just performance profile profiling.toml --output profiling-results/environment.json
```

Host metadata includes available CPU model, physical cores, logical threads,
total memory in bytes, OS and architecture. Profiling adds bounded configured
version probes, explicit context and structured native Cargo/Rust TOML
declarations. Unavailable observations are `null`; declarations are distinct
from measured tool versions and resolved build settings. The default profiling
probes capture Cargo and verbose rustc output in the selected root. Use the
same managed-tool environment as the benchmark. Source provenance callers use
`measurement.capture_provenance`, which includes this host record alongside
source/harness fingerprints and tool/dependency identity. To include source
identity directly in a profiling artifact, set `measurement = "benchmark.toml"`
and the explicit `release = "v1.2.0"` at the top level of its configuration.

Keep compatibility thresholds, warning text, historical baseline parsing and
profiling-mode selection in the consumer. Read retained metadata directly when
interpreting historical evidence; do not attach observations from today's host.

### Consumer glue and release jobs

The template's `just files` and `just validate` recipes replace discovery and
example-runner scripts. Keep scientific output expectations in configuration:

```toml
schema = 1
[[checks]]
name = "simulation"
command = ["target/debug/examples/simulation"]
expect = ["Samples:", "Mean:"]
timeout = 300
```

Compile the example first, then use `just validate examples.toml`. Explicit
executable paths discover Windows extensions. File-based validators can use
`just files run --include '*.md' --exclude CHANGELOG.md -- rumdl check`;
the shared runner selects tracked/nonignored files and batches literal arguments.

After setup/cache restoration in GitHub Actions, `just tools-export` verifies
managed tools and appends checked settings to `GITHUB_ENV`. Cache keys should
include OS/architecture and exact tool, Rust and lockfile identities. Generic
`ci export NAME ...` supports additional declared single-line environment values.

Release benchmarks use three jobs: validate a mutable draft, measure a checked-out
tag with read-only permissions, then attach/publish inert bytes in a separate
writer job. The measurement recipe invokes `performance baseline benchmark.toml
v1.2.0 project-v1.2.0-baseline.tar.gz`; it requires HEAD and configured source bytes
to match the tag. Writer jobs install the exact published package and use the
[captured release target](#trusted-release-target-handoff) to attach and publish
without checking out benchmark code. Identical completed attachment retries are
accepted; conflicting bytes or incomplete provider metadata fail.

### Trusted release target handoff

Use an exact published package version containing the post-v0.1.8 target API.
This working-tree capability is not available in v0.1.8; record its containing
release when published before adopting it downstream. Install that pin in trusted
preflight and writer jobs. With the installed console command on PATH, preflight
captures the expected workflow commit and a title-equals-tag assertion:

```sh
research-repo-tools --root "$RUNNER_TEMP" performance release-draft \
  "$GITHUB_REPOSITORY" "$RELEASE_TAG" --commit "$GITHUB_SHA" \
  --expected-title "$RELEASE_TAG" --output "$RUNNER_TEMP/release-target.json"
```

Omitting `--output` prints only target JSON on stdout. Pass this target through a
consumer-controlled trusted job output or artifact, separately from benchmark
output. The benchmark job retains read-only permissions and owns its correctness
checks and archive format. The final writer reads the original trusted target and
inert asset, then explicitly requests publication:

```sh
research-repo-tools --root "$RUNNER_TEMP" performance release-upload \
  "$GITHUB_REPOSITORY" "$RELEASE_TAG" "$RELEASE_ASSET" \
  --target "$RUNNER_TEMP/release-target.json" --publish
```

Without `--publish`, attachment and verification leave the draft unpublished.
Without `--target`, the existing upload command captures a fresh target inside
that invocation and cannot bind an earlier benchmark run. The old `release-draft`
command without `--commit` still checks draft state and emits no target. Output
and title assertions require `--commit`. These commands require authenticated
`gh`; consumers own token permissions, workflow/ref policy and fresh-preflight
run-attempt checks. JSON parsing alone does not authenticate the handoff.

The same supported Python API can run in a trusted job containing only inert files:

```python
from pathlib import Path
from research_repo_tools.release_assets import (
    parse_release_target, preflight_release_target, publish_release_asset,
    serialize_release_target,
)

root = Path("/trusted/job")  # Choose the native job directory on each platform.
target = preflight_release_target(root, "example/project", "v1.2.3",
                                 "a" * 40, expected_title="v1.2.3")
(root / "release-target.json").write_bytes(serialize_release_target(target))
# After the consumer's trusted handoff and benchmark gates:
captured = parse_release_target((root / "release-target.json").read_bytes())
publish_release_asset(root, captured.repository, captured.tag,
                      root / "baseline.tar.gz", target=captured, publish=True)
```

An invalid or unavailable publication response reports an unknown outcome.
Inspect the captured release ID and attached asset before retrying; no rollback
or automatic publication retry occurs. The [API contract](docs/workflow-api.md#release-assets)
details validation, retry guarantees and the limits of client-side rechecks.
The [la-stack deletion map](docs/performance-migration.md#captured-release-target-adoption)
keeps downstream migration tied to an exact published package and integration checks.

Once no local executable modules remain, remove the consumer's build backend,
console scripts and self-referencing notebook extra. Set `[tool.uv] package=false`,
retain exact shared pins in tooling/notebook dependency groups, refresh the lock,
and remove local wheel/entry-point tests. Rust/scientific and configuration tests
remain. Declare `release.tag-policy="canonical-stable"` where `vX.Y.Z` is required;
use `just release-update TAG PREVIOUS DATE` for offline release preparation.
Version-independent documentation examples avoid release-update callbacks.

### Clippy SARIF helpers

Declare the exact converter versions in `pyproject.toml`. Keep the consumer's
Rust compiler pinned in `rust-toolchain.toml`, with the `clippy` component:

```toml
[tool.research-repo-tools.toolchain.cargo]
clippy-sarif = "0.8.0"
sarif-fmt = "0.8.0"
```

Use `just setup` to install them and `just tools-check` to verify them. Keep
the Cargo invocation and SARIF upload workflow in the consumer. A Bash recipe can
preserve failure from every pipeline stage with `pipefail`:

```just
clippy-sarif:
    #!/usr/bin/env bash
    set -euo pipefail
    tool() { uv run --locked --no-sync --no-python-downloads research-repo-tools toolchain run -- "$@"; }
    tool cargo clippy --message-format=json | tool clippy-sarif | tee results.sarif | tool sarif-fmt
```

`just update-cargo-tools` upgrades only declared tools. Both helpers use the shared
exact, locked Cargo installer and checked execution. See the
[platform contract](https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md#additional-cargo-tools-and-native-prerequisites).

### Calling from Python

A consumer that needs a Python wrapper can invoke the same CLI contract without
a subprocess. For a script in the consumer's `scripts/` directory:

```python
from pathlib import Path

from research_repo_tools import __version__
from research_repo_tools.cli import main

root = Path(__file__).resolve().parents[1]
print(f"Using research-repo-tools {__version__}")
raise SystemExit(main(["--root", str(root), "release", "check"]))
```

The process and file APIs below require a release newer than `0.1.2` that includes
them. Pin the published version before removing local consumer helpers.
They leave command policy and scientific decisions in the consumer:

```python
import subprocess
import sys
from pathlib import Path

from research_repo_tools.files import replace_many
from research_repo_tools.process import format_exception_diagnostics, run_command, run_git_bytes

root = Path(__file__).resolve().parents[1]
try:
    result = run_command(sys.executable, ["--version"], cwd=root, timeout=30)
    payload = (root / "results.json").read_bytes()
    # Git receives the original bytes and applies its own configured filters.
    digest = run_git_bytes(
        ["--no-pager", "hash-object", "--path=results.json", "--stdin"],
        cwd=root, input=payload,
    ).stdout.strip()
    replace_many({
        root / "reports/results.json": payload,
        root / "reports/results.hash": digest + b"\n",
    })
except (OSError, subprocess.SubprocessError, ExceptionGroup) as error:
    raise SystemExit(format_exception_diagnostics(error)) from error
```

Text execution uses explicit UTF-8 by default; byte execution preserves LF, CRLF,
and binary data. Publication stages every file first and rolls back caught
replacement failures. See [supported interfaces][api] for lookup behavior, typed
results/errors, recovery backups, concurrency limits, and API stability.

### Consumer integration tests

The notebook fixture and Just inspection APIs target **v0.1.8**. Adopt an exact
published release containing them before deleting consumer helpers. They require
no pytest plugin; keep scientific assertions and repository-specific gate policy
in the consumer tests.

Run notebook tests in the consumer's already synchronized locked environment,
with the notebook extra and analysis dependencies installed. A pytest test can
borrow that interpreter and copy only its selected inputs:

```python
import sys
from pathlib import Path

from research_repo_tools.notebook_testing import isolated_project

def test_analysis(tmp_path):
    with isolated_project(
        Path(__file__).resolve().parents[2],
        [Path("notebooks/analysis.ipynb"), Path("data/example.csv")],
        parent=tmp_path,
        environment=Path(sys.prefix),
    ) as project:
        # Prepare domain inputs and assertions inside project.root.
        result = project.execute(
            Path("notebooks/analysis.ipynb"),
            env={"ANALYSIS_OUTPUT_DIR": str(project.root / "figures")},
        )
        assert result.returncode == 0, result.report
        assert result.report["failed_cell"] is None
        assert result.notebook_path.is_file()
```

The fixture copies the manifest, lockfile and Python selector without rewriting
them, excludes native toolchain setup, and removes only its temporary workspace
on context exit. Returned paths live until that exit. Cell failures and timeouts
return a failed report. The helper does not synchronize dependencies or certify
that installed packages match the lock; run the consumer's normal setup first.

Inspect Just's native metadata and dry-run text using the installed package's
Just version:

```python
from pathlib import Path

from research_repo_tools.just_inspect import dry_run, inspect_justfile

root = Path.cwd()
recipes = inspect_justfile(root).recipes
dependencies = {item["recipe"] for item in recipes["ci"]["dependencies"]}
assert "test-rust" in dependencies  # This assertion is consumer policy.
preview = dry_run(root, "release-notes", ["v1.2.3"])
assert "changelog notes" in preview.stderr
```

Dry-run preserves native stdout/stderr and argument boundaries. It does not run
recipe bodies, but Just still evaluates configuration and expressions; use
trusted Justfiles. See the [integration-test API contract][api] for overrides,
errors, path boundaries, environment handling, and migration details.

### Preparing a structured release

The release API requires a published version newer than `0.1.2` containing it.
It replaces wrapper-owned discovery, staging, CLI-output parsing, and rollback:

```python
from pathlib import Path

from research_repo_tools.releases import apply_release, plan_release

plan = plan_release(
    Path.cwd(), "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-20",
)
for edit in plan.edits:
    print(edit.path)  # Inspect edit.before and edit.after for the exact byte diff.
# Omit apply_release for a fully validated preview.
result = apply_release(plan)
```

Declare required files, fixed metadata, and selected active references in
[release configuration][release]. Add a small Python adapter when validation
needs consumer-owned evidence rules. See the
[worked MCMC-style migration](https://github.com/acgetchell/research-repo-tools/blob/main/docs/release-policy-migration.md)
and [typed API contract][api]. Historical evidence stays excluded from updates.

## Templates and optional settings

Standard changelog archiving and release commands need no configuration file.
For generated GitHub links, specify the owner and repository in existing project
metadata:

```toml
[tool.research-repo-tools.changelog]
owner = "example"
repository = "consumer"
# formatter = "rumdl.toml"  # optional external formatting

[tool.research-repo-tools.deps.tools]
rumdl_version = "rumdl"
uv_version = "uv"

[tool.research-repo-tools.semgrep]
config = "semgrep.yaml"
fixtures = "tests/semgrep"
```

`--config PATH` reads unprefixed tables from a separate TOML file when needed.
Paths are relative to the consumer root, which defaults to the configuration
directory; `--root PATH` overrides that root. Explicit executable paths such as
`deps.uv = "./bin/uv"` use the same root; bare names use `PATH`.
The selected uv executable is used for dependency resolution, Python pin updates,
and uv version checks.
Unknown settings fail. There are no repository profiles.

`deps.tools` values name packages installed separately with Cargo, as listed by
`cargo install --list`. The special `uv` entry reads the selected uv executable.
Just is supplied by the shared package's exact `rust-just` dependency. Do not
introduce a competing Just variable or independently installed Cargo Just pin.

```sh
uv run --locked research-repo-tools templates CHANGELOG.md
uv run --locked research-repo-tools templates cliff.toml --owner example --repository consumer
uv run --locked research-repo-tools templates justfile
uv run --locked research-repo-tools templates research-repo-tools.toml
```

Templates print to stdout. `--output PATH` creates a file and refuses to replace
existing content. Generation uses the packaged git-cliff template by default,
so consumers need not maintain a copy.

Cargo pins accept canonical prereleases and build metadata, such as
`1.2.3-rc.1+build.5`; uv pins require stable `X.Y.Z`. All pin changes are validated
before replacement. Comments, line endings, permissions, and symlink targets
are preserved. `deps update-python` updates exact development requirements
while retaining ranges, markers, extras, and other unmanaged constraints.
Before uv changes the manifest and lockfile, the updater saves both originals.
A failed update restores them; if restoration also fails, the command reports
the retained backup paths for recovery.

See [changelog behavior][changelog], [release behavior][release], and
[scope and adoption][migration] for detailed contracts and migration guidance.

## Contributing

To develop or fix this package, see [CONTRIBUTING.md][contributing] for environment
setup, coding conventions, maintainer recipes, testing, and release preparation.

## License

BSD-3-Clause. See [LICENSE][license].

[publishing]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RELEASING.md
[contributing]: https://github.com/acgetchell/research-repo-tools/blob/main/CONTRIBUTING.md
[api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/api.md
[cargo-examples-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/cargo-examples-api.md
[cargo-examples-migration]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/cargo-examples-migration.md
[toolchain]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md
[dependabot]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/AUTOMATING_DEPENDABOT.md
[changelog]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/GENERATING_CHANGELOGS.md
[release]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_RELEASE_METADATA.md
[migration]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md
[update-adoption]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md#dependency-and-tool-update-adoption
[license]: https://github.com/acgetchell/research-repo-tools/blob/main/LICENSE
[just-template]: https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/justfile
[pypi-badge]: https://badgen.net/pypi/v/research-repo-tools
[pypi]: https://pypi.org/project/research-repo-tools/
[license-badge]: https://badgen.net/github/license/acgetchell/research-repo-tools
[ci-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/ci.yml/badge.svg?branch=main
[ci-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/ci.yml
[codeql-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/codeql.yml/badge.svg?branch=main
[codeql-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/codeql.yml
[zizmor-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/zizmor.yml/badge.svg?branch=main
[zizmor-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/zizmor.yml
[codecov-badge]: https://codecov.io/gh/acgetchell/research-repo-tools/branch/main/graph/badge.svg
[codecov-dashboard]: https://app.codecov.io/gh/acgetchell/research-repo-tools
[audit-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/audit.yml/badge.svg?branch=main
[audit-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/audit.yml
