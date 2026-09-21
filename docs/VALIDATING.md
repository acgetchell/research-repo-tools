# Validating the package

This guide describes how to validate the current package. Fixes and release
history belong in the generated [CHANGELOG.md](../CHANGELOG.md). See
[Contributing](../CONTRIBUTING.md#generated-changelog) for its generation workflow.

## Local checks

| Command | Purpose |
| --- | --- |
| `just audit` | Check locked third-party Python dependencies against online vulnerability advisories |
| `just check` | Workflow, lockfile, lint, format, newline, and type checks during development |
| `just ci` | Final checks, tests, wheel/sdist builds, and isolated installation checks |
| `just coverage` | Run tests with branch and subprocess coverage; write `coverage/cobertura.xml` |
| `just newline-check` | Reject implicit newline translation in Python text-file writes |
| `just test` | Run the test suite |

Run the [contributor setup](../CONTRIBUTING.md#development-environment)
before using these recipes. Environment activation is not required.
Run focused regressions when a behavioral change needs verification, then
`just ci` when the work is ready for review. A passing `just check` does not
establish that tests, builds, or installation checks passed.

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
groups disabled, verifying that recipes restore their required tooling group.
Maintenance-only installations must not contain notebook dependencies. Each
distribution is also installed with its notebook extra through a locked consumer
group, then checked with project-kernel synchronization, native Ruff/ty linting,
and real notebook execution. Installed recipes cover both the default notebook
group and a custom group, including output cleanup.
Synthetic tests exercise malformed notebooks, stable IDs, output cleanup,
interpreter selection, cell errors, and timeouts. Native checker tests cover
syntax, formatting, types, IPython forms, cross-cell references, configuration,
and diagnostic cell mapping. Kernel tests require local socket access.
`just check-dist` validates existing wheel and sdist artifacts without rebuilding.

GitHub runs package checks on Linux, macOS, and Windows, plus a git-cliff
integration job, for pull requests and pushes to `main`. Branch pushes with an
open pull request do not duplicate the matrix. Separate workflows run dependency
auditing, CodeQL, and zizmor security analysis. Local results do not establish that
hosted jobs passed.
Platform models and fake executables do not substitute for native installer
verification; see the [toolchain contract](INSTALLING.md) for prerequisites.

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

`just check-setup` requires a disposable GitHub-hosted runner because it installs
real tools and changes that runner user's shell configuration or Windows user
PATH. It is deliberately excluded from local `just ci`. These checks use the
runner's native compiler and SDK; they do not install operating-system build
prerequisites or prove compatibility with every supported shell or Cargo tool.

Consumer adoption, native Semgrep rule execution, and published-package
verification require their own checks. Follow the [publishing guide](RELEASING.md)
for a clean PyPI installation and setup check before closing the release issue.

## Validation under the agent Git policy

[AGENTS.md](../AGENTS.md) prohibits agents from mutating Git state, including in
disposable test repositories. Exclude the four tests that perform such mutations:

```sh
PYTEST_ADDOPTS='-k "not test_generate_real_git_history_with_packaged_template
and not test_tag_preserves_utf8_notes_and_force_replaces_atomically
and not test_repository_identity_and_history_are_read_from_the_consumer
and not test_runs_simple_git_command"' just ci
```

These exclusions apply to agent-run validation. Maintainers and hosted CI run
the full suite. Read-only Git checks may still run under the agent policy.

## Reporting results

Record the source state, commands, platform, results, exclusions, and limitations
in PR descriptions, review notes, or CI logs. Track outstanding verification in
the relevant issue. Distinguish native platform runs from models or emulation,
and report unavailable checks explicitly.

Keep this guide focused on current procedures and coverage boundaries. Do not
append dated fix summaries, test counts, or validation histories. Refer to the
generated [CHANGELOG.md](../CHANGELOG.md) for changes.
