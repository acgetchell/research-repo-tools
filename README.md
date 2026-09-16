# research-repo-tools

Shared development and maintenance tooling for Rust research repositories using
Python scripting and Jupyter notebooks. Each
capability has one implementation and one set of contracts, with tests grouped
by capability. Start with changelog handling and a small maintenance core; add
further workflows only after their shared contract is clear.

Python 3.14+ is required. This package has not been published to PyPI.

## Install with uv

Consumers will install pinned releases directly from PyPI. After the first
publication, replace `X.Y.Z` with the chosen released version:

```sh
uv add --dev "research-repo-tools==X.Y.Z"
uv run --locked research-repo-tools --help
```

Until then, local wheels and editable installs support development and pilot
validation. They are not the intended dependency source for consumer CI.

Build a wheel locally:

```sh
uv sync --locked
uv run --locked just build
```

Then, from a consuming project, install the local wheel:

```sh
uv add --dev /path/to/research-repo-tools/dist/research_repo_tools-0.1.0-py3-none-any.whl
uv run --locked research-repo-tools --help
```

For development across local checkouts, use
`uv add --dev --editable /path/to/research-repo-tools` instead. Neither form
requires publication. Every installation supplies the pinned `just` executable
through `rust-just`, alongside `packaging` and PyYAML. No extra is needed.

## Common workflows

| Capability | Commands | Contract |
| --- | --- | --- |
| Changelog | `changelog generate`, `normalize`, `archive`, `notes`, `tag` | Root `CHANGELOG.md`; completed minor series in `docs/archives/changelog/` |
| Release metadata | `release check`, `release update` | Infer Cargo or Python metadata; validate before replacing files |
| Dependencies | `deps update-python`, `update-tools`, `check-uv` | Exact development pins; canonical Cargo SemVer; stable uv pins |
| Semgrep fixtures | `semgrep check-fixtures` | Validate consumer-supplied rules and positive fixture coverage |
| Documentation | `docs check-lines` | UTF-8 Markdown line checks with table exemptions |
| Coverage | `coverage report` | Cobertura summaries with deduplicated source lines |
| Templates | `templates NAME` | Shared changelog, git-cliff, just, TOML, and rumdl resources |

```sh
research-repo-tools changelog generate --tag v1.2.3 --date 2026-09-07 --dry-run
research-repo-tools changelog normalize
research-repo-tools changelog archive
research-repo-tools changelog notes v1.2.3
research-repo-tools release check
research-repo-tools release update 1.2.3 --previous-release v1.2.2 --dry-run
research-repo-tools deps update-tools --dry-run
research-repo-tools docs check-lines README.md CHANGELOG.md
research-repo-tools coverage report --prefix src --limit 10
```

`changelog tag TAG` creates a local annotated tag when explicitly invoked.
`--dry-run` previews it. Generation needs git-cliff; tagging needs Git. Optional
formatting needs rumdl, fixture validation needs Semgrep, and dependency
updates need uv or Cargo. These executables are required only by the commands
that invoke them. Nothing here publishes packages or hosted releases.

External-tool installation and generic notebook setup, execution, and validation
are planned shared capabilities. They are not implemented in this first version.
Scientific benchmarks, evidence schemas, plotting, and deployment workflows are
also outside this first version. Scientific algorithms,
case inventories, repository rules, and custom release commands stay in their
consuming repositories.

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
research-repo-tools templates justfile
research-repo-tools templates cliff.toml --owner example --repository consumer
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

See [changelog behavior](docs/changelog.md), [release behavior](docs/release.md),
[scope and adoption](docs/migration.md), and [validation](docs/validation.md).
For development, run `just check` during iteration and `just ci` at the end.
`just audit` separately checks exported locked third-party requirements against online
Python vulnerability advisories. See [GitHub setup](docs/github.md) for repository
security, the initial push, and the later PyPI publishing boundary.

## License

BSD-3-Clause, with one top-level [LICENSE](LICENSE). [NOTICE.md](NOTICE.md) and
[source provenance](docs/provenance.json) provide attribution.
