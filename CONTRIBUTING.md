# Contributing

Clone this repository to develop features or fix bugs in the shared package.
Users install releases from PyPI with uv in their own projects; see
[installation](README.md#install-with-uv).

## Development environment

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first,
using the version declared in `pyproject.toml`. From this checkout, run the
package setup command. It installs the pinned user-level Just command,
configures PATH, and synchronizes the locked development environment.
Recipes require a POSIX `sh` on PATH; on Windows, expose Git for Windows' `bin`
directory as described in the [toolchain guide](docs/INSTALLING.md).

```sh
uv run --locked --managed-python research-repo-tools setup
```

Open a new terminal if PATH changed, then use the recipes directly:

```sh
just help
just check
```

No virtual-environment activation is required. The manifest and lockfile own
Python, Ruff, ty, pytest, and the other development dependencies; recipes invoke
uv internally. Run `just setup` again after changing tool declarations.

## Maintainer commands

These recipes belong to this package's root justfile. Consumer setup and daily
commands are documented in the [README](README.md#just-recipes).
Run `just help`, `just help-workflows`, or
`just` for the complete lexicographically sorted command list,
including changelog recipes and aliases.

| Recipe | Purpose |
| --- | --- |
| `just audit` | Audit locked Python dependencies against online vulnerability advisories |
| `just build` | Build the wheel and source distribution |
| `just check` | Check the lockfile, Python linting, formatting, types, and workflows |
| `just check-dist` | Check isolated installations of existing build artifacts |
| `just check-setup` | Exercise real setup on disposable GitHub-hosted runners only |
| `just ci` | Run checks, tests, builds, and installation checks for final review |
| `just coverage` | Run tests with branch and subprocess coverage; write `coverage/cobertura.xml` |
| `just help` | List available commands and arguments in lexicographic order |
| `just help-workflows` | Alias for `help` |
| `just install-check` | Build and check isolated installations |
| `just release-check TAG` | Run the read-only PyPI publication preflight |
| `just release-update VERSION PREVIOUS DATE` | Synchronize release metadata before generating notes |
| `just review [base]` | Run opt-in CodeRabbit review of branch and local changes |
| `just review-uncommitted` | Run opt-in CodeRabbit review of local changes only |
| `just setup` | Install user Just and synchronize the declared development environment |
| `just sync` | Synchronize the locked development environment |
| `just tag-preview TAG` | Preview the annotated release tag without changing Git state |
| `just test` | Run the Python test suite |
| `just update` | Upgrade uv, reconcile its pin, update Python development pins, refresh the lockfile, and sync |
| `just workflow-check` | Run actionlint and offline zizmor checks |

The maintainer justfile also exposes the [shared changelog recipes](README.md#just-recipes).
Its `just release-check TAG` performs the package publication preflight;
the consumer recipe `just release-check` validates consumer release metadata.

Use `just check` while iterating, with targeted regressions when
a behavioral change needs verification. Run `just ci` once the
work is ready for final review.
It runs checks and tests with coverage, builds wheel and sdist artifacts, and installs both
outside the checkout with uv. Installation checks exercise the console entry
point, imports, packaged templates, runtime dependencies, and the bundled `just` executable.
See [Validation](docs/VALIDATING.md) for check coverage and agent restrictions.
Hosted CI builds once and installs the same wheel and sdist on all three platforms
using `just check-dist`. Linux uses `just coverage`; macOS and Windows use
`just test`. CI calls the reusable `codecov.yml` workflow to upload the Linux
report without rerunning tests. The required platform jobs also run `just check-setup`
against the built wheel, installing real tools and updating the disposable
runner user's shell configuration. This check is excluded from local `just ci`.
The [release workflows](docs/RELEASING.md) attach the validated distributions
and signed provenance to a draft GitHub Release. Publishing that release triggers
asset verification and the approval-gated PyPI upload without rebuilding.

`just check` lints, formats, and type-checks `src`, `scripts`, and
`tests`. It also runs the locked actionlint and zizmor workflow validators.
`just audit` performs the separate network-backed Python dependency audit.
GitHub repository settings and required checks are documented in
[GitHub setup](docs/CONFIGURING_GITHUB.md); their API payloads live in `.github/settings/`.

Run `just update` to upgrade uv through its owner, reconcile its manifest pin,
then advance exact development-tool pins through
this package's `deps update-python` command, refresh `uv.lock` within the resulting manifest
constraints, and sync the development environment. Review the manifest and lockfile
changes, then validate them with `just ci`. Exact runtime, build,
and audit pins remain unchanged by this recipe.

## Local package evaluation

For pre-publication checks or debugging across local checkouts, build a wheel:

```sh
just build
```

Then install it in a disposable consuming project:

```sh
uv add --dev /path/to/research-repo-tools/dist/research_repo_tools-0.1.0-py3-none-any.whl
uv run --locked research-repo-tools --help
```

An editable install with `uv add --dev --editable /path/to/research-repo-tools`
also supports local development. For setup evaluation, use the `tooling`
group described in the [toolchain guide](docs/INSTALLING.md). Local wheels and
editable installs are development aids; normal consumer setup and CI use a
pinned PyPI release.

## Generated changelog

This repository uses its own shared changelog implementation and packaged
git-cliff template. Write Conventional Commit subjects and useful commit bodies;
they are the source of release notes. Generate `CHANGELOG.md` rather than adding
entries by hand:

```sh
just changelog-preview
just changelog
```

These commands require git-cliff. Run them after committing substantive changes
when preparing release notes, then review and commit the generated file.
Uncommitted changes cannot appear in a changelog generated from Git history.
Generation includes the shared normalization and validation steps.

## Shared implementation

The package's runtime `__version__` and CLI `--version` come from installed
distribution metadata, whose source is `project.version` in `pyproject.toml`.
Use the uv environment so imports see the matching installed distribution.
See [Supported interfaces](docs/api.md) for the CLI, configuration, and public
Python entry-point contract.

Maintain one implementation per common capability under `src/research_repo_tools/`.
Organize tests under `tests/changelog`, `dependencies`, `notebooks`, `releases`, `review`, `semgrep`, `toolchain`, and
`utilities`. Fixtures should be small representative inputs generated in temporary
directories. Preserve meaningful regression assertions against this package;
merge duplicates instead of maintaining historical implementations or repository
snapshots. Use actual child processes for byte transport and minimal disposable
repositories for Git behavior.
Notebook tests use synthetic files and real fresh kernels for interpreter,
working-directory, cell-error, and timeout behavior. They require local socket
access for Jupyter. Optional notebook dependencies are pinned in the development
group but remain absent from maintenance-only distribution installations.

A new capability needs a shared purpose and a consistent contract. Repository
names must never select behavior. Standardize common defaults; leave scientific
algorithms, datasets, notebook content, benchmark cases, and custom workflows in
consumers. Do not add compatibility flags merely to retain old script differences.

Normal CLI failures should identify the relevant input without a traceback.
Imports must have no network or consumer-file side effects. Changes involving
multiple files must validate candidates before replacement and preserve originals
on caught failures; incomplete rollback must report recovery files.

Follow [AGENTS.md](AGENTS.md): agents must not run mutating Git commands in this
or consumer repositories. Leave staging, commits, tags, pushes, and branch changes
to the user. Tests exercise Git mutations only in disposable fixtures. Source
repositories remain read-only. Package publication requires an explicit request;
follow [Releasing research-repo-tools](docs/RELEASING.md) for account setup, review, tagging,
and deployment approval.

## Documentation conventions

Keep installation, consumer commands, and usage examples in README.md; keep
coding and package-development guidance here. Use `docs/` for detailed guides
and informational references.

Documents containing operational commands use UPPERCASE gerund filenames, such
as `INSTALLING.md` and `VALIDATING.md`. Informational documents use lowercase
names, such as `api.md` and `migration.md`. Preserve established root filenames
such as README.md, AGENTS.md, SECURITY.md, and the generated CHANGELOG.md.

Document uv as a prerequisite and setup that installs a user-level Just command, then
use `just ...` for routine commands without environment activation. Keep uv invocation details inside the recipes.

Keep recipe definitions, CLI help, and command-reference lists in lexicographic
order. Generate Just help from recipe descriptions so it stays current.
Multi-step workflow examples follow the order required to execute them.

Keep `docs/` focused on current behavior and procedures.
Record validation results and limitations in PR descriptions, review notes, or
CI logs. Fixes and release history belong in the generated
[CHANGELOG.md](CHANGELOG.md); other documents should link to it instead of
maintaining parallel logs. Keep one top-level LICENSE.
