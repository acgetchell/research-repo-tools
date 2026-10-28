# research-repo-tools

[![PyPI version][pypi-badge]][pypi]
[![License][license-badge]][license]
[![CI][ci-badge]][ci-workflow]
[![CodeQL][codeql-badge]][codeql-workflow]
[![zizmor][zizmor-badge]][zizmor-workflow]
[![Codecov][codecov-badge]][codecov-dashboard]
[![Audit dependencies][audit-badge]][audit-workflow]

Shared setup, checks, notebooks, performance evidence and release tooling for
research software repositories. Install one pinned Python package and invoke it
through your repository's Just recipes. Keep scientific algorithms, benchmark
case selections, native builds and deployment policy in your own project.

Start with [installation](#install-with-uv), try the [first workflow](#first-workflow),
then choose a [capability](#capability-index). Package development belongs in
[CONTRIBUTING.md][contributing].

## Release availability

This README describes current source, including work intended for **v0.2.4**.
The quickstart uses the published [0.2.3 package][published-pin]. Newer examples
are marked where a supplying release is required: prepared performance stages,
parallel Criterion suites, shared release-evidence handoffs, command timing,
optional CI reuse, and the notebook/stream refinements need v0.2.4.

An upstream merge, local wheel or passing source test does not publish a package.
Before adopting a new capability, verify its exact PyPI release, update your
consumer's pin and lockfile, and run its integration checks. Keep working adapters
until that adoption succeeds. Package publication and consumer adoption are
separate steps. [Migration guidance][migration] covers compatibility changes;
the generated [CHANGELOG.md][changelog-history] records release history.

## Install with uv

Use an existing consumer Git repository; you do not need to clone this package.
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git first.
Recipes require a POSIX `sh` on PATH, including on Windows: add Git for Windows'
`bin` directory, typically `C:\Program Files\Git\bin`. uv supplies managed Python;
the published package requires Python 3.15 or newer.

From the consumer root, add the exact published package:

```sh
uv add --group tooling --no-sync "research-repo-tools==0.2.3"
```

Include `tooling` from `dev`, retaining your other dependencies. Set
`.python-version` to a compatible selection such as `3.15`. Keep any stricter
application Python constraints; use the [adoption workflow][python-adoption]
if the project currently requires an older interpreter.

```toml
[dependency-groups]
tooling = ["research-repo-tools==0.2.3"]
dev = [{include-group = "tooling"}]
```

Declare the uv version you installed as `tool.uv.required-version = "==X.Y.Z"`.
The [toolchain guide][toolchain] explains Rust and optional tool declarations;
a Python-only consumer can omit them. Refresh the lock, then export the matching
installed Just template:

```sh
uv lock
uv run --locked --only-group tooling research-repo-tools templates justfile --output justfile
```

For an existing justfile, omit `--output justfile` and merge the printed recipes;
the exporter refuses to overwrite files. Review and retain the manifest, lockfile,
Python selector and recipes together.

Run the one-time setup:

```sh
uv run --locked --managed-python --only-group tooling research-repo-tools setup
```

Setup verifies the declarations, installs a pinned user-level Just and declared
tools, configures PATH and synchronizes the locked development environment.
Open a new terminal if PATH changed. A failed step stops setup with a diagnostic;
fix that prerequisite and rerun. [Installation and recovery][toolchain] explains
ownership, native build prerequisites and platform behavior.

## First workflow

After setup, list your recipes and check Markdown source lines in the consumer:

```sh
just help
just files check-lines --limit 160 --include '*.md' --exclude CHANGELOG.md
```

This read-only check needs no optional scanner or notebook dependencies. It selects
tracked and nonignored Markdown, counts every raw line (including tables and code),
and returns zero for a clean inventory. Long lines produce
`file:line: line length N exceeds 160` and status 1. Fix the selected source or
[declare your intended selection and limit][line-checks], then rerun.

Use `just ...` for routine work; no environment activation is needed. Recipes
select the locked package internally. The underlying CLI and [Python APIs][api]
remain available for integrations.

## Common workflows

These are independent commands; run only the workflow you intend. Some require
consumer configuration or optional dependencies described in their guides.

| Command | Use |
| --- | --- |
| `just changelog-preview --tag v1.2.3 --date YYYY-MM-DD` | Inspect prospective notes without replacing files |
| `just check` | Run your configured local checks and native/domain gates |
| `just files check-lines --limit 160 --include '*.md'` | Check selected raw text without formatting |
| `just help` | Discover recipes supplied by your installed template |
| `just notebook-inspect notebooks/analysis.ipynb --no-preview` | Inspect notebook structure without executing cells |
| `just setup` | Recreate the environment from existing declarations |
| `just tools-check` | Verify installed tools and versions without installing |
| `just update` | Upgrade declared tools and dependencies, then audit final locks |

The [complete command and capability reference][commands] is organized lexicographically.
`just help` remains the authority for the recipes actually merged into your project.
The [packaged templates][just-template] provide thin wrappers; consumers extend
checks with their own Rust, scientific and deployment requirements.

## Capability index

Choose a task guide for execution order and outcomes; use its linked reference
for exhaustive arguments, configuration and Python contracts.

| Task | Primary guide | Reference |
| --- | --- | --- |
| Automate Dependabot review and merge | [Configure the shared workflow][dependabot] | [GitHub settings][github-settings] |
| Build reproducible papers | [Dates, PDFs and native dependencies][papers-guide] | [Paper APIs][papers-api] |
| Check code, dependencies and secrets | [Python, Cargo, Semgrep, SARIF and review][checks-guide] | [Security APIs][security-api] |
| Generate changelogs and prepare releases | [Changelog generation][changelog], [metadata preparation][release] | [Release APIs][publishing-api] |
| Inspect and execute notebooks | [Notebook workflows][notebooks-guide] | [Notebook APIs][notebooks-api] |
| Publish reports and figures | [Preview and publish retained evidence][reports-guide] | [Publication APIs][publication-api] |
| Publish to PyPI or crates.io | [Reviewed release procedure][publishing] | [Exact-commit evidence][evidence-guide], [optional reuse][reuse-guide] |
| Record complete performance results | [Prepare, measure/retain, publish README][performance-guide] | [Measurement policy][performance-policy], [complete-run APIs][complete-api] |
| Select files, run commands and export CI values | [Consumer checks and integration][checks-guide] | [File APIs][files-api], [process/Just APIs][process-api], [workflow APIs][workflow-api] |
| Set up or repair tools | [Installation and ownership][toolchain] | [Toolchain APIs][toolchain-api] |
| Time external commands | [Exploratory command timing][timing-guide] | [Timing evidence contract][timing-api] |
| Update dependencies and Actions pins | [Update and adoption workflows][updates-guide] | [CLI/configuration][configuration-api] |

### Notebooks

Add the `notebooks` extra at your exact tooling version to a separate notebook
dependency group. Declare analysis packages and the required Ruff/ty checkers,
refresh the lock, then follow [notebook setup][notebooks-guide]:

```sh
just notebook-sync
just notebook-lint notebooks/analysis.ipynb
just notebook-execute notebooks/analysis.ipynb
```

Execution preserves source notebooks and writes executed copies plus JSON reports
under `target/notebooks`. A failed cell returns nonzero and identifies the cell.
Consumers retain scientific assertions and fast/slow selection. Interactive
JupyterLab, explicit source reset and source-policy checks are in the same guide.

### Performance

For v0.2.4, configure the installed `performance-release.toml` template with your
measurement, retained-report and document-publication policies. Declare and ignore
its scratch directory; keep the report archive outside scratch. Then run:

```sh
just performance-prepare tooling/performance-release.toml
just performance-release tooling/performance-release.toml
just performance-readme tooling/performance-release.toml --preview
just performance-readme tooling/performance-release.toml
```

Preparation saves independently selected release tags and full commits. Measurement
runs the gates and benchmarks, then retains/promotes the complete evidence.
README publication only consumes retained evidence: repair a presentation failure
and retry that last stage without measuring again. The [performance guide][performance-guide]
explains source exports versus worktrees, common versus independent harnesses,
parallel suite assembly, ratio eligibility and first-use report state.

### Releases

Follow the [ordered release procedure][publishing] and matching installed
`RELEASING.md` template. Exact-commit checks are the default. [CI reuse][reuse-guide]
is an explicit reviewed option with its own eligibility and revalidation;
package and artifact checks still apply. Approval, registry publication,
attachment uploads and downstream adoption remain distinct actions.

## Contributing

See [CONTRIBUTING.md][contributing] for package development, tests and maintainer
commands. [Supported interfaces][api] defines the consumer compatibility boundary.

## Citation

If you use research-repo-tools in your work, cite the software using
[CITATION.cff](https://github.com/acgetchell/research-repo-tools/blob/main/CITATION.cff). Include the version used so others can identify
the same release.

## License

BSD-3-Clause. See [LICENSE][license].

[api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/api.md
[audit-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/audit.yml/badge.svg?branch=main
[audit-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/audit.yml
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
[complete-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/complete-run-api.md
[configuration-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/configuration-api.md
[contributing]: https://github.com/acgetchell/research-repo-tools/blob/main/CONTRIBUTING.md
[dependabot]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/AUTOMATING_DEPENDABOT.md
[files-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/files-api.md
[github-settings]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/CONFIGURING_GITHUB.md
[just-template]: https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/justfile
[license]: https://github.com/acgetchell/research-repo-tools/blob/main/LICENSE
[license-badge]: https://badgen.net/github/license/acgetchell/research-repo-tools
[line-checks]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_CHECKS.md#raw-text-line-limits
[migration]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md
[notebooks-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/notebooks-api.md
[notebooks-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_NOTEBOOKS.md
[papers-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/papers-api.md
[papers-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/BUILDING_PAPERS.md
[performance-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RECORDING_PERFORMANCE.md
[performance-policy]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/DECLARING_PERFORMANCE.md
[process-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/process-api.md
[publication-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/publication-api.md
[published-pin]: https://pypi.org/project/research-repo-tools/0.2.3/
[publishing]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RELEASING.md
[publishing-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/publishing-api.md
[pypi]: https://pypi.org/project/research-repo-tools/
[pypi-badge]: https://badgen.net/pypi/v/research-repo-tools
[python-adoption]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md#shared-python-adoption
[release]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_RELEASE_METADATA.md
[reports-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PUBLISHING_REPORTS.md
[reuse-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_CI.md
[security-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/security-api.md
[timing-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/command-timing-api.md
[timing-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/TIMING_COMMANDS.md
[toolchain]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md
[toolchain-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/toolchain-api.md
[updates-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md
[workflow-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/workflow-api.md
[zizmor-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/zizmor.yml/badge.svg?branch=main
[zizmor-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/zizmor.yml
[evidence-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/VERIFYING_RELEASE_EVIDENCE.md
