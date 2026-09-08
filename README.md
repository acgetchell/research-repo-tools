# research-repo-tools

Common Python tools for maintaining research software repositories. Each
capability has one implementation and one set of contracts, with tests grouped
by capability. Start with changelog handling and a small maintenance core; add
further workflows only after their shared contract is clear.

Python 3.14+ is required. This package has not been published to PyPI.

## Install with uv

Build a wheel locally:

```sh
uv sync --locked --all-extras
uv run --locked just build
```

Then, from a consuming project, install the local wheel:

```sh
uv add --dev /path/to/research-repo-tools/dist/research_repo_tools-0.1.0-py3-none-any.whl
uv run --locked research-repo-tools --help
```

For development across local checkouts, use
`uv add --dev --editable /path/to/research-repo-tools` instead. Neither form
requires publication. The optional `just` extra supplies the pinned `rust-just`
executable; the base package requires only `packaging` and PyYAML.

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

Notebook execution, scientific benchmarks, evidence schemas, plotting, and
deployment workflows are outside this first version. Scientific algorithms,
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
just_version = "rust-just"
rumdl_version = "rumdl"
uv_version = "uv"

[tool.research-repo-tools.semgrep]
config = "semgrep.yaml"
fixtures = "tests/semgrep"
```

`--config PATH` reads unprefixed tables from a separate TOML file when needed.
Paths are relative to the configuration directory; `--root PATH` selects an
explicit consumer root. Unknown settings fail. There are no repository profiles.

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

See [changelog behavior](docs/changelog.md), [release behavior](docs/release.md),
[scope and adoption](docs/migration.md), and [validation](docs/validation.md).
For development, run `just check` during iteration and `just ci` at the end.

## License

BSD-3-Clause, with one top-level [LICENSE](LICENSE). [NOTICE.md](NOTICE.md) and
[source provenance](docs/provenance.json) provide attribution.
