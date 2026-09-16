# Supported interfaces

The `0.1` series supports the `research-repo-tools` command, its documented TOML
configuration, packaged templates, and the two Python imports below. Consumers
should pin an exact released version in their uv development dependencies.
Patch releases preserve these contracts; a minor release may introduce a
documented breaking change while the package remains below `1.0`.

## Consumer just recipes

The [packaged justfile template](../src/research_repo_tools/templates/justfile)
supplies thin wrappers that consumers retain in their own justfiles. Each recipe
selects the consumer's locked package version. The shared implementation stays
in the installed package; the consumer owns its configuration and invocation.

The [README command reference](../README.md#just-recipes) covers routine use.
The [toolchain guide](INSTALLING.md) defines managed execution, and the
[changelog guide](GENERATING_CHANGELOGS.md) describes generation and archiving.
Package development and publication preflight recipes belong to
[Contributing](../CONTRIBUTING.md#maintainer-commands).

## Command line and configuration

The [README](../README.md#direct-cli-examples) contains runnable examples and
help commands for inspecting CLI arguments.

Place global `--root` and `--config` options before the command group. Configuration
defaults to `[tool.research-repo-tools]` in the consumer's `pyproject.toml`.
A standalone configuration uses unprefixed tables and `schema = 1`, as shown in
the [packaged template](../src/research_repo_tools/templates/research-repo-tools.toml).
Unknown settings fail. Relative paths and explicit executable paths resolve
against the consumer root; bare executable names use `PATH`.

Commands return zero on success. Validation failures and handled operational
errors return nonzero; argument errors return `2`. Help and version output are
successful exits. Diagnostics use stderr, while reports and generated content
use stdout. Human-readable diagnostics and progress messages are not a structured
machine API. Template output and extracted release notes are intended for reuse.

File-changing commands operate only when invoked: dependency and release updates,
changelog generation/normalization/archiving, template output, and local tagging.
Dry runs are available only where command help lists them. Importing the package
does not install tools, access the network, or modify consumer files.

## Python entry point

Thin Python scripts can reuse the same command contract without a subprocess;
see the [README example](../README.md#calling-from-python).

- `research_repo_tools.__version__` is a string read from installed distribution
  metadata. `project.version` in this package's `pyproject.toml` is its authority.
- `research_repo_tools.cli.main(argv: list[str] | None = None) -> int` accepts
  arguments without the executable name. `None` uses the process arguments.
  It writes to the process stdout/stderr and returns the command status.
  Argument parsing raises `SystemExit(0)` for help/version and `SystemExit(2)`
  for usage errors. Unexpected programming errors may propagate.

Other module functions, configuration objects, parsers, and constants are
implementation details in `0.1`; importing them creates an unsupported dependency.
Add a shared library API deliberately when consumers need richer return values.
The existing `py.typed` marker supplies typing information, not a stability promise
for every importable symbol.

## External programs and platforms

Python 3.14+ is required. CI exercises Python 3.14 on Linux, macOS, and Windows,
including separate installations from the same wheel and source archive.
Later Python versions are allowed by metadata but are not yet in the test matrix.
Installed-package checks cover these public imports and representative CLI use
outside the source checkout.

The runtime dependency `rust-just` supplies `just`. Generated bootstrap launchers
obtain uv and managed Python. Explicit toolchain synchronization installs pinned
Rust/Cargo and supported declared Cargo tools, including git-cliff and rumdl.
See [toolchain setup](INSTALLING.md) for declarations, host support, installation
ownership, and remaining native validation gates. Git is a system prerequisite;
Semgrep belongs in the consumer's Python dependencies. GitHub CLI is needed for
automatic discovery of a previous release; an explicit previous release permits
offline preparation. Notebook infrastructure remains separate future work.
