# Installing and managing toolchains

Issue [#2](https://github.com/acgetchell/research-repo-tools/issues/2) is a
prerequisite for the first PyPI release. This implementation provides explicit
setup, checks, synchronization, and managed execution. uv is a hard prerequisite.
Native installer verification and isolated installed-package validation are
release gates; package unit tests alone do not close the issue. Migration of
existing consumer repositories is separate work after publication.

## Ownership and declarations

Consumers own their version declarations. The installed package owns how to
install and verify them. There are no repository-name profiles.

| Input | Authority |
| --- | --- |
| `[tool.uv].required-version` | Exact uv version, `==X.Y.Z` |
| `.python-version` | Managed Python selection, `3.MINOR` or `3.MINOR.PATCH`, at least 3.14 |
| `pyproject.toml` and `uv.lock` | Python dependencies and the pinned shared package |
| `rust-toolchain.toml` | Stable exact Rust release, components, targets, and minimal/default profile |
| `[tool.research-repo-tools.toolchain.cargo]` | Exact Cargo tool package versions |

uv must be at least 0.12.10 so the shared installer can enforce its Python
installation ownership policy with `--no-bin` and `--no-registry`.

For a Rust consumer, merge this structure into its existing manifest. Versions
below are examples, not automatic upgrades:

```toml
[dependency-groups]
tooling = ["research-repo-tools==0.1.0"]
dev = [{include-group = "tooling"}]

[tool.uv]
required-version = "==0.12.15"

[tool.research-repo-tools.toolchain.cargo]
git-cliff = "2.14.1"
```

Keep the consumer's other development dependencies in `dev` and its notebook
dependencies in a separate group. `dev` must include `tooling` so full environment
sync retains the shared package. Set `.python-version` to `3.14` (or a patch pin).
Its selection must satisfy `project.requires-python`.
Minor selectors such as `3.14` resolve within that constraint, including patch
minimums, upper bounds, and exclusions. Checks verify the resolved patch version.

Use the consumer's existing `rust-toolchain.toml`. Cargo includes its own version
within that Rust distribution; do not introduce an independent Cargo version pin.
The initial contract supports stable `X.Y.Z` toolchains and the `minimal` or
`default` profile; an omitted profile means `minimal`. Named channels, dated nightlies, custom paths, and the `complete`
profile are rejected explicitly. Python-only consumers can omit the Rust file and
Cargo tool table.

Supported Cargo packages are `git-cliff`, `rumdl`, `cargo-nextest`, `cargo-llvm-cov`,
`cargo-edit`, `dprint`, `taplo-cli`, `typos-cli`, and `zizmor`. Versions accept
canonical Cargo SemVer. `just` is supplied by the Python `rust-just` dependency;
do not declare another installation of it under Cargo. Other Python tools belong
in the consumer's dependency groups.

## First adoption and setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) separately
and make the declared version available on PATH. Setup never installs uv or
substitutes a private copy when the installed version differs.

The consuming project's maintainer adds the published package to `tooling`,
includes that group from `dev`, and refreshes the lockfile. Merge the packaged
just recipes into the existing justfile, or create one if the project has none:

```sh
uv add --group tooling --no-sync "research-repo-tools==0.1.0"
# After including tooling from dev and completing the declarations above:
uv lock
uv run --locked --only-group tooling research-repo-tools templates justfile --output justfile
```

The template command refuses to overwrite existing files; print the template
without `--output` when merging into an existing justfile. Commit the declarations,
lockfile, and recipes. No checkout of research-repo-tools or generated installer
scripts are needed. Installing the dependency alone does not run setup or modify
the consumer's source files.

Developers run one command from the configured consumer checkout on any supported
platform:

```sh
uv run --locked --managed-python --only-group tooling research-repo-tools setup
```

The tooling-only environment excludes the consumer project and its runtime or
notebook dependencies, so setup can start before a native build backend has Rust
available. uv supplies managed Python when needed. The command then:

1. Verifies the existing uv and installs the declared Python, Rust, and Cargo tools.
2. Installs the package's pinned `rust-just` through `uv tool install` in a persistent
   user environment and verifies that installation's executable.
3. Uses `uv tool update-shell` to configure the user's shell PATH.
4. Verifies prerequisites and synchronizes the Python environment, explicitly
   including `dev` even when uv defaults exclude it, with the managed Cargo paths.

Open a new terminal if PATH changed, then use `just help` and `just <recipe>`.
Recipes select the locked environment; activation is unnecessary. Setup can be
repeated with `just setup`. Failures stop the sequence; fix the reported cause and
rerun it. Setup does not rewrite declarations, locks, or existing recipes.
`uv tool update-shell` may modify shell startup files or the Windows user PATH.

## Routine recipes and CLI contract

The consumer template provides:

- `just changelog`: run the shared generator with the checked managed git-cliff.
- `just help`: list all available commands and arguments in lexicographic order.
- `just help-workflows`: alias for `help`.
- `just setup`: synchronize declared tools and then the default Python environment.
- `just tools-check`: inspect without syncing the environment or downloading Python.
- `just update`: upgrade uv, reconcile its pin, update Python pins and locks, and synchronize tools.

Bare `just` shows help. When merging the template, reconcile its default recipe
with the consuming repository's existing default.

After setup, use the recipes above directly without environment activation.
`just tools-check` checks the existing environment without synchronization or
Python downloads. Its recipe carries the read-only uv flags internally.

The underlying commands are:

| Command | Behavior |
| --- | --- |
| `setup` | Install user Just and declared tools, configure PATH, and sync the locked Python environment |
| `toolchain check [--json]` | Report expected/actual versions and selected paths; nonzero if incomplete |
| `toolchain run -- COMMAND ...` | Check tools, then run with their selected paths; propagate failure/exit status |
| `toolchain sync [--dry-run]` | Install declared versions and verify results; dry run reports without installation |

Global `--root` and `--config` retain the shared CLI contract. Toolchain commands
also read the conventional files at the consumer root. A standalone configuration
uses `[toolchain.cargo]`; uv/Python/Rust declarations stay in their authoritative
files. `toolchain run` does not invoke a shell or install missing tools. Adapt
other recipes that need Cargo tools to use this command, so they select the same
executables that checks verified. Direct bare Cargo tools on the user's PATH may
belong to a different installation.

Python commands prefer the consumer's verified virtual environment (`.venv`, or
the path selected by `UV_PROJECT_ENVIRONMENT`) so its installed dependencies remain
available. If that environment is missing or incompatible with the declarations,
the toolchain selects a compatible uv-managed interpreter.

## Installation ownership and failures

uv remains owned by its original installer. Just is installed in uv's user tool
location; uv configures PATH and refuses to overwrite executables owned by a
different installer. The package's runtime dependency also supplies Just inside
the project environment. The user-level command makes bare `just` available
without activating that environment.

Managed Rust/Cargo installations live under `~/.cache/research-repo-tools`,
overridden by an absolute `RESEARCH_REPO_TOOLS_HOME`. Python installation through
uv disables user-level executable aliases and Windows registry entries.

Rustup 1.29.1 is the package-owned installer version. Rustup/Cargo homes are isolated
by that version and host target. Cargo tool roots include host, Rust version,
package name, and package version. Distinct consumers can keep distinct pins;
sync never upgrades the selected release or rewrites the user's default Rust
toolchain. Old cache entries remain available until deliberately removed.

Installers run only during explicit setup or toolchain sync. Rustup uses its
versioned upstream binary and verifies the
published SHA-256 before execution. Cargo installs use exact versions and
`--locked`; their build dependencies come from each crate's published lockfile.
Install operations stream progress with a one-hour subprocess timeout. A failed
operation retains earlier successful tools and reports recovery guidance. Rerun
setup after fixing the cause; every installed executable is checked again.

The package does not install OS packages. Git must be available. Native Rust
builds may require Xcode Command Line Tools on macOS, a C/C++ toolchain and system
development libraries on Linux, or Visual Studio C++ Build Tools and a Windows SDK
on Windows. A successful version check is not a native compilation test; missing
linkers or libraries are diagnosed by the build/install operation. Initial host
support is x86_64/aarch64 macOS, glibc Linux, and Windows MSVC. Cross-compilation
linkers remain consumer/platform prerequisites even when Rust targets are present.

## Updates

Sync converges on declared versions; changing versions is a separate reviewed
operation. Edit Cargo pins or `rust-toolchain.toml`, then run setup and the
consumer's validation. Change the package pin with uv, refresh `uv.lock`, and
review its release notes, then rerun setup.

`just update` upgrades uv before the Python dependency update.
Standalone uv uses its official self-updater; Homebrew uv is upgraded through
Homebrew. Other installation owners are left to their package manager, with
uv's diagnostic propagated to the caller. An externally updated uv can run
`just update` even while the old project pin differs: that recipe starts the
already-installed package without syncing or enforcing the stale uv configuration.

Upgrading the user-level uv affects other checkouts using it. Their exact uv pins
must also be reconciled before running their locked commands. An installed uv
upgrade cannot be rolled back if
writing the project pin fails. Fix the reported cause and rerun `just update`.
The remaining `just update` steps retain declared Rust/Cargo versions and the
shared package pin; changing those remains an explicit reviewed operation.
The Python pin updater preserves included tooling-group pins as resolver
constraints; it updates only direct exact `dev` requirements.

The older `deps update-tools` command updates legacy Just variables from
user-installed Cargo tools. It does not manage this new TOML toolchain contract
and is not included in the new consumer template.

Upstream behavior: [uv installation](https://docs.astral.sh/uv/getting-started/installation/),
[user tools](https://docs.astral.sh/uv/concepts/tools/),
[managed Python](https://docs.astral.sh/uv/concepts/python-versions/),
[dependency groups](https://docs.astral.sh/uv/concepts/projects/sync/), and
[rustup installers](https://rust-lang.github.io/rustup/installation/other.html).
