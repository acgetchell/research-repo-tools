# Validation

Final local `just ci` run on 2026-09-07 passed: **534 tests in 14.99 seconds**,
Ruff, formatting, ty, wheel/sdist builds, and both isolated uv installations.
No tests were skipped on this host. The execution log is
`/tmp/rrt-consolidation-ci.log`.

Validated source fingerprint (SHA-256):
`df289a804cf1d8c6df2397cb9ffb54f9f03509de7635678528ffd8a5bb7a8459`. It hashes sorted source, script, and test paths plus
`pyproject.toml`, `uv.lock`, and `justfile`, each encoded as relative path, NUL,
and the file's binary SHA-256 digest; Python caches are excluded.

Run `just check` during development and `just ci` for final validation. The
latter runs Ruff, formatting, ty, pytest, local distribution builds, and isolated
uv installation of both the wheel and the source distribution.

The retained tests exercise the installed package's shared capabilities using
synthetic inputs. There are no copied consumer repositories, reference
implementations, or historical test runners. The previous 4,072-test extraction
baseline included tests of old implementations and is superseded by this suite;
its count is not a compatibility claim for this smaller scope.

Installation validation runs outside the checkout, imports every shipped module,
checks packaged templates and the console entry point, exercises changelog
archiving and archived notes, and checks the optional pinned `just` executable.
It also verifies one license per distribution and minimal base dependencies.

Native validation is performed on macOS arm64 with Python 3.14.7 and uv 0.12.10.
The workflow defines Linux, macOS, and Windows jobs, but local results do not
establish that those hosted jobs have passed. Platform-specific mocks verify
error and byte-transport contracts; they do not substitute for native runs.
Git and git-cliff integration tests use disposable repositories. Consumer
adoption, native Semgrep rule execution, and package publication are separate
from this local validation.
