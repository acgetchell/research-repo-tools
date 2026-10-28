# Running checks and security scans

Start with [consumer setup](../README.md#install-with-uv) and a synchronized
locked environment. Select the checks your repository needs, then include them
in its `check` and `ci` recipes alongside native and scientific tests. A clean
check exits zero; findings, invalid configuration and missing tools stop the gate.
Scanner policies and scientific acceptance criteria belong to the consumer.

- [Workflow security and complete Python validation](#workflow-security-and-complete-python-validation)
- [Cargo examples](#cargo-examples)
- [Dependency and secret scanning](#dependency-and-secret-scanning)
- [Rust documentation scans](#rust-documentation-scans)
- [Raw text line limits](#raw-text-line-limits)
- [SARIF selection](#sarif-selection)
- [Clippy SARIF helpers](#clippy-sarif-helpers)
- [CodeRabbit review](#coderabbit-review)
- [Coverage summaries](#coverage-summaries)
- [Consumer glue and release jobs](#consumer-glue-and-release-jobs)

## Workflow security and complete Python validation

Use Python 3.15 or newer and the locked development environment. Merge the
packaged `python-validation.toml` policy into your pyproject and retain the full
existing Ruff configuration. It enables missing
annotations (ANN001/002/003/201/202/204/205/206), TC, and UP037. The `python-check`
recipe discovers `*.py` and `*.pyi` throughout the repository, including support
code, tests, and negative Semgrep fixtures. Use exact per-file/rule exceptions
for intentional violations. Extend `check` with existing Rust/domain tests and
native notebook linting; keep `semgrep-check` in the canonical local and CI gate.

The commands require an already synchronized development environment. Checks
never install or upgrade tools. `python check` runs Ruff format checks, Ruff lint,
and strict ty (`--error all`); `python typecheck` runs strict ty alone. Both disable
automatic source changes.
`python fix` applies configured Ruff fixes followed by formatting; lint failures
from the fix pass still propagate. Native settings, nested configuration, and
precise exceptions remain active. Empty inventories require no validators;
opted-in declaration and lock checks still apply. Missing tools fail before
validation starts. Discovery selects one inventory for all policies. Executables
and argument batches
are preflighted before validation starts. Batches fill a portable argument-size
budget; the first failing batch stops the gate and its native status propagates.
`python fix` stops on a failed lint-fix batch before formatting.
Use `just setup` to prepare the locked environment.

Declare one zizmor scanner pin, either `zizmor==1.30.1` in the consumer's dev
dependency group or `zizmor = "1.30.1"` in its existing managed Cargo toolchain.
Keep the choice in that one repository-owned declaration. Configure an explicit
persona in pyproject.toml:

```toml
[tool.research-repo-tools.zizmor]
persona = "regular"
timeout = 300
```

After [setup](INSTALLING.md), run:

```sh
just python-check
just zizmor-check
just ci
```

Local auditing uses `ZIZMOR_GITHUB_TOKEN`, then `GH_TOKEN`, then authenticated
`gh` discovery. It reports an offline fallback when authentication is absent.
Use `just zizmor-check --offline` for deliberate offline checking, or
`just zizmor-check --require-online` in CI. Scanner errors propagate and never
trigger an offline retry. Token values are not printed or stored by the wrapper.

The packaged `zizmor.yml` workflow runs the finding gate and then produces SARIF
with the same pin/persona. SARIF output alone does not fail on findings; the
plain gate does. Fork and Dependabot PRs retain audits and skip privileged upload.
The installed
[validation and migration guide](../src/research_repo_tools/templates/VALIDATING_WORKFLOWS.md)
documents authentication, permissions, Python 3.15 annotations, exact negative
fixture exceptions, notebook wiring, and consumer CodeRabbit settings. Extract it
with `templates VALIDATING_WORKFLOWS.md`; `templates python-validation.toml` and
`templates zizmor.yml` expose the corresponding reusable examples.

## Cargo examples

With the published release containing this capability pinned, define a consumer
plan such as `tooling/examples.toml`. Cargo discovers binary examples, including
nested and explicit targets. Consumers own feature choices, selection, budgets
and scientific assertions:

```toml
schema = 1
profile = "release"
timeout = 600
build-timeout = 1800

[examples.diagnostics]
features = ["diagnostics"]
expect = ["consumer-defined success marker"]
expect-stderr = ["consumer-defined logger marker"]
```

Omit `include` to run every discovered binary example. Ordinary examples build
once with default features; feature overrides build and run separately. Copy
your existing scientific stdout markers into per-example `expect` arrays unchanged.
Preserve logger/stderr markers in `expect-stderr` after pinning a verified
published version that supports it; stdout and stderr assertions remain independent.
The packaged `cargo-examples.toml` provides a scaffold.
Add a thin consumer recipe and run `just examples`:

```just
examples:
    uv run --locked --group dev research-repo-tools toolchain run -- research-repo-tools validation cargo-examples tooling/examples.toml
```

Output is inherited by default. `expect` opts stdout into live byte forwarding
and literal matching; `expect-stderr` does the same for stderr. The child's
buffering may change and temporary disk use scales with selected output.
Stdin and unasserted streams remain inherited. See the
[CLI/configuration/Python contract](cargo-examples-api.md) and
[migration from static validation plans](cargo-examples-migration.md).

## Dependency and secret scanning

Declare the optional release binaries and run setup:

```toml
[tool.research-repo-tools.toolchain.binaries]
gitleaks = "8.30.1"
osv-scanner = "2.6.0"
```

```sh
just setup
just tools-check
just audit
just security
```

Supply only the lockfiles the consumer actually owns. Use `just files` to inspect
tracked and nonignored inputs for repositories containing several packages.
The v0.2.4 template's `audit` recipe selects root `uv.lock` by default. Replace
that selection with every maintained resolution, for example:

```just
audit: (security-osv "uv.lock" "tooling/scanner/uv.lock" "Cargo.lock" "fixtures/extra root/Cargo.lock")

security: audit security-secrets
```

`update-dependencies` runs `audit` after both package managers finish writing
their locks; `update` composes tool updates before this dependency workflow.
Findings or audit errors make either aggregate fail, leaving the updated files
available for review and repair. Direct Python/Cargo update recipes remain
available for deliberately narrower work. `check` and `ci` keep their existing
local/offline policy; auditing is an explicit network-backed command. Consumers
own advisory exceptions, additional gates, and report publication.

OSV checks each explicit supported lockfile with Go and Rust call analysis
disabled; it does not execute dependency build scripts. Advisory queries need
network access. Missing inputs, uncovered sources, scanner failures, malformed
or missing reports, and findings all block the gate.

Gitleaks checks all reachable Git history and a private snapshot of current
tracked/nonignored files, including uncommitted new files. Full history is
required (`fetch-depth: 0` in Actions). Environment/build directories are excluded
from the working snapshot. Selected symlinks and submodules fail explicitly;
scan submodules as separate repositories. Native history scanning covers patches,
not unreachable objects, nested repositories, archive contents or binary blobs.
Extra working-tree exclusions are explicit `--exclude` arguments.

Reports go to `target/security`: numbered OSV JSON/SARIF pairs and Gitleaks
history/working pairs. Findings retain native rule IDs and locations. Gitleaks
uses complete native secret redaction; surrounding match text and commit messages
are also removed before reports are published. Inline `gitleaks:allow` and
ambient `.gitleaksignore` bypasses are disabled. Consumer `.gitleaks.toml` policy
is honored; use `--scanner-config PATH` for another reviewed configuration.
Raw scanner logs are not echoed because they can include sensitive metadata.
Validated findings are printed by default: OSV identifies the lockfile,
package/version and vulnerability; Gitleaks identifies the rule and location
separately for history and the working tree. Semgrep prints the rule and source
location. Each scan prints a finding count and published report paths, with
findings listed once across JSON and SARIF. Terminal output omits descriptions
that could interpolate source values, snippets, adjacent matches and commit
messages. Extend the aggregate `just security` recipe with consumer-owned policy.
The first nonzero scanner status is returned; invalid successful reports return
1, and timeouts return 124. Checks never install or select an ambient scanner.

The standalone binaries require neither a paid GitHub security product nor a
Gitleaks Action license. Consumers own exceptions, schedules, Actions permissions,
and whether SARIF uploads are available for their repositories. See the
[migration map](shared-capability-migration.md) before deleting local tools.

## Rust documentation scans

Declare `cargo-deny = "0.20.2"` in the managed Cargo table to run the exact
`cargo deny` through the shared toolchain. Keep `deny.toml` policy local.

Consumers can wrap `semgrep scan --include '*.rs' --include '*.py' --include '*.md'
--rust-docs` in their own `just semgrep-scan` recipe. Keep rules and scope local.
The installed shared package owns the Semgrep version and compatible scanner
Python version. Both scans and fixture checks provision and verify that pin
through uv in an isolated tool environment; ambient Semgrep executables and
consumer dependency pins do not select the scanner. The wrapper continues to
use the project's Python environment.

Run `just semgrep-setup` to prepare the scanner before an offline gate. `just setup`
also prepares it when `semgrep.config` is declared. A cold cache needs network
access; provisioning failures stop the command without falling back to PATH.
uv shares its tool cache across repos, and scanner Python defaults to
`~/.cache/research-repo-tools/semgrep/python`, separate from project-interpreter
cleanup. Existing `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR` settings are honored.
Upgrade the shared-package pin to adopt a new scanner version. Rules, fixtures,
selected paths and suppression policy remain consumer-owned.

The shared scan checks native errors, exact coverage and agreement of active
JSON/SARIF findings from the same native invocation. It scans batches of at most
100 files, also bounded by portable command length. Defaults are one job,
120 seconds per rule/target, a 300-second process timeout, disabled inline
`nosem`, and aggregate `semgrep.json`/`semgrep.sarif` reports. The aggregate
SARIF contains one run with the stable `semgrep` category. Set consumer policy
explicitly when reviewed suppressions should apply:

```toml
[tool.research-repo-tools.semgrep]
config = "semgrep.yaml"
fixtures = "tests/semgrep"
batch-size = 100
inline-suppressions = true
jobs = 1
report-category = "semgrep-repository-rules"
report-layout = "aggregate"
target-timeout = 120
timeout = 600
```

Matching CLI overrides are `--batch-size`, `--inline-suppressions` /
`--no-inline-suppressions`, `--jobs`, `--report-category`, `--report-layout`
and `--target-timeout`. `numbered` layout writes one JSON/SARIF pair per batch.
Each layout owns the entire output directory: successful generations remove
all prior members, including reports from larger inventories or other layouts.
Missing/malformed reports, scan errors in JSON, incomplete coverage and timeouts
publish an empty generation. Generation or filesystem failures preserve or
restore the previous directory, with recovery backups retained if rollback fails.
Use a dedicated output directory. Native failures and active findings block;
accepted inline suppressions remain marked in SARIF and do not count as active
findings. Explicit file arguments include Python/Rust tests normally ignored by
Semgrep; deliberately excluded fixtures remain consumer policy. Rule path filters
remain active, and skipped required inputs fail the coverage gate.

`--rust-docs` scans every selected original file, including prose-only Markdown,
then scans Rust fences in Markdown and line/block rustdoc comments using the
configured Rust rules. Generic/regex prose rules still scan the originals. Each
block gets a separate temporary input at its original repository-relative path:
`src/lib.rs` remains `src/lib.rs`, and `docs/guide.md` remains `docs/guide.md`.
Native rule includes and excludes apply unchanged to those logical paths. Rust
rules intended for Markdown examples must therefore include the relevant `.md`
paths as well as production `.rs` paths. Every original and every block must be
covered by at least one applicable rule; excluded blocks fail with their source
path, block number and line range. Restrict the selected inventory when a source
is intentionally outside the scan policy.

Source lines, columns and byte offsets are restored in findings; lines hidden by
a `#` followed by a space remain code. Identical rule/span/suppression findings
from an original and its block are reported once, while distinct spans and
per-block coverage remain.
JSON `input_coverage` records source paths and representations, plus one-based
block numbers and source line ranges. Macro-generated docs and `#[doc = ...]`
attributes are outside the adapter's scope.

`semgrep check-fixtures --rust-docs` accepts mixed native, prose-only and Rust
fence fixtures. An annotation targets the next nonblank, non-fence line. A Rust
rule annotation targeting a Rust block belongs to that block; other assertions
belong to the original file. Put `ruleid:` or `ok:` directly before the code or
its opening fence, and give every Rust block at least one Rust-rule annotation.
Each representation checks its owned positive counts and rejects unexpected
findings, including violations of negative assertions. As in ordinary annotated
fixtures, production path filters are ignored for assertions; production scans
retain them. Keep configured count-based fixtures in a separate fixture gate.
Findings and fixture mismatches remain blocking.

## Raw text line limits

Declare selection and the positive limit in the consumer's `pyproject.toml`:

```toml
[tool.research-repo-tools.text]
line-limit = 160
include = ["*.md"]
exclude = ["CHANGELOG.md", "docs/archive/**", "docs/archives/changelog/**"]
```

```sh
just files check-lines
just files check-lines --limit 100 --include '*.txt' --exclude 'generated/**'
```

Each supplied option replaces its configured value; repeated include/exclude
options form that override's list. Shared selection uses Git pathspec includes
and case-sensitive POSIX glob excludes, fails on discovery errors, and checks
every selected raw line. Fences, tables, URLs, tabs and trailing spaces count.
Lengths are Unicode code points, independent of locale, rather than bytes or
display columns. LF/CRLF/CR endings do not count; final lines without a newline
do. Diagnostics are `file:one-based-line: line length N exceeds LIMIT` on stderr.
Violations return 1, a clean or empty inventory returns 0, and read/selection or
configuration errors return 1. The gate never formats source. The separate
`docs check-lines FILE...` preserves its established fixed 160 limit and table
exemption. Use `files check-lines` for a strict raw-source policy.

## SARIF selection

Declare exact driver names and rule-ID namespace prefixes. An empty prefix list
keeps all rules for that driver; unlisted drivers are omitted. No Codacy or
repository-specific selection is built into the package.

```toml
[tool.research-repo-tools.sarif]
category-prefix = "codacy"

[tool.research-repo-tools.sarif.drivers]
"Opengrep (reported by Codacy)" = ["project."]
ruff = []
```

A consumer recipe can invoke `sarif split input.sarif --output target/sarif`
and upload that dedicated directory. Keep the input outside the output directory;
overlapping source paths and portable case/Unicode aliases are rejected. Repeated driver names get distinct stable
categories; unrelated metadata is retained and indexed driver rule references
are corrected. Empty generations remove stale output files. Invalid/non-finite
input fails before publication. Extension rule references are rejected; see the
[supported SARIF API](security-api.md#sarif-api) for the precise subset.
Add `--github-output "$GITHUB_OUTPUT"` to append `SARIF_DIRECTORY`,
`SARIF_HAS_UPLOADABLE_RUNS` and `SARIF_RUN_COUNT` after publication through the
existing checked `ci.export_environment` API. Upload permissions, conditions and
categories remain consumer-owned.

For complete figure generations, use [transactional directory publication](files-api.md#python-file-publication-api).

## Clippy SARIF helpers

Declare the exact converter versions in `pyproject.toml`. Keep the consumer's
Rust compiler pinned in `rust-toolchain.toml`, with the `clippy` component:

```toml
[tool.research-repo-tools.toolchain.cargo]
clippy-sarif = "0.8.0"
sarif-fmt = "0.8.0"
```

Use `just setup` to install them and `just tools-check` to verify them. Keep
the Cargo invocation and SARIF upload workflow in the consumer. A Bash recipe can
preserve failure from every pipeline stage with `pipefail`:

```just
clippy-sarif:
    #!/usr/bin/env bash
    set -euo pipefail
    tool() { uv run --locked --no-sync --no-python-downloads research-repo-tools toolchain run -- "$@"; }
    tool cargo clippy --message-format=json | tool clippy-sarif | tee results.sarif | tool sarif-fmt
```

`just update-cargo-tools` upgrades only declared tools. Both helpers use the shared
exact, locked Cargo installer and checked execution. See the
[platform contract](INSTALLING.md#additional-cargo-tools-and-native-prerequisites).

## CodeRabbit review

Install and authenticate the [CodeRabbit CLI](https://docs.coderabbit.ai/cli)
explicitly and ensure `coderabbit` is on PATH. The package does not install it,
authenticate, enable usage credits, or retry reviews automatically. These recipes
are opt-in and remain outside `just check` and `just ci`. Agents must have an
explicit maintainer request before invoking a live review.

```sh
just review
just review main
just review-uncommitted
```

`just review` includes committed branch changes plus staged, unstaged, and
non-ignored untracked files. Before invoking CodeRabbit, it compares the local
`origin/main` commit with the live `origin` main branch. Missing or stale refs
stop with fetch guidance; failed or malformed remote lookups also stop review.
The wrapper never fetches or changes Git state. Verification applies at invocation,
not throughout a long review. An explicit local base such as `main` is checked
locally without a remote query. Uncommitted review skips the base check entirely.

The configured consumer root must contain `AGENTS.md` and exactly one of
`.coderabbit.yaml` or `.coderabbit.yml`; missing files, directories in place of
files, or both configuration names are errors. Both files are passed as additional
instructions. Output uses CodeRabbit's structured `--agent` mode and streams directly
to the terminal without the usual five-minute subprocess timeout. Nonzero exit
statuses propagate, and interruption returns 130. Service or authentication errors
are unavailable reviews, not clean results. Verify findings and suggested changes
against current code before acting on them.

## Coverage summaries

Generate a Cobertura XML report with the consumer's native coverage tool first.
To summarize it, add a thin recipe:

```just
coverage-summary:
    uv run --locked --group dev research-repo-tools coverage report --report coverage/cobertura.xml
```

`just coverage-summary` prints files with coverable lines in ascending coverage
order. Duplicate source lines are counted once. The CLI supports `--prefix`,
`--limit` and `--descending`; missing or malformed reports fail. This summary
does not run tests or impose a minimum coverage threshold. Consumers own those
gates and their interpretation of the results.

## Consumer glue and release jobs

The template's `just files` and `just validate` recipes replace discovery and
example-runner scripts. Keep scientific output expectations in configuration:

```toml
schema = 1
[[checks]]
name = "simulation"
command = ["target/debug/examples/simulation"]
expect = ["Samples:", "Mean:"]
timeout = 300
```

Compile the example first, then use `just validate examples.toml`. Explicit
executable paths discover Windows extensions. File-based validators can use
`just files run --include '*.md' --exclude CHANGELOG.md -- rumdl check`;
the shared runner selects tracked/nonignored files and batches literal arguments.

After setup/cache restoration in GitHub Actions, `just tools-export` verifies
managed tools and appends checked settings to `GITHUB_ENV`. Cache keys should
include OS/architecture and exact tool, Rust and lockfile identities. Generic
`ci export NAME ...` supports additional declared single-line environment values.
