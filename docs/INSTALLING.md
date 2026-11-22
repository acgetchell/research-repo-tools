# Installing and managing toolchains

For a first installation, use the [consumer quickstart](../README.md#install-with-uv).
To change an existing pin, follow [shared Python adoption](UPDATING_DEPENDENCIES.md#shared-python-adoption).
Use this guide to configure optional tools, understand what setup installs, or recover a failed installation.

## Ownership and declarations

Install the matching Justfile and `python-bootstrap.py` from the pinned package.
The [Python adoption guide](UPDATING_DEPENDENCIES.md#shared-python-adoption)
covers interpreter selection and application support bounds. To inherit Ruff,
ty and pytest versions, opt into the [shared Python tool profile](UPDATING_DEPENDENCIES.md#shared-python-tool-versions).

| Input | Authority |
| --- | --- |
| `[tool.uv].required-version` | Exact uv version, `==X.Y.Z` |
| `.python-version` | Managed development selection, `3.MINOR` or `3.MINOR.PATCH`, compatible with the published minimum and consumer constraints |
| `[project].requires-python` | Application support range intersected with the installed release's minimum; stricter bounds and exclusions retained |
| `pyproject.toml` and `uv.lock` | Python dependencies and the pinned shared package |
| `rust-toolchain.toml` | Stable exact Rust release, components, targets, and minimal/default profile |
| `[tool.research-repo-tools.toolchain.cargo]` | Exact Cargo tool package versions |
| `[tool.research-repo-tools.toolchain.binaries]` | Exact dprint, Gitleaks, OSV-Scanner, and rumdl release versions |

uv must be at least 0.12.10 so the shared installer can enforce its Python
installation ownership policy with `--no-bin` and `--no-registry`.

For a Rust consumer, start with the published package pin and dependency groups
in the [consumer quickstart](../README.md#install-with-uv), then merge the tool
declarations below into the existing manifest. The versions are examples.

```toml
[tool.uv]
required-version = "==0.13.9"

[tool.research-repo-tools.toolchain.cargo]
git-cliff = "2.14.2"
```

Keep the consumer's other development dependencies in `dev` and its notebook
dependencies in a separate group. `dev` must include `tooling` so full environment
sync retains the shared package. Set `.python-version` to `3.15` (or a patch pin).
Its selection must satisfy `project.requires-python`.
Minor selectors such as `3.15` resolve within that constraint, including patch
minimums, upper bounds, and exclusions. Checks verify the resolved patch version.

Use the consumer's existing `rust-toolchain.toml`. Cargo includes its own version
within that Rust distribution; do not introduce an independent Cargo version pin.
The contract supports stable `X.Y.Z` toolchains and the `minimal` or
`default` profile; an omitted profile means `minimal`. Named channels, dated nightlies, custom paths, and the `complete`
profile are rejected explicitly. Python-only consumers can omit the Rust file and
Cargo tool table.

Supported Cargo packages are `cargo-audit`, `cargo-deny`, `cargo-edit`, `cargo-llvm-cov`,
`cargo-machete`, `cargo-nextest`, `clippy-sarif`, `dprint`, `git-cliff`, `rumdl`, `samply`, `sarif-fmt`,
`taplo-cli`, `tectonic`, `tex-fmt`, `typos-cli`, and `zizmor`. Versions accept
canonical Cargo SemVer. `just` is supplied by the Python `rust-just` dependency;
do not declare another installation of it under Cargo. Other Python tools belong
in the consumer's dependency groups.

Optional `dprint`, `gitleaks`, `osv-scanner`, and `rumdl` release binaries use isolated host/version
directories, upstream GitHub release-asset SHA-256 checksums, shared archive
validation, and executable version probes before atomic file replacement.
GitHub release-metadata requests optionally authenticate with `GITHUB_TOKEN`,
falling back to `GH_TOKEN`; requests remain unauthenticated when neither is set.
Use the supported `toolchain sync-binaries` command for authenticated installation
without package synchronization or dependency builds. The
[GitHub Actions integration](#authenticated-release-installation-in-github-actions)
keeps locked package installation and ordinary setup in separate steps.
Setup commands and toolchain probes remove both lookup variables from child
environments; explicitly requested `toolchain run` commands retain them.
Other build settings remain inherited. Release-installation diagnostics
redact both credential values, and asset downloads remain unauthenticated.
Missing checksums, unsupported assets and mismatched versions fail before
publication. Setup, check, run, export and upgrade use these same managed paths;
checks do not install or fall back to ambient executables. Both architectures of
the supported macOS, glibc Linux and Windows hosts have explicit asset mappings,
except native Windows ARM64 rumdl, which requires its explicit Cargo owner.
See [scanner commands](RUNNING_CHECKS.md#dependency-and-secret-scanning) for scope,
redaction, reports and consumer-owned exceptions.

## First adoption and setup

Managed Cargo installs reuse compilation output under
`RESEARCH_REPO_TOOLS_HOME/cargo-build/<host>/<Rust version>` (the default home is
`~/.cache/research-repo-tools`). Package versions retain separate installation
directories and are still installed with exact pins, `--locked`, and executable
verification. Cargo fingerprints distinguish package versions, features, and
compiler flags within the build cache. A warm cache never replaces an install check.

Set `RESEARCH_REPO_TOOLS_CARGO_BUILD_HOME` to an absolute directory to retain
compilation separately from installation homes, including disposable CI setups.
Host and Rust-version subdirectories are added automatically. This affects only
managed tool installation; project builds keep their own target directories.
Cache only this build directory in CI, key it by runner OS/architecture and
toolchain/tool declarations, and preserve the CI provider's branch trust boundaries.
The compilation cache is disposable; remove it with no builds running to reclaim
space or force recompilation. `just clean` retains it while pruning obsolete installs.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) separately
and make the declared version available on PATH. Setup never installs uv or
substitutes a private copy when the installed version differs.

The packaged Just recipes require a working POSIX `sh` on PATH on every platform.
On Windows, install Git for Windows and add its `bin` directory containing
`sh.exe` to PATH (typically `C:\Program Files\Git\bin`). Having `git.exe` on
PATH alone is insufficient. Setup checks the shell before installing tools;
`just tools-check` includes the same read-only shell check.

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
Recipes explicitly select the locked `dev` group, including when
`tool.uv.default-groups` excludes it; activation is unnecessary. Setup can be
repeated with `just setup`. Failures stop the sequence; fix the reported cause and
rerun it. Setup does not rewrite declarations, locks, or existing recipes.
`uv tool update-shell` may modify shell startup files or the Windows user PATH.

## Routine recipes and CLI contract

After setup, run `just tools-check` to inspect the existing environment without
syncing or downloading Python. Run `just tools-sync-binaries` to repair only
release binaries. Bare `just` and `just help` list the installed recipes. When
merging the template, reconcile its default recipe with the consumer's existing
default. See the [consumer command reference](RUNNING_COMMANDS.md#just-recipes)
for the full catalog.

The underlying commands are:

| Command | Behavior |
| --- | --- |
| `clean [--dry-run \| --apply] [--keep-root PATH]...` | Compose managed-installation and retained-coverage cleanup |
| `coverage clean [--dry-run \| --apply]` | Preview/remove retained coverage shards and runs, preserving current database and reports |
| `setup` | Install user Just and declared tools, configure PATH, and sync the locked Python environment |
| `toolchain adopt (--dry-run \| --apply)` | Plan or apply migration to the executing shared package and its mandatory Python minimum, plus enabled development mirrors |
| `toolchain check [--tool NAME]... [--json]` | Report expected/actual versions and selected paths; nonzero if incomplete |
| `toolchain clean [--dry-run \| --apply] [--keep-root PATH]...` | Preview obsolete package-owned installations; explicitly apply removals while retaining the selected consumers |
| `toolchain export [--tool NAME]... [--file PATH]` | Verify tools and append their environment to an explicit file or `GITHUB_ENV` |
| `toolchain python-check` | Enforce the installed shared Python minimum and check enabled development mirrors |
| `toolchain python-tools-check` | Reject inherited tool declaration, lock, and executable drift without synchronization |
| `toolchain run [--tool NAME]... -- COMMAND ...` | Check tools, then run with their selected paths; propagate failure/exit status |
| `toolchain sync [--tool NAME]... [--dry-run]` | Install declared versions and verify results; dry run reports without installation |
| `toolchain sync-binaries` | Verify and repair only pinned release binaries; no uv prerequisite probe, package synchronization, or dependency builds |
| `toolchain upgrade [--dry-run]` | Resolve stable managed Cargo and release-binary upgrades, install and verify them, then publish the exact pins |

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

## Selecting tools for a job

A narrow CI job can select exact keys from its existing tool catalog. After
one-time setup and adopting the matching Just template, use the same repeatable
`--tool` selection for setup, verification, export and execution. For example,
with a declared `zizmor` pin:

```sh
just tools-sync --tool zizmor
just tools-check --tool zizmor
just tools-export --tool zizmor
just tools-run --tool zizmor -- zizmor .github/workflows
```

Export appends checked assignments to `GITHUB_ENV`; locally pass `--file PATH`.
Repeat `--tool` for multiple tools. `--tool cargo` selects only pinned minimal
Rust/Cargo for packaging; `--tool cargo-edit` selects the package providing
`cargo upgrade`. Binary selections such as `gitleaks` need no Rust; Cargo tools
add minimal Rust without the catalog's extra components or targets. Omission
keeps full contributor behavior. Selection does not install native libraries or
synchronize project Python groups; retain those prerequisites in the owning job.

## Installation ownership and failures

uv remains owned by its original installer. Just is installed in uv's user tool
location; uv configures PATH and refuses to overwrite executables owned by a
different installer. The package's runtime dependency also supplies Just inside
the project environment. The user-level command makes bare `just` available
without activating that environment.

Managed installations live under `~/.cache/research-repo-tools`, overridden by an
absolute `RESEARCH_REPO_TOOLS_HOME`. New Python installs default to its `python`
subdirectory; an explicit `UV_PYTHON_INSTALL_DIR` remains honored. Existing
compatible project environments remain usable. Setup and cleanup preserve
installations in uv's user-wide store. Explicit Python installation through uv
disables user-level executable aliases and Windows registry entries.

On Windows, keep a custom `RESEARCH_REPO_TOOLS_HOME` short: Rust adds nested
toolchain and library directories, and native build tools can still hit the
[Windows path-length limit](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation).
If a linker reports a library path that exceeds that limit, select a shorter
absolute cache path and rerun setup.

Rustup 1.29.1 is the package-owned installer version. Rustup/Cargo homes are isolated
by that version and host target. Cargo tool roots include host, Rust version,
package name, and package version. Distinct consumers can keep distinct pins;
sync never upgrades the selected release or rewrites the user's default Rust
toolchain. Old cache entries remain available until deliberately removed.

## Cleaning obsolete installations

`just clean` previews obsolete installations and [retained coverage](#cleaning-retained-coverage);
`just clean --apply` performs removals after checking the inventories and declarations
again. The CLI `toolchain clean` keeps its installation-only scope. Neither mode installs tools. Both use
the locked project environment without synchronizing it or downloading Python.
Run setup first if that environment is missing.

The store can be shared across repositories. Pass `--keep-root PATH` for each
other consumer whose declarations must be retained, in both preview and apply.
There is no automatic registry of consumer repositories. Additional roots use
their `pyproject.toml` configuration and conventional Python/Rust selectors;
invalid roots fail before any removal. Paths containing spaces are supported.
Relative retention roots resolve against the configured consumer root, including
when `--root` selects a different directory from the invoking shell.

Retention is conservative:

- Cargo tools and release binaries are eligible only when older than every
  retained pin for that package. Cargo copies built with an older Rust compiler
  are also eligible when their package version is no newer than those pins.
  Same-release Cargo prerelease/build variants remain with the current compiler.
- Stable Rust toolchains older than all selected Rust pins are uninstalled using
  the package's private rustup home. Its default toolchain is retained. Other
  rustup manager versions, host targets and custom toolchains remain intact.
- CPython cleanup considers only the private `python` store and current host.
  Each minor selector retains its newest compatible installed patch; exact
  selectors retain that patch. Versions older than all these selections can be
  removed. Missing selections fall back to the declared version as a conservative
  cutoff. The running interpreter, selected roots' `.venv` interpreters, active
  or explicitly selected environments, and uv tool interpreters are protected.
  Removal deletes only selected private directories and their uv minor-version
  symlinks/junctions. Targets of unrecognized aliases are preserved. It does not
  invoke uv's user alias or registry cleanup.
  Other architectures, variants and interpreters outside the private store stay.

Unknown packages, newer versions, user-wide installations, external Python
stores, project environments, build artifacts, and download/dependency caches
are outside tool-installation cleanup. Linked or junction-backed installation boundaries are
rejected. Close processes using obsolete tools before applying cleanup,
especially on Windows where open executables can prevent removal. Do not install
tools or edit declarations concurrently. Cleanup stops on the first failure;
earlier removals remain applied. Correct the cause and preview again.

## Cleaning retained coverage

The consumer template exports `clean-coverage` and includes the same shared
cleanup in `clean`. Upgrade the package pin and lock, then merge these two recipes
from its exported Justfile; upgrading the package alone does not replace existing
recipes. The CLI entry points are `coverage clean [--dry-run | --apply]` and
`clean [--dry-run | --apply] [--keep-root PATH]...`.

```sh
just clean-coverage
just clean-coverage --apply
```

Stop coverage writers before applying cleanup. Preview is the default, reports
selected file bytes, and makes no changes. Cleanup selects regular root
`.coverage.*` files and direct `coverage/failed-*` or `coverage/.run-*` directories
containing only `.coverage`, `.coverage.*`, staged `cobertura.xml`, or pytest's
`pytest.xml` failure report. Empty matching directories are also removed. This is
a fixed shared convention; custom
data paths and Rust coverage output remain consumer-owned.

The root `.coverage`, current `coverage/cobertura.xml`, HTML reports and unrelated
files stay. Matching run directories with unfamiliar or nested contents are
reported and preserved in full. Selected symlink/junction boundaries are refused.
Retained failed-run shards may be useful for diagnosis; apply only after deciding
they are no longer needed.

Application rechecks the complete inventory before deletion and rejects changes.
It does not lock coverage processes or establish that a run has stopped. Removal
uses individual files followed by empty directories, never a recursive sweep.
An I/O failure stops cleanup, reports that earlier removals remain applied, and
requires a fresh preview after resolving the cause. Aggregate cleanup plans both
capabilities before deleting anything, then removes tools followed by coverage;
it does not roll back successful deletions if a later operation fails.
`--keep-root` retains other consumers' tool pins; it does not clean their coverage.

## Installing and recovering tools

Installers run only during explicit setup, toolchain sync, binary-only sync, or toolchain upgrade. Rustup uses its
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
linkers or libraries are diagnosed by the build/install operation. Supported hosts
are x86_64/aarch64 macOS, glibc Linux, and Windows MSVC. Cross-compilation
linkers remain consumer/platform prerequisites even when Rust targets are present.

### Additional Cargo tools and native prerequisites

All catalog entries use the same exact `cargo install --locked` contract and
post-install executable verification. The version probes for the additional tools
are listed below; probes do not run an audit, dependency scan, profiler, typesetter,
or formatter.

| Package | Direct executable probe | Expected output |
| --- | --- | --- |
| `cargo-audit` | `cargo-audit --version` | `cargo-audit X.Y.Z` |
| `cargo-deny` | `cargo-deny --version` | `cargo-deny X.Y.Z` |
| `cargo-machete` | `cargo-machete --version` | `X.Y.Z` |
| `clippy-sarif` | `clippy-sarif --version` | `clippy-sarif X.Y.Z` |
| `samply` | `samply --version` | `samply X.Y.Z` |
| `sarif-fmt` | `sarif-fmt --version` | `sarif-fmt X.Y.Z` |
| `tectonic` | `tectonic --version` | `Tectonic X.Y.Z` |
| `tex-fmt` | `tex-fmt --version` | `tex-fmt X.Y.Z` |

The [Clippy converter](https://docs.rs/crate/clippy-sarif/0.8.0) and
[SARIF formatter](https://docs.rs/crate/sarif-fmt/0.8.0) use ordinary Cargo builds
on the supported macOS, glibc Linux, and Windows MSVC hosts. Native setup CI
installs the exact `0.8.0` pins, exercises a small JSON diagnostic through both
managed executables, and verifies converter failures on all three platforms,
including Ubuntu for Code Scanning consumers. Platform support for a release
requires these native jobs to pass; synthetic probes alone are insufficient.
The compiler's Clippy component must be declared separately. SARIF uploading,
Clippy arguments, and pipeline failure policy remain consumer-owned; see the
[consumer example](RUNNING_CHECKS.md#clippy-sarif-helpers). Upstream `clippy-sarif` ignores
unrecognized input lines, so successful conversion alone does not validate every
Cargo diagnostic or establish that Clippy succeeded.

The platform compiler/SDK prerequisites above apply to these builds.
[Tectonic additionally needs native font, Unicode, compression, and TLS libraries](https://tectonic-typesetting.github.io/book/latest/howto/build-tectonic/).
Provision them before setup: its upstream guide covers
[system packages and discovery through pkg-config or vcpkg](https://tectonic-typesetting.github.io/book/latest/howto/build-tectonic/external-dep-install.html).
On macOS, configure pkg-config to find Homebrew's libraries; on glibc Linux,
provide the development packages; on Windows MSVC, configure the matching vcpkg
libraries and `TECTONIC_DEP_BACKEND=vcpkg`. Environment settings for native
dependency discovery pass through to Cargo. This package uses upstream default
Cargo features and does not install OS libraries or run cargo-vcpkg automatically.
A failed build retains previous tool declarations and installations; provision
the missing dependencies and retry. A missing dynamic library also fails the
version probe, so an unusable executable never counts as a verified installation.

[Samply supports macOS, Linux, and Windows](https://github.com/mstange/samply).
Profiling permissions, including Linux perf access, remain consumer/platform
configuration; setup never changes kernel settings or grants privileges.
Similarly, cargo-audit's advisory database access, Tectonic's document bundles,
and the actual profiling/typesetting workflows are consumer-owned. A successful
version probe establishes executable availability, not those workflows' success.

## Updates

Run setup to install or repair the declared versions. To change an exact pin,
follow the [uv and managed tool upgrade procedure](UPDATING_DEPENDENCIES.md#upgrading-uv-and-managed-tools).
Consumers with user-wide Cargo or Homebrew tools use the
[user-installed tool recipes](UPDATING_DEPENDENCIES.md#user-installed-tool-updates).
To upgrade the shared package itself, follow [shared Python adoption](UPDATING_DEPENDENCIES.md#shared-python-adoption).

Upstream behavior: [uv installation](https://docs.astral.sh/uv/getting-started/installation/),
[user tools](https://docs.astral.sh/uv/concepts/tools/),
[managed Python](https://docs.astral.sh/uv/concepts/python-versions/),
[dependency groups](https://docs.astral.sh/uv/concepts/projects/sync/), and
[rustup installers](https://rust-lang.github.io/rustup/installation/other.html).

## Authenticated release installation in GitHub Actions

`toolchain sync-binaries` installs the exact release-binary pins declared by the
consumer (dprint, gitleaks, osv-scanner, and rumdl). It checks warm caches and repairs damaged
executables through the same SHA-256-verified installer as setup. It does not
install Python, Rust, Cargo tools, Just, or project dependencies.

Install the locked tooling package first, then authenticate binary installation
in a separate step. This example uses an existing uv installation and the
consumer's committed manifest and lockfile:

```yaml
- name: Install locked tooling package
  run: uv sync --locked --managed-python --only-group tooling
- name: Install pinned release binaries
  env:
    GITHUB_TOKEN: ${{ github.token }}
  run: uv run --locked --no-sync --no-python-downloads python -I -X utf8 -m research_repo_tools toolchain sync-binaries
- name: Set up development tools and dependencies
  run: uv run --locked --managed-python --only-group tooling research-repo-tools setup
```

The credential-bearing step uses the already installed package without
synchronizing its environment. Release metadata requests authenticate with the
first nonempty `GITHUB_TOKEN`, then `GH_TOKEN`; asset downloads carry no lookup
credential. Setup commands and all toolchain version probes receive neither
variable, and handled release-installation diagnostics redact both values.
An explicit `toolchain run -- COMMAND ...` retains the caller's credentials for
that command. Other native build and package-index settings continue to pass
through. After setup, use `just tools-sync-binaries` for binary-only repair.

To reclaim obsolete managed Cargo tools, Rust toolchains, release binaries, and
Python interpreters, use the shared `clean` recipe. Include every other consumer
whose installed pins you want to retain:

```sh
just clean --keep-root ../la-stack
just clean --apply --keep-root ../la-stack
```

The default is a preview. Cleanup preserves current declarations, newer versions,
unknown tools, other hosts, and Python interpreters referenced by the selected
consumers' environments or uv tools. It only removes installations inside the
package-owned store; user-wide Cargo, rustup and uv Python installs, project
`.venv`, build artifacts, and dependency caches remain intact. See the
[cleanup contract](INSTALLING.md#cleaning-obsolete-installations) for retention
rules and Windows guidance. Consumers copy the packaged recipe and pin the shared
package; the cleanup implementation lives here.

## Reproducible isolated release runtimes

The stronger runtime-policy capability selects a **dedicated runtime lock**,
independent of the consumer's development lock. A reviewed manifest binds the
complete installed dependency graph, exact wheel bytes, Python/platform, and
any required native source-build evidence. The [isolated tooling wrapper](UPDATING_DEPENDENCIES.md#development-pins-and-release-dependencies)
continues to resolve its transitive dependencies independently; `--locked` with
`--no-project` does not turn that environment into a locked runtime.

Use a trusted bootstrap installation outside the consumer checkout, with the
matching `release-runtime` recipe from the shipped Justfile. With v0.3.1 installed,
resolve the dedicated project there:

```sh
just release-runtime resolve --directory /trusted/runtime-project --require 'research-repo-tools==0.3.1'
```

Keep uv as a prerequisite. Add these bootstrap recipes alongside the trusted
Justfile with positional arguments enabled. `runtime-sync` installs only wheels
from the lock. `runtime-builds` supports the existing explicit native-build
policy, using the bootstrap interpreter rather than executing the target venv:

```just
runtime-builds project:
    uv run --locked research-repo-tools --root "$1" release dependency-builds

runtime-lock project:
    uv lock --no-config --no-build --project "$1"

runtime-sync project *args:
    runtime_project="$1"; shift; uv sync --locked --no-config --no-default-groups --no-build --project "$runtime_project" "$@"
```

For a wheel-only graph, run `just runtime-sync /trusted/runtime-project`. If
native source builds are required, first configure the dedicated project's
`dependency-sources.toml` and build group using the
[source-build contract](UPDATING_DEPENDENCIES.md#source-built-python-dependencies). Run
`just runtime-lock /trusted/runtime-project`, then `just runtime-sync` with
that project and `--no-install-package NAME` for each selected source build.
This creates the venv and installs the remaining locked dependencies. Then run
`just runtime-builds /trusted/runtime-project` to build/install the selected
native wheels and authenticate their evidence.
Record from the separate bootstrap installation; do not launch the target venv
or compile bytecode before recording:

```sh
just release-runtime record --project /trusted/runtime-project --output /trusted/runtime-bundle
```

Review the dedicated lock, `runtime.json`, retained wheels and native receipts/logs.
Pin the printed SHA-256 in protected workflow configuration or another independently
authenticated handoff. A digest supplied alongside an untrusted bundle is not
authentication. Transfer the bundle, **never the machine-local signing key**.
The selected Python patch version and platform must match on the receiving host:

```sh
just release-runtime install --policy /trusted/runtime-bundle/runtime.json --sha256 APPROVED_SHA256 --directory /trusted/writer
just release-runtime verify --policy /trusted/runtime-bundle/runtime.json --sha256 APPROVED_SHA256 --environment /trusted/writer
```

Installation uses the approved manifest's hashes and retained wheels offline,
then verifies the installed graph; it does not trust an editable requirements file.
An existing destination is never overwritten. If installation fails, preserve it
for inspection and retry with a new directory after repairing the reported input.

Launch release writers through a recipe using the exact installed interpreter,
`-I -B`, and the approved manifest. On Windows, select `Scripts/python.exe` in
place of `bin/python`. For example, define the following in the trusted Justfile:

```just
[positional-arguments]
runtime-release python policy digest root +args:
    runtime_python="$1" runtime_policy="$2" runtime_digest="$3" runtime_root="$4"; \
    shift 4; \
    "$runtime_python" -I -B -m research_repo_tools --root "$runtime_root" release \
        --isolated-runtime --runtime-policy "$runtime_policy" --runtime-policy-sha256 "$runtime_digest" "$@"
```

Pass the existing `assets`, `evidence` or other release arguments unchanged.
The same prefix supports candidate/native evidence, authenticated draft preparation,
and checkout-free attachment writers. Keep standalone controller configuration
outside the observed checkout and pass its existing `--checkout` argument where
required. The Python API accepts `policy=` and `artifacts=` on
`isolated_release_runtime`; [runtime policy API](runtime-policy-api.md)
describes all public functions and trust boundaries.
