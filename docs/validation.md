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
