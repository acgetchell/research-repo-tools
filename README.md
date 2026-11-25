# research-repo-tools

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23744167.svg)](https://doi.org/10.5281/zenodo.23744167)
[![PyPI version][pypi-badge]][pypi]
[![License][license-badge]][license]
[![CI][ci-badge]][ci-workflow]
[![CodeQL][codeql-badge]][codeql-workflow]
[![zizmor][zizmor-badge]][zizmor-workflow]
[![Codecov][codecov-badge]][codecov-dashboard]
[![Audit dependencies][audit-badge]][audit-workflow]

Shared setup, checks, performance evidence, and release tooling for research software repositories.

## Contents

- [Why use research-repo-tools?](#why-use-research-repo-tools)
- [Quick start](#quick-start)
- [Common workflows](#common-workflows)
- [Capability index](#capability-index)
- [Consumer examples](#consumer-examples)
- [Release availability](#release-availability) and [CHANGELOG.md][changelog-history]
- [Contributing](#contributing), [citation](#citation), and [license](#license)

## Why use research-repo-tools?

Teams maintaining scientific software can pin one set of Just recipes and Python
APIs across repositories. Contributors use the same commands to reproduce a tool
environment, check notebooks, or trace a release report back to its samples.
Your project declares its scientific workloads, native builds, correctness gates,
and publication approvals.

## Quick start

### Install with uv

Work in the root of the **consumer's Git repository**. Install Git and
[uv](https://docs.astral.sh/uv/getting-started/installation/) (0.12.10 or newer).
Recipes need POSIX `sh` on PATH. On Windows, add Git for Windows' `bin` directory,
typically `C:\Program Files\Git\bin`. Setup installs Just.

**1. Declare the package and interpreter.** If the repo has no `pyproject.toml`,
create one with `uv init --bare --python 3.15`. In an existing manifest, keep
the project's name and dependencies. Set `project.requires-python` to
require Python 3.15 or later, preserving any stricter bounds and exclusions.
Set `.python-version` to an interpreter version that satisfies that range.

Set `tool.uv.required-version` to `==` followed by the version number reported
by `uv --version`. Then add the published package to the tooling group.

```sh
uv add --group tooling --bounds exact --no-sync research-repo-tools
```

Include `{include-group = "tooling"}` in the existing `dev` group or create that
group. Setup installs the project's declared tools before syncing the
development environment. See [toolchain declarations][toolchain].

**2. Lock the dependencies and export both templates.** For a repo with an existing
Justfile or helper, omit the corresponding `--output` argument and merge the
printed template into the existing file. Run both exports from the consumer root.

```sh
uv lock
uv run --locked --only-group tooling research-repo-tools templates justfile --output justfile
uv run --locked --only-group tooling research-repo-tools templates python-bootstrap.py --output python-bootstrap.py
```

**3. Run setup.** Add any [Rust or optional tool declarations][toolchain] first.

```sh
uv run --locked --managed-python --only-group tooling research-repo-tools setup
```

Setup verifies the declarations, installs a pinned user-level Just and declared
tools, configures PATH and synchronizes the locked development environment.
Open a new terminal if PATH changed. Run `just help` and the [first workflow](#first-workflow)
from the consumer root. Keep the manifest, `uv.lock`, `.python-version`, Justfile
and `python-bootstrap.py` together in version control. See [installation and recovery][toolchain]
if setup reports a missing prerequisite.

### First workflow

List the installed recipes, then check the Markdown files in your repository.

```sh
just help
just files check-lines --limit 160 --include '*.md' --exclude CHANGELOG.md
```

If a line exceeds 160 characters, the checker reports its path, line number
and measured length and exits with status 1. It runs without optional dependencies.
You can [configure the selection and limit][line-checks] in your project.

## Common workflows

After setup, run these commands from the consumer root. Each recipe uses the
locked environment. Run `just help` to see the recipes in your project's Justfile.

| Command | Use |
| --- | --- |
| `just actions-check` | Check that configured GitHub Actions use consistent pins. [Wire the offline check][actions-wiring]. |
| `just changelog-preview --tag v1.2.3 --date YYYY-MM-DD` | Print the proposed root changelog for a tag and date. |
| `just check` | Run the checks configured by your project. |
| `just notebook-inspect notebooks/analysis.ipynb --no-preview` | Inspect notebook structure without executing cells or printing source. |
| `just review-uncommitted` | Request an opt-in CodeRabbit review of local changes. [Setup, results and paid consent][review-guide]. |
| `just tools-check` | Verify the installed tools against their declared versions. |
| `just update` | Upgrade declared tools and dependencies, then audit the updated locks. |

### Pin Actions from the same release

The v0.3.2 policy checks occurrences of each Action identity independently.
To check several subpaths against one reviewed release, use a tooling pin that
supports `[[consistency.releases]]`. See [release availability](#release-availability).
Add the exact Action identities to `.github/action-updates.toml` and replace
the example tag and commit with your reviewed values.

```toml
[actions]
"owner/repo/init" = "v1.2.3"
"owner/repo/analyze" = "v1.2.3"

[consistency]
actions = ["owner/repo/init", "owner/repo/analyze"]

[[consistency.releases]]
actions = ["owner/repo/init", "owner/repo/analyze"]
tag = "v1.2.3"
commit = "0123456789abcdef0123456789abcdef01234567"
```

Run `just actions-check` to check the pins offline. On update, the updater
resolves the upstream tag and compares its peeled commit before writing.

## Capability index

| Task | Guides |
| --- | --- |
| Set up or update a repository | [Installation and cleanup][toolchain] · [Dependency updates and Actions pins][updates-guide] · [Migration][migration] |
| Check code and workflows | [Python, Cargo, security scans and review][checks-guide] · [GitHub settings][github-settings] · [Dependabot approvals][dependabot] |
| Work with notebooks and papers | [Run notebooks][notebooks-guide] · [Build reproducible papers][papers-guide] |
| Measure performance | [Time a command][timing-guide] · [Record benchmark evidence][performance-guide] · [Reuse an acquisition][candidate-guide] · [Publish reports and figures][reports-guide] |
| Prepare a release | [Generate changelogs][changelog] · [Update metadata][release] · [Prepare a draft][draft-guide] |
| Verify and publish a release | [Consumer release guide][consumer-release-guide] · [Verify evidence][evidence-guide] · [Reuse CI checks][reuse-guide] · [Recover an unfinished release][recovery-guide] |

See the [command reference][commands] for the full recipe catalog and
[supported interfaces][api] for configuration, CLI behavior and Python APIs.

## Consumer examples

### Notebooks

Declare the `notebooks` extra at the same version as the tooling pin, add your
analysis dependencies and the Ruff/ty checkers, then refresh the lock.
Sync the kernel, check the source and execute the notebook.

```sh
just notebook-sync
just notebook-lint notebooks/analysis.ipynb
just notebook-execute notebooks/analysis.ipynb
```

The executed copy and JSON report appear under `target/notebooks`. A failed cell
returns a nonzero status and identifies the cell. [Notebook setup and execution][notebooks-guide]

### Performance

For a release with a published predecessor, configure the measurement, archive
and report paths in `tooling/performance-release.toml`. Prepare the release
pair, measure it, then preview and publish the README section.

```sh
just performance-prepare tooling/performance-release.toml
just performance-release tooling/performance-release.toml
just performance-readme tooling/performance-release.toml --preview
just performance-readme tooling/performance-release.toml
```

If only the README step fails, fix the presentation and retry it using retained
evidence. For a [first stable release][first-release], record current timings.
If published releases predate the workload, follow the [benchmark series
procedure][series-guide]. To revalidate the series after publication, pin v0.3.3
once available on [PyPI][pypi]. The series guide also covers
[reviewing a preparation PR with a large archived payload][raw-review].

### Releases

Print the release guide shipped with your pin and adapt it to your repository's
CI workflow, registry and approval environment.

```sh
just templates RELEASING.md
```

With publication configured, `just release-registry v1.2.3 --expect absent`
checks the proposed version before publication. After publication, run
`just release-verify v1.2.3`. The [consumer release guide][consumer-release-guide]
covers the intervening approval and publication steps.

## Release availability

Check [PyPI][pypi] for published versions. Take the Justfile and bootstrap helper
from the same release as your pin. For an existing repository, follow the
[migration guide][migration] and run your consumer checks. The [changelog][changelog-history]
records release history.

For a development rehearsal, [pin a reviewed commit][dev-pins]. Before collecting
release evidence, switch to the exact published dependency, refresh the lock and
verify it with `just release-dependencies`. [Dependency source verification][dependency-sources]

## Contributing

See [CONTRIBUTING.md][contributing] for package development, tests and maintainer
commands. [Supported interfaces][api] defines the consumer compatibility boundary.
Report vulnerabilities through the [security policy][security-policy].

## Citation

If you use research-repo-tools in your work, cite the software using
[CITATION.cff](https://github.com/acgetchell/research-repo-tools/blob/main/CITATION.cff).
Include the version used so others can identify the same release. The
[Zenodo concept DOI][doi] covers all versions; use the version-specific DOI from
the corresponding Zenodo record when citing an exact release.

## License

BSD-3-Clause. See [LICENSE][license].

[actions-wiring]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md#actions-pin-consistency
[api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/api.md
[audit-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/audit.yml/badge.svg?branch=main
[audit-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/audit.yml
[candidate-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_BENCHMARKS.md
[changelog]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/GENERATING_CHANGELOGS.md
[changelog-history]: https://github.com/acgetchell/research-repo-tools/blob/main/CHANGELOG.md
[checks-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_CHECKS.md
[ci-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/ci.yml/badge.svg?branch=main
[ci-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/ci.yml
[codecov-badge]: https://codecov.io/gh/acgetchell/research-repo-tools/branch/main/graph/badge.svg
[codecov-dashboard]: https://app.codecov.io/gh/acgetchell/research-repo-tools
[codeql-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/codeql.yml/badge.svg?branch=main
[codeql-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/codeql.yml
[commands]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_COMMANDS.md
[consumer-release-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/RELEASING.md
[contributing]: https://github.com/acgetchell/research-repo-tools/blob/main/CONTRIBUTING.md
[dependabot]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/AUTOMATING_DEPENDABOT.md
[dependency-sources]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md#verified-dependency-sources
[dev-pins]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md#development-pins-and-release-dependencies
[doi]: https://doi.org/10.5281/zenodo.23744167
[draft-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PREPARING_DRAFTS.md
[evidence-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/VERIFYING_RELEASE_EVIDENCE.md
[first-release]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/candidate-reuse-api.md#first-stable-release
[github-settings]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/CONFIGURING_GITHUB.md
[license]: https://github.com/acgetchell/research-repo-tools/blob/main/LICENSE
[license-badge]: https://badgen.net/github/license/acgetchell/research-repo-tools
[line-checks]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_CHECKS.md#raw-text-line-limits
[migration]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md
[notebooks-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_NOTEBOOKS.md
[papers-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/BUILDING_PAPERS.md
[performance-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RECORDING_PERFORMANCE.md
[pypi]: https://pypi.org/project/research-repo-tools/
[pypi-badge]: https://badgen.net/pypi/v/research-repo-tools
[recovery-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RECOVERING_RELEASES.md
[release]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_RELEASE_METADATA.md
[reports-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PUBLISHING_REPORTS.md
[reuse-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_CI.md
[review-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_CHECKS.md#coderabbit-review
[security-policy]: https://github.com/acgetchell/research-repo-tools/blob/main/SECURITY.md
[series-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_BENCHMARKS.md#starting-a-benchmark-series-after-earlier-releases
[raw-review]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_BENCHMARKS.md#reviewing-a-large-archived-payload
[timing-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/TIMING_COMMANDS.md
[toolchain]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md
[updates-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md
[zizmor-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/zizmor.yml/badge.svg?branch=main
[zizmor-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/zizmor.yml
