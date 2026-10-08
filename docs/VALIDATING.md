# Validating the package

This guide describes how to validate the current package. Fixes and release
history belong in the generated [CHANGELOG.md](../CHANGELOG.md). See
[Contributing](../CONTRIBUTING.md#generated-changelog) for its generation workflow.

## Local checks

| Command | Purpose |
| --- | --- |
| `just audit` | Check locked third-party Python dependencies against online vulnerability advisories |
| `just changelog-test` | Require the pinned git-cliff version and exercise generator contracts |
| `just check` | Lock, Justfile, Python/newline, and workflow checks during development |
| `just check-dist-changelog` | Exercise real CLI generation from existing wheel and sdist installations |
| `just ci` | Final checks, tests, wheel/sdist builds, and isolated installation checks |
| `just coverage` | Run tests with branch and subprocess coverage; write `coverage/cobertura.xml` |
| `just help` | List recipes and arguments in lexicographic order, with aliases inline |
| `just justfile-check` | Check maintainer and packaged recipes with the pinned Just formatter |
| `just lock-check` | Verify the manifest and lock agree without changing dependencies |
| `just newline-check` | Reject implicit newline translation in Python text-file writes |
| `just python-check` | Check lint, formatting, and types for the complete Python inventory |
| `just test` | Run the test suite |

Run the [contributor setup](../CONTRIBUTING.md#development-environment)
before using these recipes. Environment activation is not required.
Run focused regressions when a behavioral change needs verification, then
`just ci` when the work is ready for review. A passing `just check` does not
establish that tests, builds, or installation checks passed.

`just check` checks the lock, Justfiles, and Python policies before workflow
audits. `just justfile-check` validates both the maintainer recipes and the
packaged consumer template; their pinned formatter is installed during setup.
The repository's `.gitattributes` preserves LF for both files on Windows checkouts.
`just ci` shares the lock check between validation and building.

`just check` includes `just newline-check`, so both local `just ci` and every
native package job enforce explicit newline policies. The guard's positive and
negative fixtures verify detection; byte assertions exercise mixed LF/CRLF data
on the native host and a focused Windows translation model. See the
[contributor guidance](../CONTRIBUTING.md#shared-implementation) for the syntax
check's scope and limits. Native Windows validation remains required.

`just audit` excludes the local package and evaluates dependency markers on the
current platform. It does not audit every platform's conditional dependencies.

## Package and platform coverage

Tests exercise the shared implementation using small synthetic fixtures grouped
by capability. They do not use copied consumer repositories or old implementations
as the shared package's test suite.

Installation checks run outside the checkout. They verify shipped imports, the
console entry point, packaged templates, runtime dependencies, licenses,
and the bundled just executable. They also exercise a locked tooling-only
environment before the consumer project's native build backend is available,
including setup startup and its missing-uv failure. These checks do not install
user tools or modify shell profiles.
They run the installed Just template against a consumer with default dependency
groups disabled, verifying that setup and mutating workflows restore their
required groups. Python checks use an existing synchronized environment.
Offline update fixtures verify exact dev-pin upgrades, retained ranges and
markers, full-lock upgrades when direct pins are already current, and explicit
dev synchronization. Ambiguous universal resolutions must leave both the
manifest and lock unchanged.
Maintenance-only installations must not contain notebook dependencies. Each
distribution is also installed with its notebook extra through a locked consumer
group, then checked with project-kernel synchronization, native Ruff/ty linting,
and real notebook execution. Installed recipes cover both the default notebook
group and a custom group, including output cleanup and opt-in cell-ID patterns.
Base-package installed consumers exercise locked JupyterLab launch with a
stand-in server, private native caches, browser policy, and exit propagation.
Git-enabled fixtures verify source restoration from index and explicit revisions,
deleted tracked notebooks, literal filenames, index preservation, and declared
cleanup. These reset fixtures honor the shared Git-mutation skip control.
Synthetic tests exercise malformed notebooks, stable IDs, output cleanup,
interpreter selection, cell errors, and timeouts. Native checker tests cover
syntax, formatting, types, IPython forms, cross-cell references, configuration,
and diagnostic cell mapping. Kernel tests require local socket access.
`just check-dist` validates existing wheel and sdist artifacts without rebuilding.
The dedicated Linux changelog job installs the generator through
`just changelog-setup`, tests generation with `just changelog-test`, and runs
`just check-dist-changelog` against the same built wheel and sdist. This last
component exercises only the installed CLI generation contract, without replaying
the remaining distribution suites. Missing or mismatched generators fail this gate;
ordinary base installation checks may omit the external generator.

Both installed distributions resolve the optional `python-tools` extra and run
its public checks with exact native versions, modeled drift, and real adoption
candidate resolution. These checks preserve public runtime constraints and
consumer sources. Base installs omit Ruff, ty, and pytest.

Base installations also omit pypdf. Both distributions run source-date, raw-line
and read-only Tectonic discovery consumers without PDF dependencies, then resolve
their own `papers` extra and run complete synthetic PDF consumers. Coverage
includes date/comment failures, deterministic same-width normalization,
text/geometry equivalence across different bytes, validated refresh, failure
preservation and CLI/configuration policy. Native pkg-config tests use small
`.pc` fixtures; Windows tests use triplet-directory fixtures. These prove
discovery/export on each host, not native library ABI or Tectonic compilation.
Raw-line tests include native Git selection, Unicode/CRLF, fences/tables/URLs,
missing final newline and unchanged inputs.

Both base installations also adopt a synthetic installable application through
the packaged bootstrap under native Python 3.13. They check mandatory metadata
enforcement without development opt-ins, unchanged dry runs and Git state,
repeat-apply environment stability, and preserved upper bounds/exclusions.
The resulting application wheel and sdist reject old Python and install on a
supported interpreter without the shared tooling package as a runtime dependency.
Focused fixtures model future minimum changes, stricter bounds, and rollback.

Both base installations exercise the public Just inspection API using the
shipped Just version, native metadata, argument boundaries, evaluation failures,
and unchanged recipe files. With notebook extras, they also exercise public
temporary-project fixtures, fresh-kernel success/failure/timeout reports,
explicit environment and working-directory selection, link/junction rejection,
and cleanup without modifying the borrowed environment or original inputs.
A representative fixture integration reuses the installed consumer's real lock
and synchronized notebook environment.

Both installed distributions run the validation consumer suite with native
Ruff, ty, and offline zizmor. It verifies the shipped typing policy, precise
negative-fixture exceptions, missing-annotation/TC/UP failures, CLI selection,
gate wiring, scanner finding status, and valid SARIF. Modeled Git inventories
supplement native tracked/new-file discovery tests; only the latter require Git
mutations. Credential-free unit tests cover token precedence, failed discovery,
required-online failure, redaction, scanner errors, and managed scanner selection.
These fixtures do not establish successful authenticated audits or SARIF upload.

GitHub runs package checks on Linux, macOS, and Windows, plus a git-cliff
integration job, for pull requests and pushes to `main`. Branch pushes with an
open pull request do not duplicate the matrix. Separate workflows run dependency
auditing, CodeQL, and zizmor security analysis. Local results do not establish that
hosted jobs passed.
Platform models and fake executables do not substitute for native installer
verification; see the [toolchain contract](INSTALLING.md) for prerequisites.

Source and both installed distributions also run the public Cargo example suite.
Small dependency-free crates exercise native metadata discovery of nested and
explicit targets, one default-feature build, separate feature overrides, required
features, custom target directories, literal arguments and CLI failures. Real
child-process handshakes prove stdout arrives before completion with and without
assertions; exact-byte, marker-boundary, nonzero-exit and timeout-cleanup cases
cover the shared live runner. Cargo and a native toolchain are required on PATH.
These generic fixtures do not replace consumer coverage or scientific assertions.

The Linux test pass collects coverage of package code and repository scripts,
including Python subprocesses, and omits tests and the development environment.
Linux retains its Cobertura report as a seven-day Actions artifact. A
reusable `codecov.yml` workflow uploads the report using OIDC; forks
and Dependabot use public-repository tokenless uploads. No additional test run is
scheduled for coverage. macOS and Windows run ordinary tests and the same
installation checks. Release-tag validation retains reports without uploading.
Codecov project and patch statuses are initially advisory while a baseline is
established; test failures still fail the required platform checks. Setup and
account activation are documented in [GitHub configuration](CONFIGURING_GITHUB.md#codecov).

The required platform jobs run `just check-setup` against the built wheel in a
temporary consumer with fresh managed-tool directories. This downloads managed
Python, installs Rust with a component and target plus a Cargo tool, and runs the
explicit setup command. It checks verified executable selection, a native Rust
compile/run, user-level Just in a fresh shell, development dependency sync,
unchanged declarations/lockfiles, and repeat setup without replacing tools or
duplicating shell configuration. Linux uses Bash, macOS uses Zsh, and Windows
uses PowerShell with the refreshed user PATH from the registry.
The same job exercises an explicit Cargo upgrade, verifies the published pins
and managed executable, and checks that old installations remain available.
It also installs `clippy-sarif` and `sarif-fmt`, passes a small Cargo JSON diagnostic
through the managed tools, and checks their failure statuses.
The native setup check also probes managed Cargo deny, Gitleaks, and OSV-Scanner,
installs the locked tooling package without release credentials, and invokes the
installed isolated Python for authenticated cold and warm binary-only sync.
Ordinary setup, dependency builds, and package synchronization use credential-free
environments; authenticated upgrade invokes the installed CLI directly.
It also
checks a synthetic Gitleaks finding with redacted JSON/SARIF output, and parses an
empty Cargo lockfile through OSV without advisory queries. It previews and applies
cleanup inside the disposable managed store, then rechecks the retained tools.

Wheel and sdist installation checks run the same public Python consumer suite
outside the checkout on every platform. It imports only documented process and
publication APIs and covers byte transport, configured Git clean filters, Unicode
paths, command errors/timeouts, file permissions, staging failures, rollback, and
retained recovery backups. Git byte/filter checks use read-only `hash-object`
without creating a repository or writing objects. POSIX permission and symlink
assertions do not substitute for the native Windows checks of ordinary paths.

The installed performance consumer suite exercises Criterion estimates, complete
and incomplete benchmark coverage, exact CRLF/binary bytes, provenance differences,
immutable evidence, tar/ZIP extraction, and retained rendering using public imports.
Shared regressions cover unsafe paths and links, resource limits, malformed and
non-finite estimates, stale provenance, and publication/rollback failures. HTTP
transport failure tests use synthetic responses; release selection and access
to a particular published asset remain consumer integration checks. The existing
native platform matrix runs these suites against both distribution formats.

Installed toolchain and security suites exercise cleanup retention roots, private
Python aliases, adoption helper permissions and linked-environment refusal, native
status propagation, malformed reports, and Rust-doc source line mapping. Windows
fixtures create real junctions and verify their targets before applying checks.
Scanner subprocess models cover report handling; native setup probes above cover
the downloaded binaries. Installed notebook lint checks both magic and literal
keyword-argument install commands without executing notebook cells.

The installed document-publication consumer suite checks selected tables and SVGs,
exact surrounding document bytes, retained-input snapshots, CLI preview/check modes,
and tagged artifact/source identity. Disposable Git fixtures exercise stored blob
bytes, file modes, and replacement objects without mutating source repositories.
Shared publication regressions also cover malformed markers, portable aliases,
stale release/report references, and multi-output rollback and recovery failures.

`just check-setup` requires a disposable GitHub-hosted runner because it installs
real tools and changes that runner user's shell configuration or Windows user
PATH. It is deliberately excluded from local `just ci`. These checks use the
runner's native compiler and SDK; they do not install operating-system build
prerequisites or prove compatibility with every supported shell or Cargo tool.

Consumer adoption, native Semgrep rule execution, and published-package
verification require their own checks. Follow the [publishing guide](RELEASING.md)
for a clean PyPI installation and setup check before closing the release issue.

## Validation under the agent Git policy

[AGENTS.md](../AGENTS.md) requires explicit task authorization for Git mutations,
including disposable test repositories. Without that authorization, set the shared
test control for agent-run validation:

```sh
RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS=1 just ci
```

The value `1` skips the Git-mutating changelog, process, and publication fixtures
before setup. The installation checker inherits this setting, so the directly
executed wheel and sdist consumer suites skip their Git-mutating fixtures too.
Read-only Git checks still run. Tests requiring a disposable Git repository remain
unverified by this command; report the skips with the validation result.

Authorized agent validation, maintainers, and hosted CI leave this setting unset
and run the full suite. Authorization for fixture mutations does not authorize
unrequested operations on the source repository.

## Reporting results

Record the source state, commands, platform, results, exclusions, and limitations
in PR descriptions, review notes, or CI logs. Track outstanding verification in
the relevant issue. Distinguish native platform runs from models or emulation,
and report unavailable checks explicitly.

Keep this guide focused on current procedures and coverage boundaries. Do not
append dated fix summaries, test counts, or validation histories. Refer to the
generated [CHANGELOG.md](../CHANGELOG.md) for changes.
