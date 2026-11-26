# Validating the package

After [contributor setup](../CONTRIBUTING.md#development-environment), run
`just check` while editing and `just ci` when the change is ready for review.
Consumer repositories use the [consumer check recipes](RUNNING_CHECKS.md).

## Local checks

| Command | When to use it |
| --- | --- |
| `just audit` | Check locked third-party dependencies applicable to this host against online advisories. |
| `just check` | Verify the lock, both Justfiles, explicit Python newlines, formatting, lint, strict types and workflows. |
| `just check-dist` | Exercise existing wheel and sdist installations outside the checkout. |
| `just ci` | Run policy checks, build once, verify distributions, then run source tests with coverage and isolated installation checks. Use `just --jobs 1 ci` to serialize the last two branches. |
| `just coverage [PYTEST_ARGS...]` | Collect branch and subprocess coverage in `coverage/cobertura.xml`. |
| `just publish-check` | Check the current wheel and sdist metadata with strict Twine. Use `just build publish-check` if the artifacts are missing or stale. |
| `just test [PYTEST_ARGS...]` | Run source tests, or select a focused regression during development. |

Use `just help` for the complete [maintainer recipe list](../CONTRIBUTING.md#maintainer-commands).
Strict Twine checks distribution metadata and description warnings. Check README
rendering and link targets separately when changing package documentation.

## Package and platform coverage

The [package checks workflow](../.github/workflows/ci.yml) builds one wheel and
sdist. Each native job consumes that build by immutable artifact ID. Use the
job that failed to decide what to reproduce.

| Job | What it verifies |
| --- | --- |
| `source` | Runs the repository checks and source tests on Linux, macOS and Windows. Linux also collects coverage. |
| `distributions` | Installs the wheel and sdist outside the checkout on each OS. The [installation checker](../scripts/check_install.py) selects the public CLI, API, template and optional-extra consumer suites for each artifact. |
| `setup` | Runs `just check-setup` from the built wheel in a disposable consumer. Verifies real tool installation, native execution, user-level Just, repeated setup, upgrades and cleanup. |
| `changelog-integration` | Installs the pinned git-cliff on Linux, then runs `just changelog-test` and `just check-dist-changelog` against the built distributions. |

The installation suites distinguish base-package behavior from features that
need extras. They exercise native byte transport, paths and subprocesses,
including the Windows-specific cases in the [portability guide](../CONTRIBUTING.md#common-windows-issues-and-how-to-avoid-them).
Cargo fixtures need a native toolchain, and notebook kernel tests need local
sockets. Synthetic performance, publication and scanner fixtures exercise the
shared contracts. Consumers verify their scientific workloads, native integration
and published pin with their own configuration.

`just check-setup` changes the runner user's shell configuration or Windows
PATH and installs real tools. It runs on disposable hosted runners with native
compilers and SDKs. Local `just ci` covers its host and excludes this setup job.
Each required `check (OS)` result depends on the build and all three native
stages across the matrix. Verify the current commit's hosted results separately.

### Coverage and release evidence

Linux collects coverage for `src/research_repo_tools` and `scripts`, including
Python subprocesses. External Semgrep runs without instrumentation. If collection
fails, inspect the raw shards in the reported `coverage/failed-*` directory.
The previous database and report may still be present.

Actions retains the Linux Cobertura report for seven days and uploads it through
the [Codecov workflow](../.github/workflows/codecov.yml). Project and patch coverage
statuses are advisory. See [Codecov configuration](CONFIGURING_GITHUB.md#codecov)
for account setup and fork uploads.

A successful main push signs evidence tying the exact commit and workflow attempt
to the producer, native gates and distribution bytes. Distribution and evidence
artifacts are retained for 30 days. For incomplete attempts or expired artifacts,
follow the [release recovery window](RECOVERING_RELEASES.md#artifact-retention-and-recovery-window).
Published-package verification belongs to the [release procedure](RELEASING.md).

## Validation under the agent Git policy

[AGENTS.md](../AGENTS.md) requires explicit task authorization for Git mutations,
including disposable test repositories. Without that authorization, set the shared
test control for agent-run validation:

```sh
RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS=1 just ci
```

The value `1` skips fixtures that create or mutate Git repositories
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
