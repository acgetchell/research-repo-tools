# Validating the package

This guide describes how to validate the current package. Fixes and release
history belong in the generated [CHANGELOG.md](../CHANGELOG.md). See
[Contributing](../CONTRIBUTING.md#generated-changelog) for its generation workflow.

## Local checks

| Command | Purpose |
| --- | --- |
| `just audit` | Check locked third-party Python dependencies against online vulnerability advisories |
| `just check` | Workflow, lockfile, lint, format, and type checks during development |
| `just ci` | Final checks, tests, wheel/sdist builds, and isolated installation checks |
| `just test` | Run the test suite |

Run the [contributor setup](../CONTRIBUTING.md#development-environment)
before using these recipes. Environment activation is not required.
Run focused regressions when a behavioral change needs verification, then
`just ci` when the work is ready for review. A passing `just check` does not
establish that tests, builds, or installation checks passed.

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
`just check-dist` validates existing wheel and sdist artifacts without rebuilding.

GitHub runs package checks on Linux, macOS, and Windows, plus a git-cliff
integration job. Separate workflows run dependency auditing, CodeQL, and zizmor
security analysis. Local results do not establish that hosted jobs passed.
Platform models and fake executables do not substitute for native installer
verification; see the [toolchain contract](INSTALLING.md) for prerequisites.

The required platform jobs run `just check-setup` against the built wheel in a
temporary consumer with fresh managed-tool directories. This downloads managed
Python, installs Rust with a component and target plus a Cargo tool, and runs the
explicit setup command. It checks verified executable selection, a native Rust
compile/run, user-level Just in a fresh shell, development dependency sync,
unchanged declarations/lockfiles, and repeat setup without replacing tools or
duplicating shell configuration. Linux uses Bash, macOS uses Zsh, and Windows
uses PowerShell with the refreshed user PATH from the registry.

`just check-setup` requires a disposable GitHub-hosted runner because it installs
real tools and changes that runner user's shell configuration or Windows user
PATH. It is deliberately excluded from local `just ci`. These checks use the
runner's native compiler and SDK; they do not install operating-system build
prerequisites or prove compatibility with every supported shell or Cargo tool.

Consumer adoption, native Semgrep rule execution, and published-package
verification require their own checks. Follow the [publishing guide](PUBLISHING.md)
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
