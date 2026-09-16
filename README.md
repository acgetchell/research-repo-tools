# research-repo-tools

Shared development and maintenance tooling for Rust research repositories using
Python scripting and Jupyter notebooks. Each
capability has one implementation and one set of contracts, with tests grouped
by capability. Shared toolchain setup supports changelog handling and the
maintenance core; further workflows require a clear shared contract.

This package consolidates shared utilities from `la-stack`, `delaunay`,
`markov-chain-monte-carlo`, and `causal-triangulations`.
Its goal is to simplify that stack around a correct, performant, orthogonal,
and simple toolchain, keeping consuming repositories focused on Rust.

Python 3.14+ is required. See [release status and publishing][publishing]
and the [supported CLI and Python interfaces][api].

## Install with uv

Users install pinned releases directly from PyPI with uv in their own projects.
Cloning this repository is for developing or fixing the package. Once the desired
version is published, replace `X.Y.Z` with that version:

```sh
uv add --dev "research-repo-tools==X.Y.Z"
uv run --locked research-repo-tools --help
```

For managed Python/Rust/Cargo setup, follow the [toolchain setup guide][toolchain]
to put the package in a tooling dependency group and generate launchers in the
consuming project. On a fresh machine without uv, that project's launcher obtains
uv and installs its locked package from PyPI. The shared package's source checkout
is not needed.

Every installation supplies the pinned `just` executable through `rust-just`,
alongside `packaging` and PyYAML. No extra is needed. Before publication,
contributors can use [local development installs][contributing] for validation.

## Common workflows

| Capability | Commands | Contract |
| --- | --- | --- |
| Changelog | `changelog archive`, `generate`, `normalize`, `notes`, `tag` | Root `CHANGELOG.md`; completed minor series in `docs/archives/changelog/` |
| Coverage | `coverage report` | Cobertura summaries with deduplicated source lines |
| Dependencies | `deps check-uv`, `update-python`, `update-tools` | Exact development pins; canonical Cargo SemVer; stable uv pins |
| Documentation | `docs check-lines` | UTF-8 Markdown line checks with table exemptions |
| Release metadata | `release check`, `release update` | Infer Cargo or Python metadata; validate before replacing files |
| Semgrep fixtures | `semgrep check-fixtures` | Validate consumer-supplied rules and positive fixture coverage |
| Templates | `templates NAME` | Shared changelog, git-cliff, just, TOML, and rumdl resources |
| Toolchain | `toolchain bootstrap`, `check`, `run`, `sync` | Exact declarations; managed installations; generated uv launchers |

### Just recipes

For routine workflows, merge the [consumer justfile template][just-template] into
the consuming repository's justfile. These thin recipes invoke its locked package
version. Use `uv run --locked just ...` to select the bundled just executable.
Run `just help`, `just help-workflows`, or bare `just` to list commands and their
arguments in lexicographic order.

| Consumer recipe | Purpose |
| --- | --- |
| `just bootstrap` | Generate bootstrap launchers from declarations |
| `just bootstrap-check` | Check that bootstrap launchers match declarations |
| `just changelog` | Generate, normalize, and archive completed minor series |
| `just changelog-archive` | Archive existing notes without regenerating history |
| `just changelog-preview` | Validate the generated root and archives without writing; print the root candidate |
| `just changelog-release TAG DATE` | Generate a prospective release with an explicit `YYYY-MM-DD` date |
| `just changelog-unreleased TAG DATE` | Alias for `changelog-release` |
| `just help` | List available commands and arguments in lexicographic order |
| `just help-workflows` | Alias for `help` |
| `just release-check` | Check consumer release metadata |
| `just release-notes TAG` | Print release notes from the root changelog or an archive |
| `just semgrep-check` | Validate the consumer's Semgrep rules and fixtures |
| `just setup` | Install and verify declared tools, then synchronize the Python environment |
| `just tag TAG` | Forward to `tag-release` |
| `just tag-force TAG` | Explicitly replace an existing local tag |
| `just tag-release TAG` | Create a local annotated tag from validated release notes |
| `just tools-check` | Check installed tools and versions without installing them |
| `just update-python-deps` | Update exact direct Python development pins |

To preview a prospective release, run
`just changelog-preview --tag v1.2.3 --date YYYY-MM-DD`.
Follow the [toolchain guide][toolchain] for declarations, first-time setup, and
strictly read-only tool checks.

### Direct CLI examples

The command groups above are subcommands of `research-repo-tools`. Prefix direct
invocations with `uv run --locked` when using the project-installed package:

```sh
research-repo-tools --help
research-repo-tools changelog archive
research-repo-tools changelog generate --help
research-repo-tools changelog generate --tag v1.2.3 --date 2026-09-07 --dry-run
research-repo-tools changelog normalize
research-repo-tools changelog notes v1.2.3
research-repo-tools coverage report --prefix src --limit 10
research-repo-tools deps update-tools --dry-run
research-repo-tools docs check-lines README.md CHANGELOG.md
research-repo-tools release check
research-repo-tools release update --help
research-repo-tools release update 1.2.3 --previous-release v1.2.2 --dry-run
```

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

External-tool installation is explicit through `toolchain sync`; managed
execution selects the checked versions. Generic notebook setup, execution, and
validation remain planned capabilities.
Scientific benchmarks, evidence schemas, plotting, and deployment workflows are
also outside this first version. Scientific algorithms,
case inventories, repository rules, and custom release commands stay in their
consuming repositories.

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
research-repo-tools templates CHANGELOG.md
research-repo-tools templates cliff.toml --owner example --repository consumer
research-repo-tools templates justfile
research-repo-tools templates research-repo-tools.toml
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

[publishing]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PUBLISHING.md
[contributing]: https://github.com/acgetchell/research-repo-tools/blob/main/CONTRIBUTING.md
[api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/api.md
[toolchain]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md
[changelog]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/GENERATING_CHANGELOGS.md
[release]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_RELEASE_METADATA.md
[migration]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md
[license]: https://github.com/acgetchell/research-repo-tools/blob/main/LICENSE
[just-template]: https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/justfile
