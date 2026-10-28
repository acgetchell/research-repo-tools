# CLI and configuration contracts

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Command line and configuration](#command-line-and-configuration)
- [Python entry point](#python-entry-point)
- [Python configuration API](#python-configuration-api)
- [External programs and platforms](#external-programs-and-platforms)

## Command line and configuration

The [task guide](../README.md#first-workflow) contains runnable examples and
help commands for inspecting CLI arguments.

Place global `--root` and `--config` options before the command group. Configuration
defaults to `[tool.research-repo-tools]` in the consumer's `pyproject.toml`.
A standalone configuration uses unprefixed tables and `schema = 1`, as shown in
the [packaged template](../src/research_repo_tools/templates/research-repo-tools.toml).
Unknown settings fail. Relative paths and explicit executable paths resolve
against the consumer root; bare executable names use `PATH`.
Explicit `ci export --file` and `toolchain export --file` paths follow this rule.
The `GITHUB_ENV` fallback keeps the path supplied by the invoking environment.

`actions allowlist --policy PATH INPUT...` checks consumer-owned selected-actions
JSON against structured workflow job/step and composite `runs.steps[*].uses`
references. Only the strict false/false owner-mode payload with exact
`owner/repo[/path]@*` or `owner/repo[/path]@<40-hex-SHA>` patterns is supported.
Repository identities and SHA hex ignore case; nested paths match exactly.
Rules are alternatives for each exact identity; repository entries do not
approve subpaths. Exact refs retain their constraints during matching and
diagnostics. Local `./`, same-repository `$/` references, and containers are
outside this policy. YAML aliases are supported with
anchor locations; duplicate/merge keys and recursive aliases fail. Explicit
directories expand to regular `.yml`/`.yaml` files; repeated paths are deduplicated.
Leaf symlinks are rejected and parent paths are resolved. Composite metadata
requires `runs.using: composite` and a `runs.steps` sequence; other action kinds
and documents mixing workflow `jobs` with action `runs` fail. Inputs and
repository settings remain unchanged. See [consumer wiring](UPDATING_DEPENDENCIES.md#actions-allowlists-and-opt-in-pin-updates).

`actions update --policy PATH INPUT... [--check | --dry-run]` resolves explicit
Actions targets from consumer TOML through GitHub CLI. It uses the same workflow
and composite reference discovery as the allowlist and preserves source bytes
outside scalar SHAs/version comments. Selected scalars must be single-line,
unaliased, and already pinned to full SHAs with version comments; allowlist
inspection also supports folded scalars and aliases. `--check` returns 1 for drift;
`--dry-run` previews without drift failure. All candidates and compatibility
inventories are checked before any source replacement. Caught publication
failures use the shared rollback/recovery contract. The YAML/update modules are
implementation details; use the CLI or `cli.main`. Updating pins does not change
the selected-actions policy; exact-SHA approvals require deliberate review.

`deps update-python` advances only exact direct main-project `dev` pins.
Candidates preserve supported uv source/index, override, constraint and build
policy, with each current managed version as a minimum. Unsupported source or
policy forms fail before mutation; see the [supported resolution policy](UPDATING_DEPENDENCIES.md#dependency-and-tool-updates).
Adding `--all-projects` first checks the full project inventory, then advances
those pins and upgrades every selected lock. All retained direct/transitive
versions and platform branches must pass downgrade checks before any project
is synchronized. Synchronization uses checked managed tools and selects `dev`
for the main project, then each `deps.python-projects` directory in order.
These additional projects retain all manifest requirements and select their own
Python version and default groups. Each project uses its own `.venv`; caller
project, interpreter, configuration-file, and active-environment overrides do not
redirect the workflow. Project uv configuration, registry/authentication, cache,
and offline settings remain available.
Directories must be distinct portable paths relative to the repository, without
symlinks or junctions in the project path or manifest, lock, version file, and
environment targets. Declare independent uv projects, not workspace members;
the main `deps.pyproject` project is already included. A later resolver or sync
failure returns nonzero, stops subsequent work, and restores every selected
manifest and lock. If sync began, existing environments are resynchronized from
the original locks and newly created environments are removed. An existing
environment must have a lock for recovery. Recovery failures report the affected
environment or retained file backups; no crash-atomicity or concurrent-writer
guarantee is made. Post-update auditing is separate consumer recipe composition:
its failures retain the final updated files for inspection and repair.
Use the CLI or `cli.main`; the orchestration module is an implementation detail.

`deps update-uv --dry-run` inspects standalone, Homebrew, or `uv tool` ownership
and reports the native operation and intended project-pin reconciliation. Applying
verifies the resulting stable version before preserving/replacing the project
pin. The existing project pin may differ from installed uv; launch with the
packaged `update-uv` recipe. Installation upgrades persist if verification or
publication fails; reported recovery is inspection/repair and retry.
`deps.tool-owners` explicitly selects `cargo`/`homebrew` for mapped user tools;
prebuilt tools migrate to the authoritative `toolchain.binaries` contract.

`toolchain sync-binaries` verifies and repairs only the consumer's exact managed
release-binary pins. It uses the existing host/version cache, release URL/tag
checks, SHA-256 verification and staged executable probes. Warm caches are
probed without release requests; damaged caches use the verified installer.
It performs no package/environment synchronization, Python/Rust/Cargo/Just
installation, or unrelated tool probes. The command supports the usual `--root`
and `--config` options and can be called through `cli.main` without importing
toolchain or prebuilt implementation APIs. Failures return nonzero and retain
previous files and earlier successful installations.

Only release-metadata requests consume the first nonempty `GITHUB_TOKEN`, then
`GH_TOKEN`. All toolchain probes and setup subprocesses remove both variables
case-insensitively while preserving other build settings. Release-installation
diagnostics redact both inherited values. Explicit `toolchain run` commands
inherit the caller's credential variables. The
[GitHub Actions example](INSTALLING.md#authenticated-release-installation-in-github-actions)
separates package installation and ordinary setup from authenticated binary sync.

`zizmor check` verifies one repository-declared scanner pin and requires an explicit
`[tool.research-repo-tools.zizmor] persona`. It supports plain and SARIF reports,
automatic authentication discovery, `--offline`, and `--require-online`.
The [packaged audit and validation contract](../src/research_repo_tools/templates/VALIDATING_WORKFLOWS.md)
defines token precedence, redaction, timeouts, native exit status (including
SARIF's finding behavior), complete Python inventory, and migration requirements.
It is available from installed distributions as `templates VALIDATING_WORKFLOWS.md`.
The `python-validation.toml` and `zizmor.yml` templates accompany it. The zizmor
module itself is an implementation detail; use the supported CLI entry point.

`python check`, `python fix`, and `python typecheck` use the same complete Git
inventory and native consumer policy. Checks require existing Ruff/ty executables;
only `fix` enables source edits. See the [Python gate contract](../src/research_repo_tools/templates/VALIDATING_WORKFLOWS.md#python-inventory-and-policy)
for empty selections, batching, timeouts, and failure behavior. The `python_checks`
module is private; use the CLI or `cli.main`.

Commands return zero on success. Validation failures and handled operational
errors return nonzero; argument errors return `2`. Help and version output are
successful exits. Diagnostics use stderr, while reports and generated content
use stdout. Human-readable diagnostics and progress messages are not a structured
machine API. Template output and extracted release notes are intended for reuse.
Notebook inspection additionally supports a [versioned JSON inventory](notebook-inspection.md).
Completed inventories may report repair problems with status zero; advisory
warnings fail only when strict mode is enabled. These review commands do not
replace notebook validation.

Review commands require Git and an externally installed, authenticated CodeRabbit
CLI. `review branch --base origin/main` verifies the cached base against the remote;
`review uncommitted` does not query a remote. Both use the configured consumer root
and require its `AGENTS.md` and exactly one CodeRabbit YAML configuration. See the
[review contract and recipes](RUNNING_CHECKS.md#coderabbit-review). CodeRabbit output is
streamed without a wrapper timeout. Its exit status propagates; signal termination
maps to 128 plus the signal number, and keyboard interruption returns 130.

File-changing commands operate only when invoked: dependency and release updates,
changelog generation/normalization/archiving, template output, local tagging,
explicit setup/toolchain synchronization and upgrades, and notebook synchronization
or output cleanup. Notebook launch synchronizes its locked environment and uses
private session caches; notebook reset previews by default and requires `--apply`
for restoration and declared deletion. Notebook execution publishes separate artifacts. Performance
output, asset retrieval, and extraction publish only when explicitly invoked.
Dry runs are available only where command help lists them. Importing the package
does not install tools, access the network, or modify consumer files.

## Python entry point

Thin Python scripts can reuse the same command contract without a subprocess;
see the [worked example](process-api.md#calling-from-python).

- `research_repo_tools.__version__` is a string read from installed distribution
  metadata. `project.version` in this package's `pyproject.toml` is its authority.
- `research_repo_tools.cli.main(argv: list[str] | None = None) -> int` accepts
  arguments without the executable name. `None` uses the process arguments.
  It writes to the process stdout/stderr and returns the command status.
  Argument parsing raises `SystemExit(0)` for help/version and `SystemExit(2)`
  for usage errors. Unexpected programming errors may propagate.

## Python configuration API

Use `research_repo_tools.config.load(path=None, root=None)` to construct settings
for the [documented public APIs](api.md). By default it reads `[tool.research-repo-tools]` from
`pyproject.toml` under `root`, or the current directory when `root` is omitted.
An explicit `path` selects a configuration file; relative file paths use the
caller's directory. Files named `pyproject.toml` use the prefixed table; other
filenames use unprefixed tables. Without an explicit root, the file's parent is
the consumer root. A missing default manifest supplies default settings, while
a missing explicitly selected file raises `ValueError`.

`config.parse(value, *, root)` validates an in-memory unprefixed configuration
table against the same schema. Both factories resolve the consumer root,
reject unknown or malformed settings with `ValueError`, and perform no tool
installation or execution. File reads can raise `OSError`; invalid TOML raises
`tomllib.TOMLDecodeError`. Treat the returned settings as opaque: pass them to
documented APIs and use these factories to change configuration. Dataclass
constructors and internal attributes are not supported extension points.

```python
from pathlib import Path

from research_repo_tools.config import load
from research_repo_tools.toolchain_clean import plan_clean

settings = load(root=Path.cwd())
preview = plan_clean(settings, keep_roots=(Path("../another-consumer"),))
```

## External programs and platforms

Python 3.15+ is required. CI exercises Python 3.15 on Linux, macOS, and Windows,
including separate installations from the same wheel and source archive.
The 3.15 floor is required by explicit `lazy` import syntax at selected CLI/handler
boundaries. Global lazy imports are never enabled by the package. Later Python
versions are allowed by metadata but are not yet in the test matrix.
Installed-package checks cover these public imports and representative CLI use
outside the source checkout.

uv is a hard prerequisite and must be available on PATH. The runtime dependency
`rust-just` supplies Just in the project environment. The explicit `setup` command
also installs a persistent user-level Just command through uv and configures PATH.
Recipes select the locked project environment without activation. Setup then
synchronizes Python dependencies with the declared managed Rust tools available.
Explicit toolchain synchronization installs pinned
Rust/Cargo and supported declared Cargo tools, including git-cliff and rumdl.
See [toolchain setup](INSTALLING.md) for declarations, host support, installation
ownership, and remaining native validation gates. Git is a system prerequisite;
Semgrep belongs in the consumer's Python dependencies. GitHub CLI is needed for
automatic discovery of a previous release; an explicit previous release permits
offline preparation. The optional `notebooks` extra supplies notebook validation,
cleanup, synchronization, and execution. Notebook Python linting also requires
consumer-declared Ruff and ty; see [the notebook contract](RUNNING_NOTEBOOKS.md).

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
just templates CHANGELOG.md
just templates cliff.toml --owner example --repository consumer
just templates justfile
just templates research-repo-tools.toml
```

Templates print to stdout. `--output PATH` creates a file and refuses to replace
existing content. Generation uses the packaged git-cliff template by default,
so consumers need not maintain a copy.

Dependency and tool updates use the [update contract](UPDATING_DEPENDENCIES.md),
including supported source policies and rollback of multi-project changes.
See [changelog behavior](GENERATING_CHANGELOGS.md),
[release metadata](UPDATING_RELEASE_METADATA.md) and [migration](migration.md)
for the remaining capability-specific settings.
