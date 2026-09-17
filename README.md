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
| Changelog | `changelog archive`, `generate`, `normalize`, `notes`, `tag` | Root `CHANGELOG.md`; completed minor series in `docs/archives/changelog/` |
| Coverage | `coverage report` | Cobertura summaries with deduplicated source lines |
| Dependencies | `deps check-uv`, `update-python`, `update-tools`, `update-uv` | Exact development pins; canonical Cargo SemVer; stable uv pins |
| Documentation | `docs check-lines` | UTF-8 Markdown line checks with table exemptions |
| Release metadata | `release check`, `release update` | Infer Cargo or Python metadata; validate before replacing files |
| Semgrep fixtures | `semgrep check-fixtures` | Validate consumer-supplied rules and positive fixture coverage |
| Setup | `setup` | Require uv; install user Just and declared tools; sync the locked environment |
| Templates | `templates NAME` | Shared changelog, git-cliff, just, TOML, and rumdl resources |
| Toolchain | `toolchain check`, `run`, `sync` | Exact declarations; managed installations; verified execution |

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
| `just update` | Upgrade uv, reconcile its pin, update Python development pins and locked dependencies, and synchronize tools |
| `just update-python-deps` | Update exact direct Python development pins |

To preview a prospective release, run
`just changelog-preview --tag v1.2.3 --date YYYY-MM-DD`.
Follow the [toolchain guide][toolchain] for declarations, first-time setup, and
strictly read-only tool checks.

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

External-tool installation is explicit through `setup` or `toolchain sync`; managed
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

[publishing]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PUBLISHING.md
[contributing]: https://github.com/acgetchell/research-repo-tools/blob/main/CONTRIBUTING.md
[api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/api.md
[toolchain]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md
[changelog]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/GENERATING_CHANGELOGS.md
[release]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_RELEASE_METADATA.md
[migration]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md
[license]: https://github.com/acgetchell/research-repo-tools/blob/main/LICENSE
[just-template]: https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/justfile
