# Validation

Run `just check` during development and `just ci` for final validation. The
latter runs actionlint, zizmor, Ruff, formatting, ty, pytest, local distribution builds, and isolated
uv installation of both the wheel and the source distribution.
`just audit` separately exports locked third-party requirements from all dependency
groups and checks them against online vulnerability advisories. It excludes the
local project and evaluates requirement markers on the current platform; it does
not claim to audit every platform's conditional dependencies. GitHub also runs CodeQL for Python and Actions, and
uploads zizmor findings to code scanning.
Record the source state, command, platform, result, and any excluded tests with
each validation run. A historical passing result does not validate later edits.

The retained tests exercise the installed package's shared capabilities using
synthetic inputs. There are no copied consumer repositories, reference
implementations, or historical test runners. The previous 4,072-test extraction
baseline included tests of old implementations and is superseded by this suite;
its count is not a compatibility claim for this smaller scope.

Installation validation runs outside the checkout, imports every shipped module,
checks packaged templates and the console entry point, exercises changelog
archiving and archived notes, and checks the pinned `just` executable supplied
by each installation. It also verifies one license per distribution, matching
attribution/provenance resources, and the declared runtime dependencies.

The workflow defines Linux, macOS, and Windows jobs, but local results do not
establish that those hosted jobs have passed. Platform-specific mocks verify
error and byte-transport contracts; they do not substitute for native runs.
History-generation and tagging integration tests explicitly request disposable
repositories; generic subprocess tests do not initialize Git. Template regressions
use git-cliff with synthetic messages and an empty range in the existing checkout,
without creating commits or tags. When Git mutations are prohibited, exclude the
tests that create or modify repositories and report the exclusion with the result. Consumer
adoption, native Semgrep rule execution, and package publication are separate
from this local validation.

## Windows CI regression fixes

Validated locally on 2026-09-15 using macOS arm64 and Python 3.14.7, against
base commit `a91717e30fd62661a044d4ddfadb47ae44811324` plus the Windows CI fixes.
The source fingerprint is
`aaec163c8ec906a1a3cd43b2e310429e8426eda77b477b049cae57ba1326afb1`.
It hashes sorted Python paths under `src`, `scripts`, and `tests`, plus
`pyproject.toml`, `uv.lock`, and `justfile`, encoding each relative path, NUL,
and the file's binary SHA-256 digest.

- The new Windows-path formatting regression failed with the previous formatter
  and passed after the fix. This models path semantics on macOS; it is not a
  native Windows run.
- `just ci` passed all checks, **574 tests**, package builds, and isolated wheel
  and sdist installations. `PYTEST_ADDOPTS` excluded the four Git-mutating tests
  listed below under the agent policy.
- Rollback tests now cover LF and CRLF inputs explicitly. The Semgrep regression
  compares the original relative fixture path using native path semantics.

Excluded tests:

- `test_generate_real_git_history_with_packaged_template`
- `test_tag_preserves_utf8_notes_and_force_replaces_atomically`
- `test_repository_identity_and_history_are_read_from_the_consumer`
- `test_runs_simple_git_command`

Native Windows validation of these changes remains pending until they are pushed
and the required `check (windows-2025)` job passes.

## Changelog self-use follow-up

On the same host, configuring this repository to use its own generator exposed
a template failure before the first release tag. A real git-cliff regression
using a synthetic JSON context reproduced that failure. The corrected shared
template handles unreleased history and first/subsequent release links.

The follow-up `just ci` run used `PYTEST_ADDOPTS` to select
`tests/changelog/test_cliff_template.py` and `tests/changelog/test_contract.py`,
excluding their two Git-mutating integration tests listed above: **29 passed,
2 deselected**. Workflow, lint, format, type, and lock checks passed, as did
package builds and isolated wheel/sdist installations. Unrelated tests from the
Windows-fix run were not repeated.

Source fingerprint:
`d94cccbdeedb023379548fb3768abd8ee5c45fe3563b0fa1ac5ec89f99026f57`.
This uses the same hashing scheme but includes every file under `src`, `scripts`,
and `tests` except `__pycache__`, so packaged templates are included, plus
`pyproject.toml`, `uv.lock`, and `justfile`.

Both `just changelog-preview` and `just changelog` passed against the existing
Git history. The generated changelog contains committed work only.

## Python review fixes

Validated locally on 2026-09-15 using macOS arm64 and Python 3.14.7, against
base commit `a91717e30fd62661a044d4ddfadb47ae44811324` plus the preceding staged
changes and the Python review fixes. Source fingerprint, using the complete
source-file scheme above:
`7972edb0706a80339670eaec65f463d5d9edae54824fc112a653e2d1a10bf8f2`.

- Eighteen focused regression cases failed before the fixes and passed afterward.
  They cover retained recovery bytes after dependency rollback failure, configured
  uv selection, Markdown table preservation, normalized Python lockfile names,
  and runtime/CLI agreement with installed distribution metadata.
- Additional regressions cover backup failure before external mutation, cleanup
  after successful updates, table boundaries, and registry-entry preservation.
  Recovery failures are injected at the actual filesystem replacement boundary;
  uv subprocesses in those tests are simulated.
- `just check` passed. Its ty invocation now includes `src`, `scripts`, and
  `tests`; all eleven previously uncovered type diagnostics are corrected.
- Final `just ci` passed **598 tests**, workflow and static checks, package builds,
  and isolated wheel/sdist installations. The final combined run includes the
  focused regressions above. `PYTEST_ADDOPTS` deselected the same four Git-mutating
  tests listed under the Windows regression fixes.

The final command was:

```sh
PYTEST_ADDOPTS='-k "not test_generate_real_git_history_with_packaged_template and not test_tag_preserves_utf8_notes_and_force_replaces_atomically and not test_repository_identity_and_history_are_read_from_the_consumer and not test_runs_simple_git_command"' uv run --locked just ci
```

The validation record was appended after the build; the fingerprint excludes
documentation. Native Windows execution, live dependency-update integration,
Git-mutating integration tests, and publication are not established by this run.

## Release-readiness follow-up

On 2026-09-15, the same macOS arm64/Python 3.14.7 host validated the new
`just update` recipe and refreshed dependency state, including Ruff 0.16.7 and
ty 0.0.81. Source fingerprint:
`6b3b5240663a9bbfe62b9c131e355d25802afb9f56cf6ca5a4b517b69b964149`.
The final command above passed again: **598 tests, four deselected**, all static
and workflow checks, package builds, and isolated wheel/sdist installations.
This record was appended after building; it is outside the source fingerprint.

GitHub's latest package checks for pushed commit
`a91717e30fd62661a044d4ddfadb47ae44811324` still failed on Windows while Linux,
macOS, and changelog integration passed. Those results precede the local fixes.
The release PR must establish passing native checks and CodeRabbit review for
the submitted source state before publication. At inspection, GitHub had no
publishing environment and the repository had no publishing workflow.
