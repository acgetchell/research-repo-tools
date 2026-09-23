# Validating workflows and Python

This guide ships with the package alongside `justfile`, `python-validation.toml`,
and `zizmor.yml`. These additions target research-repo-tools 0.1.6. Adopt the exact
registry pin `research-repo-tools==0.1.6` after that release is published. A local
wheel can verify a candidate; it does not establish registry availability.

## Audit contract

`research-repo-tools zizmor check [--offline | --require-online]
[--format plain|sarif] [PATH...]` resolves existing local paths relative to the
consumer root, defaulting to `.github`. It supports local files and directories;
remote repository slugs and stdin are outside this wrapper's contract. Input
collection is strict. Finding suppressions remain in native zizmor configuration.

Declare `persona = "regular"`, `"pedantic"`, or `"auditor"` in
`[tool.research-repo-tools.zizmor]`; there is no implicit persona. Optional
`timeout = 300` is a positive scanner timeout in seconds. Declare exactly one
scanner authority: an unconditional `zizmor==X.Y.Z` requirement in
`dependency-groups.dev` (including an included group), OR `zizmor = "X.Y.Z"` in
`[tool.research-repo-tools.toolchain.cargo]`. The Python path is selected from the
locked environment's PATH. The Cargo path comes from the existing managed
toolchain cache. Both verify the executable version before auditing; neither
installs tools implicitly. Cargo setup retains its existing Rust, Python, uv,
and platform requirements. Do not add another scanner pin to a workflow action.

Authentication precedence is the first nonempty `ZIZMOR_GITHUB_TOKEN`, then
`GH_TOKEN`, then `gh auth token --hostname HOST`. `GH_HOST` selects the same host
for discovery and scanning, defaulting to `github.com`. Whitespace or invalid
explicit tokens fail; they do not select a lower-priority credential.
`GITHUB_TOKEN` is intentionally not an implicit source: map GitHub Actions'
`${{ github.token }}` into `ZIZMOR_GITHUB_TOKEN` as the template does.

Automatic local mode enables online audits when a token is available. Missing
gh, failed discovery, and empty/invalid discovery output produce a clearly
reported offline fallback. Explicit `--offline` skips discovery and credentials.
For required online audits, `--require-online` fails before scanning when no
token is available. Inherited `ZIZMOR_OFFLINE` and `ZIZMOR_NO_ONLINE_AUDITS` never
override this CLI policy. Authentication/API failures from an attempted online
scan propagate without an offline retry. A discovered token enables an online
attempt; it is not proof that GitHub accepted it or every online audit succeeded.

Tokens are passed only through the child environment, never command arguments,
logs, or package-written files. Authentication output is suppressed. Scanner
output is captured in memory and known token values are redacted from both
streams, including failure output. Launch and timeout errors suppress captured
diagnostics. Scanner reports go to stdout; policy status goes to stderr.
Native exit codes propagate, with signals mapped to 128 plus the signal number.

Zizmor's SARIF mode returns success for findings. The template runs a plain gate,
then generates SARIF even if the gate failed. It never uses `continue-on-error`
to turn the gate green. Upload runs only after successful SARIF generation and
only for same-repository trusted events; fork and Dependabot PRs still run the
required-online gate using their read token, but skip privileged upload. A host
policy that withholds that read token causes an explicit failure requiring a
consumer decision, never a hidden downgrade. Do not switch to `pull_request_target`
to execute fork code with elevated permissions. `contents: read` supports audits;
`security-events: write` is for SARIF upload. Configure code scanning and required
checks in repository settings. See the upstream
[zizmor integration contract](https://docs.zizmor.sh/integrations/).

For Cargo-pinned consumers, run their existing managed setup before the workflow
gate. Reuse the repository's setup/cache wiring instead of introducing a second
scanner installation authority. Action SHA/tag resolution remains in zizmor's
online audits. This direct scanner contract supersedes an action-specific policy
requiring `zizmor-action`; no hard-coded remote tag map is needed.

## Python inventory and policy

Merge `python-validation.toml` into the consumer's pyproject, preserving all
existing Ruff selections. It opts into ANN001/002/003/201/202/204/205/206, the TC
family including TC003, and UP037. Keep the consumer's exact Ruff and ty pins in
its locked development group. The package supplies discovery and invocation;
native Ruff and ty own the policy and diagnostics.

The packaged `python-check` recipe applies the complete configured Ruff policy,
format check, and ty to `*.py` and `*.pyi` selected by the existing `files run`
command. Quoted Git pathspecs match root and nested files. Tracked files and
nonignored new files enter automatically, including support scripts, consumer
tests, and Semgrep fixtures. Deleted and ignored untracked files are omitted.
Do not narrow the rule set with `--select` or exclude fixture directories. Avoid
directory exclusions for deliberate fixtures. Ruff and ty explicitly use
`--no-force-exclude` so supplied files are checked even if a project-wide
exclusion would otherwise remove them. Ruff's gate uses `--no-fix` to keep
checking read-only even when a consumer enables fixes in its configuration.

Use exact Ruff per-file/rule exceptions for deliberate violations. The template
shows missing-annotation exceptions on one example fixture. For intentional ty
errors, prefer a line-level `# ty: ignore[rule-name]` with a reason. Unrelated
rules, neighboring lines, and other files remain checked. Review additions to
exceptions as consumer policy, and keep Semgrep's own expected-findings checks.
Keep notebook checking native through `notebooks lint`; the `.py` inventory does
not include notebook cells. A consumer with notebooks can add this thin recipe
and include it in `check` alongside its Rust/domain gates:

```just
# Validate every tracked and nonignored notebook with native notebook handling.
notebooks-check:
    notebook_group="$(uv run --locked --only-group tooling --inexact research-repo-tools notebooks group)" && uv run --locked --group dev --group "$notebook_group" research-repo-tools files run --include '*.ipynb' -- research-repo-tools notebooks lint
```

The canonical packaged `check: python-check semgrep-check zizmor-check` and `ci: check`
examples intentionally retain fixture validation. Extend them with
`notebooks-check` where notebooks exist, existing tests, and consumer domain
checks. Invoke the same complete gate locally and in CI. Merely publishing this
package does not wire a consumer's gates or annotate its scientific code.

Python 3.14 defers ordinary annotations. For annotation-only imports, use
`TYPE_CHECKING` and unquoted annotations; do not add future-annotations imports
just to satisfy lint. Runtime consumers such as `typing.get_type_hints`, model
frameworks, decorators, or runtime type aliases can still need real imports.
Keep those imports available and configure Ruff's runtime-evaluated decorators
or base classes, or a precise documented suppression. Do not move imports that
runtime code needs. See [Ruff's type-checking settings](https://docs.astral.sh/ruff/settings/#lint_flake8-type-checking)
and [Python's annotation model](https://docs.python.org/3.14/reference/compound_stmts.html#annotations).

## Consumer review and adoption

Exclude deliberate negative fixtures from general CodeRabbit review using the
consumer's `reviews.path_filters`, for example `"!tests/semgrep/**"`. This review
filter must not become a validation exclusion. If the docstring policy requires
documentation for public scientific APIs rather than a percentage of all helpers
and tests, set `reviews.pre_merge_checks.docstrings.mode: off` and express that
policy in path instructions or keep the percentage check with a compatible
threshold. These are consumer choices; they do not alter Ruff/Ty. Consult
[CodeRabbit's configuration reference](https://docs.coderabbit.ai/reference/configuration)
when updating the consumer's settings.

MCMC adoption still requires its own annotations, exact fixture exceptions,
CodeRabbit choices, tool pins, and local/CI wiring. Keep its adoption issues open
until those checks run against the published registry pin. Other consumers can
adopt independently without production Python helpers or copied shared tests.

Record authenticated and unauthenticated smoke results separately. Synthetic
credential tests establish precedence/redaction behavior, not successful online
audits. Offline native SARIF production does not establish an upload. Record
actual GitHub upload evidence when available. Local package checks cover only
their native host; Linux, macOS, and Windows package/installation checks are
required for the current commit before declaring merge readiness.
