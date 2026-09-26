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
| Changelog | `changelog archive`, `check`, `generate`, `normalize`, `notes`, `tag` | Root `CHANGELOG.md`; completed minor series in `docs/archives/changelog/` |
| CI environment | `ci export` | Validate all single-line values before appending a GitHub environment command file |
| Coverage | `coverage report` | Cobertura summaries with deduplicated source lines |
| Dependencies | `deps check-uv`, `update-python`, `update-tools`, `update-uv` | Exact development pins; canonical Cargo SemVer; stable uv pins |
| Documentation | `docs check-lines` | UTF-8 Markdown line checks with table exemptions |
| File selection | `files list`, `run` | Tracked/nonignored inputs, exclusions and portable argument batching |
| Notebooks | `notebooks advise`, `check`, `clear`, `execute`, `group`, `inspect`, `lint`, `sync` | Read-only review, optional locked environment, native Ruff/ty checks, and execution reports |
| Performance | `performance assets`, `baseline`, `compare`, `convert`, `export`, `extract`, `fetch`, `measure`, `promote`, `publish`, `release-draft`, `release-upload`, `render`, `verify` | Complete configured measurement, retained evidence, release assets and publication |
| Release metadata | `release check`, `release update` | Infer metadata, apply declared policies, and validate complete release plans |
| Review | `review branch`, `review uncommitted` | Opt-in CodeRabbit review with verified default base and streamed findings |
| Security | `security osv`, `secrets` | Managed OSV/Gitleaks, explicit inputs, full history, redacted native reports |
| Semgrep | `semgrep check-fixtures`, `scan` | Validate consumer rules, explicit inventory, reports and fixture expectations |
| Setup | `setup` | Require uv; install user Just and declared tools; sync the locked environment |
| Templates | `templates NAME` | Shared changelog, git-cliff, just, TOML, and rumdl resources |
| Toolchain | `toolchain adopt`, `check`, `clean`, `export`, `python-check`, `run`, `sync`, `upgrade` | Exact declarations; managed installations and cleanup; verified execution and checked CI export |
| Validation | `validation cargo-metadata`, `require`, `run` | Native package preflight, executable checks and configured example output assertions |
| Workflow security | `zizmor check` | One declared scanner/persona, token discovery, explicit offline or required-online audits |

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
| `just files COMMAND...` | List selected tracked/nonignored files or run a command over them |
| `just help` | List available commands and arguments in lexicographic order |
| `just help-workflows` | Alias for `help` |
| `just notebook-advise FILE... [--strict]` | Report configured review warnings, optionally failing on them |
| `just notebook-check FILE...` | Validate notebook structure, cell IDs, and output policy |
| `just notebook-clear FILE...` | Deliberately clear generated notebook state |
| `just notebook-execute FILE...` | Execute selected notebooks and write results and reports |
| `just notebook-inspect FILE... [--json] [--no-preview]` | Inventory cells and repair problems without generating IDs or loading Jupyter |
| `just notebook-lint FILE...` | Check structure, output policy, Python syntax, Ruff rules/formatting, and ty types |
| `just notebook-sync` | Synchronize locked notebook dependencies and the project kernel |
| `just performance COMMAND...` | Compare Criterion samples, handle assets, and verify, render, or publish retained evidence |
| `just python-check` | Apply full configured Ruff/ty checks to all tracked and nonignored Python, including fixtures |
| `just release-check` | Check consumer release metadata |
| `just release-first TAG DATE` | Prepare a first release after checking published stable history |
| `just release-notes TAG` | Print release notes from the root changelog or an archive |
| `just release-update TAG PREVIOUS DATE` | Prepare metadata with an explicit predecessor and date |
| `just review [base]` | Review branch and local changes; default to verified `origin/main` |
| `just review-uncommitted` | Review staged, unstaged, and non-ignored untracked changes |
| `just security-osv LOCKFILE...` | Audit explicitly selected uv.lock/Cargo.lock files |
| `just security-secrets [ARGS...]` | Scan full reachable history and current tracked/nonignored files |
| `just semgrep-check` | Validate the consumer's Semgrep rules and fixtures |
| `just setup` | Install and verify declared tools, then synchronize the Python environment |
| `just shared-python-plan VERSION` | Preview an opt-in shared-package/Python migration outside the old environment |
| `just shared-python-update VERSION` | Apply the migration and recreate the locked environment and notebook kernel |
| `just tag TAG` | Forward to `tag-release` |
| `just tag-force TAG` | Explicitly replace an existing local tag |
| `just tag-release TAG` | Create a local annotated tag from validated release notes |
| `just tools-check` | Check installed tools and versions without installing them |
| `just tools-export` | Verify tools and append their environment to `GITHUB_ENV` |
| `just update` | Upgrade tools, then Cargo and Python dependencies and the development environment |
| `just update-cargo-dependencies` | Upgrade root Cargo requirements (including incompatible releases) and lock resolution; skip projects without a root Cargo.toml |
| `just update-cargo-tools` | Upgrade declared Cargo tools and release binaries; publish verified TOML pins |
| `just update-dependencies` | Run the Cargo and Python dependency workflows |
| `just update-python-dependencies` | Update direct dev pins, upgrade the full Python lock, and synchronize dev |
| `just update-python-deps` | Alias for `update-python-dependencies` |
| `just update-tools` | Upgrade uv and managed tools, then run setup |
| `just update-uv` | Upgrade uv through its installation owner and reconcile its pin |
| `just validate CONFIGURATION [NAME...]` | Check configured example outputs |
| `just zizmor-check [ARGS...]` | Run local workflow audits; accept `--offline`, `--require-online`, and `--format sarif` |

To preview a prospective release, run
`just changelog-preview --tag v1.2.3 --date YYYY-MM-DD`.

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

The [shared GitHub workflow][dependabot] can approve allowlisted
Python or Rust dependency patch updates and enable native auto-merge after the
repository's required reviews and checks are satisfied. Each repository owns
its dependency/file policy, Actions approval setting, and merge rules. Consumers
pin the reusable workflow to a reviewed Git commit; no Python package upgrade is
required. `research-repo-tools` is the first pilot consumer.

This workflow supersedes CodeRabbit approval polling and the
`CODERABBIT_REVIEW_TOKEN` personal access token requirement. It uses
`GITHUB_TOKEN` for approval and auto-merge. Any required CodeRabbit status
remains an independent merge gate.

### Workflow security and complete Python validation

These additions target v0.1.6; consumers should adopt `research-repo-tools==0.1.6`
after publication. Merge the packaged `python-validation.toml` policy into your
pyproject and retain the full existing Ruff configuration. It enables missing
annotations (ANN001/002/003/201/202/204/205/206), TC, and UP037. The `python-check`
recipe discovers `*.py` and `*.pyi` throughout the repository, including support
code, tests, and negative Semgrep fixtures. Use exact per-file/rule exceptions
for intentional violations. Extend `check` with existing Rust/domain tests and
native notebook linting; keep `semgrep-check` in the canonical local and CI gate.

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
just release-check
just release-notes v1.2.3
just tools-check
just update
```

`just update` upgrades uv first. Standalone uv installations use
`uv self update`; Homebrew installations use `brew upgrade uv`. Other installation
owners must update uv themselves. The updater records the resulting stable version
in `pyproject.toml`. This changes the shared
user installation; other projects with different exact uv pins must be reconciled
before their commands will run. Review the generated changes before committing.
`just setup` installs declared versions without upgrading them.

The underlying CLI remains available for integrations and custom recipes; see
[supported interfaces][api].

### Shared Python adoption

Opt in once in the consumer manifest:

```toml
[tool.research-repo-tools.toolchain]
inherit-python = true
```

The installed package owns its runtime requirement from distribution metadata
and its selected development minor (currently Python 3.14). Exact package pins,
`.python-version`, and dependency-only `project.requires-python` are checked
mirrors. Public Python packages retain their own runtime compatibility; only
the groups containing the shared tool get uv Python constraints. Ruff and ty
infer targets from project metadata. Consumer lint rules, fixture exceptions,
notebook policy, and deliberate lower targets remain consumer decisions.

After the target version is published, use the packaged standalone bootstrap:

```sh
just shared-python-plan 0.1.7
just shared-python-update 0.1.7
just python-check
```

These recipes run the exact target package outside the old project environment,
so an obsolete selector, lock or environment cannot prevent startup. Preview
resolves and installs a temporary candidate; it may download Python/packages
and populate caches, but leaves consumer files and `.venv` unchanged. Apply
updates all direct shared-package pins (including extras), Python mirrors,
`uv.lock`, the environment, and the configured notebook group's project kernel.
Resolution and candidate installation must succeed before publication.

A caught failure restores original files and the prior environment. Recovery
errors identify the retained backup; do not delete it before recovering. Close
processes using `.venv` on Windows before applying. This is not a crash-atomic
or concurrent-writer transaction. Include every required source in the tracked
or nonignored inventory; external local path dependencies need a separately
reviewed migration. `just python-check` detects mirror drift without repairing
it or downloading an interpreter. Routine checks do not advance versions.

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
The shared scan checks native errors and coverage, disables inline `nosem`, and
writes native JSON/SARIF reports with original source locations. Rust fences in
Markdown and line/block rustdoc comments preserve source line numbers and hidden
`# ` lines. Macro-generated docs and `#[doc = ...]` attributes are outside this
adapter's scope. `semgrep check-fixtures --rust-docs` adapts annotated Markdown
fixtures to the existing shared assertion checker; count-based expectations use
a separate fixture gate. Findings and fixture mismatches remain blocking.

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
just_version = "just"
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

Read-only review commands require the release containing #39 and #40, targeted
for `0.1.5`; they are absent from `0.1.4`. Merge the new recipes from the packaged
template when adopting that release. Inspection uses only the base installation
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
`os.system` commands in plain Python cells. Diagnostics retain notebook paths,
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

The coordinated v0.1.4 work adds complete workflows around the retained-evidence
APIs. Pin that version once published; source availability is not a release.
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
to match the tag. Writer jobs install the exact published package and run
`performance release-draft owner/project v1.2.0` or `performance release-upload
owner/project v1.2.0 project-v1.2.0-baseline.tar.gz --publish` without checking out
benchmark code. Identical attachment retries are accepted; different bytes fail.

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
just_version = "just"
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
The Cargo package is named `just`; the Python dependency that supplies the bundled
executable is named `rust-just`.

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
