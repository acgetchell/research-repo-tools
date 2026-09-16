# Shared toolchain setup

Issue [#2](https://github.com/acgetchell/research-repo-tools/issues/2) is a
prerequisite for the first PyPI release. This implementation provides explicit
checks, synchronization, managed execution, and generated uv bootstrap launchers.
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

## First adoption and bootstrap generation

A consuming project's maintainer uses uv to add the published package to
`tooling`, includes that group from `dev`, and refreshes the lockfile. No checkout
of research-repo-tools is needed: the installed package supplies the commands and
templates. Package contributors can use a built wheel in a disposable fixture for
pre-publication validation.

Install the package's just template into a new consumer, or merge its small
recipes into the existing justfile. Generate the two launchers through its
`bootstrap` recipe:

```sh
uv run --locked --only-group tooling just bootstrap
```

This creates `bootstrap.sh` and `bootstrap.ps1` in the consumer root. Commit these
generated files with the manifest and lockfile. They embed the uv pin derived
from the manifest; `just bootstrap-check` detects stale content. Regeneration
refuses to replace different existing files unless the maintainer explicitly
passes `toolchain bootstrap --force`. Symlinks are rejected.

Keep their bytes consistent across Git checkouts by adding these entries to the
consumer's `.gitattributes`:

```gitattributes
/bootstrap.sh text eol=lf
/bootstrap.ps1 text eol=lf
```

On a new machine, after obtaining the consumer checkout, run either:

```sh
sh bootstrap.sh
```

or, from Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

The launcher obtains a matching uv executable, then uses
`uv run --locked --managed-python --only-group tooling` to start the shared
installer. uv downloads the selected Python when needed. The consumer project
and notebook dependencies are excluded at this stage. After tool installation,
the launcher synchronizes the full Python environment through the managed Rust
environment, allowing native extensions to find the selected Cargo toolchain.
Failures stop the sequence immediately. Neither launcher edits declarations,
updates locks, authenticates services, or modifies shell startup files.

## Routine recipes and CLI contract

The consumer template provides:

- `just setup`: synchronize declared tools and then the default Python environment.
- `just tools-check`: inspect without syncing the environment or downloading Python.
- `just bootstrap`: generate the launchers.
- `just bootstrap-check`: verify launchers match the declared uv pin.
- `just changelog`: run the shared generator with the checked managed git-cliff.

Use `uv run --locked just ...` when just is not already on PATH. For a strictly
read-only check of an existing environment, use
`uv run --locked --no-sync --no-python-downloads just tools-check`; the outer
invocation must not synchronize before the check starts.

The underlying commands are:

| Command | Behavior |
| --- | --- |
| `toolchain check [--json]` | Report expected/actual versions and selected paths; nonzero if incomplete |
| `toolchain sync [--dry-run]` | Install declared versions and verify results; dry run reports without installation |
| `toolchain run -- COMMAND ...` | Check tools, then run with their selected paths; propagate failure/exit status |
| `toolchain bootstrap [--check \| --force]` | Generate, verify, or explicitly refresh the two launchers |

Global `--root` and `--config` retain the shared CLI contract. Toolchain commands
also read the conventional files at the consumer root. A standalone configuration
uses `[toolchain.cargo]`; uv/Python/Rust declarations stay in their authoritative
files. `toolchain run` does not invoke a shell or install missing tools. Adapt
other recipes that need Cargo tools to use this command, so they select the same
executables that checks verified. Direct bare Cargo tools on the user's PATH may
belong to a different installation.

## Installation ownership and failures

Managed installations live under `~/.cache/research-repo-tools`, overridden by an
absolute `RESEARCH_REPO_TOOLS_HOME`. A matching existing uv can be reused.
Otherwise a versioned private uv installation is created. An incompatible
Homebrew, system, or other user installation is left in place. Explicit Python
installation disables user-level executable aliases and Windows registry entries.

Rustup 1.29.1 is the package-owned installer version. Rustup/Cargo homes are isolated
by that version and host target. Cargo tool roots include host, Rust version,
package name, and package version. Distinct consumers can keep distinct pins;
sync never upgrades the selected release or rewrites the user's default Rust
toolchain. Old cache entries remain available until deliberately removed.

Installers run only during explicit sync or bootstrap. uv uses its versioned
official installer. Rustup uses its versioned upstream binary and verifies the
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

## Updates and provenance

Sync converges on declared versions; changing versions is a separate reviewed
operation. Edit Cargo pins or `rust-toolchain.toml`, then run setup and the
consumer's validation. Change the package pin with uv, refresh `uv.lock`, and
review its release notes. Regenerate both launchers after changing the uv pin.
The Python pin updater preserves included tooling-group pins as resolver
constraints; it updates only direct exact `dev` requirements.

The older `deps update-tools` command updates legacy Just variables from
user-installed Cargo tools. It does not manage this new TOML toolchain contract
and is not included in the new consumer template.

The contract was informed by the shared needs and existing setup recipes in
markov-chain-monte-carlo. This is a new implementation with synthetic fixtures.
It deliberately replaces global Cargo installation, mismatched-version checks
scattered through justfiles, and unpinned setup helpers with one explicit contract.
No consumer setup scripts or scientific workloads are copied into the package.

Upstream behavior: [uv installers](https://docs.astral.sh/uv/getting-started/installation/),
[managed Python](https://docs.astral.sh/uv/concepts/python-versions/),
[dependency groups](https://docs.astral.sh/uv/concepts/projects/sync/), and
[rustup installers](https://rust-lang.github.io/rustup/installation/other.html).
