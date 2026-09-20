# research-repo-tools

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
| Coverage | `coverage report` | Cobertura summaries with deduplicated source lines |
| Dependencies | `deps check-uv`, `update-python`, `update-tools`, `update-uv` | Exact development pins; canonical Cargo SemVer; stable uv pins |
| Documentation | `docs check-lines` | UTF-8 Markdown line checks with table exemptions |
| Notebooks | `notebooks check`, `clear`, `execute`, `group`, `lint`, `sync` | Optional locked environment, cell-aware Ruff/ty checks, and execution reports |
| Release metadata | `release check`, `release update` | Infer Cargo or Python metadata; validate before replacing files |
| Review | `review branch`, `review uncommitted` | Opt-in CodeRabbit review with verified default base and streamed findings |
| Semgrep fixtures | `semgrep check-fixtures` | Validate consumer-supplied rules and positive fixture coverage |
| Setup | `setup` | Require uv; install user Just and declared tools; sync the locked environment |
| Templates | `templates NAME` | Shared changelog, git-cliff, just, TOML, and rumdl resources |
| Toolchain | `toolchain check`, `run`, `sync`, `upgrade` | Exact declarations; managed installations; verified execution and explicit Cargo upgrades |

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
| `just help` | List available commands and arguments in lexicographic order |
| `just help-workflows` | Alias for `help` |
| `just notebook-check FILE...` | Validate notebook structure, cell IDs, and output policy |
| `just notebook-clear FILE...` | Deliberately clear generated notebook state |
| `just notebook-execute FILE...` | Execute selected notebooks and write results and reports |
| `just notebook-lint FILE...` | Check structure, output policy, Python syntax, Ruff rules/formatting, and ty types |
| `just notebook-sync` | Synchronize locked notebook dependencies and the project kernel |
| `just release-check` | Check consumer release metadata |
| `just release-notes TAG` | Print release notes from the root changelog or an archive |
| `just review [base]` | Review branch and local changes; default to verified `origin/main` |
| `just review-uncommitted` | Review staged, unstaged, and non-ignored untracked changes |
| `just semgrep-check` | Validate the consumer's Semgrep rules and fixtures |
| `just setup` | Install and verify declared tools, then synchronize the Python environment |
| `just tag TAG` | Forward to `tag-release` |
| `just tag-force TAG` | Explicitly replace an existing local tag |
| `just tag-release TAG` | Create a local annotated tag from validated release notes |
| `just tools-check` | Check installed tools and versions without installing them |
| `just update` | Upgrade tools, then Cargo and Python dependencies and the development environment |
| `just update-cargo-dependencies` | Upgrade root Cargo requirements (including incompatible releases) and lock resolution; skip projects without a root Cargo.toml |
| `just update-cargo-tools` | Upgrade declared managed Cargo tools and publish verified TOML pins |
| `just update-dependencies` | Run the Cargo and Python dependency workflows |
| `just update-python-dependencies` | Update direct dev pins, upgrade the full Python lock, and synchronize dev |
| `just update-python-deps` | Alias for `update-python-dependencies` |
| `just update-tools` | Upgrade uv and managed Cargo tools, then run setup |
| `just update-uv` | Upgrade uv through its installation owner and reconcile its pin |

To preview a prospective release, run
`just changelog-preview --tag v1.2.3 --date YYYY-MM-DD`.
Follow the [toolchain guide][toolchain] for declarations, first-time setup, and
strictly read-only tool checks.

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
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo upgrade --incompatible allow --exclude coupled-a --exclude coupled-b
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo upgrade --manifest-path "fixtures/extra root/Cargo.toml" --incompatible allow
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo update
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo update --manifest-path "fixtures/extra root/Cargo.toml"
```

Exclusions apply to requirement upgrades; lock resolution still follows all
declared constraints. Keep the required order of these commands and retain
consumer checks for coupled dependencies and additional manifests. Review both
manifests and lockfiles after updating. See [update adoption][update-adoption]
for superseded policies and consumer integration requirements.

`changelog tag TAG` creates a local annotated tag when explicitly invoked.
`--dry-run` previews it. Generation needs git-cliff; tagging needs Git. Optional
formatting needs rumdl, fixture validation needs Semgrep, and dependency
updates need uv or Cargo. These executables are required only by the commands
that invoke them. The CLI does not publish packages or hosted releases; this
repository's tagged-release workflow publishes the tooling package to PyPI.

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
destinations. See the [notebook contract](docs/RUNNING_NOTEBOOKS.md) for output
policy, failure reports, and environment configuration. Linting uses the locked
project's Ruff and ty with the consumer's configuration and preserves cell IDs
in diagnostics. `notebook-check` provides structure and output checks alone.

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

See [supported interfaces][api] for return values, errors, and API stability.

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
[changelog]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/GENERATING_CHANGELOGS.md
[release]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_RELEASE_METADATA.md
[migration]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md
[update-adoption]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md#dependency-and-tool-update-adoption
[license]: https://github.com/acgetchell/research-repo-tools/blob/main/LICENSE
[just-template]: https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/justfile
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
