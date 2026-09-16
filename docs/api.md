# Supported interfaces

The `0.1` series supports the `research-repo-tools` command, its documented TOML
configuration, packaged templates, and the two Python imports below. Consumers
should pin an exact released version in their uv development dependencies.
Patch releases preserve these contracts; a minor release may introduce a
documented breaking change while the package remains below `1.0`.

## Consumer just recipes

Use just recipes for routine repository workflows. The [packaged justfile
template](../src/research_repo_tools/templates/justfile) supplies thin wrappers
that consumers retain in their own justfiles:

```sh
just changelog
just changelog-archive
just release-check
```

Each recipe delegates to `uv run --locked research-repo-tools ...`, which selects
the consumer's locked package version. The shared implementation stays in the
installed package; the consumer owns the recipe that invokes it. If just is not
on `PATH`, or to select the locked `rust-just` executable explicitly, invoke the
recipe with `uv run --locked just ...`.

The template's `setup`, `tools-check`, `bootstrap`, and `bootstrap-check` recipes
implement the [shared toolchain contract](toolchain.md). Its changelog-generation
recipes use `toolchain run` to select the verified managed git-cliff. Other recipes
that invoke managed external tools should use the same execution wrapper.

These names describe the consumer template. This package's own [maintainer
justfile](../justfile) also has packaging-specific recipes; its `release-check`
requires a tag argument for the PyPI publication preflight.

## Command line and configuration

Use the [command overview](../README.md#common-workflows) and command-specific
help for the CLI arguments that recipes wrap. Direct invocation is useful for
inspecting that interface without adding a recipe for each help command:

```sh
uv run --locked research-repo-tools --help
uv run --locked research-repo-tools changelog generate --help
uv run --locked research-repo-tools release update --help
```

Place global `--root` and `--config` options before the command group. Configuration
defaults to `[tool.research-repo-tools]` in the consumer's `pyproject.toml`.
A standalone configuration uses unprefixed tables and `schema = 1`; print the
example with `research-repo-tools templates research-repo-tools.toml`. Unknown
settings fail. Relative paths and explicit executable paths resolve against the
consumer root; bare executable names use `PATH`.

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

Thin Python scripts can reuse the same command contract without a subprocess:

```python
from pathlib import Path

from research_repo_tools import __version__
from research_repo_tools.cli import main

root = Path(__file__).resolve().parents[1]
print(f"Using research-repo-tools {__version__}")
raise SystemExit(main(["--root", str(root), "release", "check"]))
```

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
See [toolchain setup](toolchain.md) for declarations, host support, installation
ownership, and remaining native validation gates. Git is a system prerequisite;
Semgrep belongs in the consumer's Python dependencies. GitHub CLI is needed for
automatic discovery of a previous release; an explicit previous release permits
offline preparation. Notebook infrastructure remains separate future work.
