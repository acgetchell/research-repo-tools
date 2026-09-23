# Shared scope and adoption

For the v0.1.6 workflow-security and Python-typing adoption contract, see the
[installed validation guide](../src/research_repo_tools/templates/VALIDATING_WORKFLOWS.md).
It replaces separate zizmor-action scanner authority with the shared direct
scanner path, reuses file selection for complete Python coverage, and retains
consumer scientific annotations, precise fixture exceptions, and gate wiring.
Publication does not complete consumer adoption.

The first version provides changelog generation, normalization, archiving, release
notes and local tags; declared toolchain setup; release metadata synchronization; dependency pin maintenance;
Semgrep fixture validation; Markdown line checks; and coverage summaries.

Consumers install one package and retain thin wrappers that invoke its commands.
Common defaults live here: `CHANGELOG.md`, `docs/archives/changelog/`, canonical
Cargo SemVer pins (including prereleases and build metadata), stable uv pins,
and staged file replacement with rollback diagnostics.

Repository-specific scientific code, benchmark cases, notebooks, custom commands,
and Semgrep rules remain in consumers. This package checks supplied Semgrep rules;
it does not distribute a rule set copied from a particular repository. Custom
commands and release-pinned links retain their contents unless explicitly selected
by a [declarative release policy](UPDATING_RELEASE_METADATA.md#declarative-consumer-policies).
The [worked release-policy migration](release-policy-migration.md) replaces consumer
discovery, staging, CLI-output parsing, and publication wrappers with structured plans.
It requires a subsequent published package containing the new API; `0.1.2` is insufficient.

Tests exercise the shared implementation with small synthetic inputs grouped by
capability, including representative edge and failure cases. Fixes and release
history are recorded in the generated
[CHANGELOG.md](../CHANGELOG.md).
Historical feature-name or sentence migrations remain consumer-owned;
normalization preserves those names in prose, code spans, and link destinations.

[Shared toolchain setup](INSTALLING.md) requires uv and provides explicit
installation and verification of declared Python, Rust, and Cargo tools.
Native installer and isolated installed-package validation are required before
closing issue #2. Issue #1 covers publication and a clean PyPI setup check;
consumer migrations are separate downstream issues. Optional [notebook infrastructure](RUNNING_NOTEBOOKS.md)
provides environment/kernel setup, execution reports, cleanup, structural validation,
and Python lint/format/type gates using the consumer's Ruff and ty. Scientific
notebook content and experiment choices remain consumer-owned. Native notebook
analysis supersedes extracted Python files; migrate notebook-specific checker
configuration to `.ipynb` paths and validate each consumer's selected workflow.

The [performance APIs](performance-api.md) provide Criterion timing comparisons,
exact-byte evidence envelopes, provenance comparison, bounded archive retrieval
and extraction, and transactional promotion. Follow the
[retained-evidence migration plan](performance-migration.md) before adoption.
The [publication API](publication-api.md) adds document sections and deterministic
SVG figures from that evidence; follow its [migration guide](publication-migration.md)
for historical formats and tagged artifact guarantees. The [workflow APIs](workflow-api.md)
provide configured benchmark execution and isolated worktree orchestration.
Consumers retain scientific case definitions, harness commands, custom plotting,
and experiment policy.

To adopt the package:

1. Configure the tooling group and setup using the [toolchain guide](INSTALLING.md).
   Install a pinned PyPI release with uv and commit the consumer's lockfile.
2. Replace one duplicate script with a thin call to the shared command.
3. Run the consumer's own checks against that command before removing its old script.
4. Keep special scientific and deployment behavior in the consumer.

The first pilot is `markov-chain-monte-carlo`, after its current work reaches a
completed, validated checkpoint. Its [separate changelog adoption issue](https://github.com/acgetchell/markov-chain-monte-carlo/issues/157)
depends on publication and does not block this package's release. External-tool
setup adoption belongs in a separate consumer follow-up. Consumers install a
pinned published version from PyPI, with no sibling checkout or machine-specific
artifact dependency.
Proceed to `causal-triangulations` after the pilot. Adoption need not follow Rust
dependency order.

Validate each adoption in the consuming repository. Local package validation
does not establish that a consumer workflow has migrated successfully.

## Dependency and tool update adoption

Adopt the [complete update recipe contract](../README.md#dependency-and-tool-updates)
as a unit. Retain consumer exclusions and extra Cargo resolution roots in
`update-cargo-dependencies`; Python-only consumers use the same template with
no Rust declarations or root Cargo manifest. The former `update-python-deps`
name now aliases the full Python update and sync workflow.

The shared contract deliberately supersedes these historical policies:

- uv is upgraded through its installation owner and its resulting stable pin is
  reconciled. Merely validating an already-installed stable uv is no longer the
  aggregate update policy.
- Just comes from the shared package's pinned `rust-just` dependency. It is not
  independently upgraded through Cargo. Changing the shared package pin remains
  an explicit consumer change.
- The managed Cargo updater replaces cargo-update; consumers do not need to keep
  that implementation dependency for parity.
- Cargo upgrades publish verified exact TOML declarations and retain old managed
  installations. They do not feed the legacy Just-variable pin reconciler.
- Dependency updates stop on failure but do not roll back earlier package-manager
  steps. Cargo exclusions constrain requirement upgrades, not independent policy
  for lockfile resolution.

The catalog includes cargo-machete, cargo-audit, samply, tectonic, and tex-fmt.
Their native build prerequisites and profiling/typesetting runtime configuration
remain consumer/platform responsibilities; see [tool installation](INSTALLING.md#additional-cargo-tools-and-native-prerequisites).
Declare tools in TOML only after provisioning those prerequisites. Keep any
consumer-owned prerequisite operations and checks during migration.

Before removing old helpers, run focused integration checks in each consumer
against its pinned published package, merged recipes, and configuration. Verify
the selected tools and their versions, dependency-only and tool-only boundaries,
failure propagation, full Python lock refresh with dev synchronization, and any
coupled Cargo exclusions or additional manifests. Keep domain checks in consumers.
Shared synthetic tests and local wheel evaluation do not replace these pinned
consumer checks. Release preparation and downstream adoption remain separate work.

## CI ownership after adoption

This repository owns tests, linting, type checks, and distribution checks for the
shared Python implementation. Consumers install a pinned release from PyPI and
keep thin recipes, local configuration, and focused integration checks that
exercise the commands they use. They can remove tests and checks for scripts
replaced by the package.

Rust builds, domain tests, repository-specific Python or notebook checks, and
validation of each consumer's generated artifacts remain in that consumer.

## CodeRabbit review adoption

The shared review contract follows the branch/uncommitted scopes and live-base
checks in markov-chain-monte-carlo's Just review helper. Consumer adoption is
separate from maintenance-tool migration and requires a published package version
containing the review capability. See the [review recipes](../README.md#coderabbit-review).

Replace duplicated review helpers with the packaged thin recipes. The common
default is verified `origin/main`, superseding local `main` defaults; explicit local
bases remain available. Instructions are discovered only at the configured root,
with `AGENTS.md` and exactly one CodeRabbit YAML configuration required. Keep
repository instructions and scientific validation local. Move common regressions
upstream before removing local helpers and retain focused integration checks for
the pinned package, recipes, and configuration. Live reviews remain explicitly
authorized work, outside routine checks and CI.

## Migrating Python helpers

Published `0.1.2` does not support process or transaction imports. Wait for a
subsequent published package containing the [public APIs](api.md#python-process-api),
pin that version, and run the consumer's focused integration checks before
removing local helper copies. Local wheels remain a pre-publication evaluation aid.

Replace executable lookup with `resolve_executable`; use `run_command` for captured
text, `run_command_bytes` for binary data, and `run_git_bytes` for Git input that
must reach clean filters unchanged. The text runner preserves newlines and raises
checked failures with raw byte diagnostics. `env` replaces the environment rather
than overlaying it. Use `run_command_live` for inherited input/output streams;
repository-specific command policy remains in the consumer.

Replace generic multi-file writers with `replace_many`, including single-file
writes as one-entry mappings. Encode payloads explicitly. Leaf symlinks are
rejected, overlapping targets fail before effects, and incomplete rollback exposes
recovery files through `RecoveryError` children of the exception group. This
supersedes local writers that follow output symlinks or discard recovery failures;
do not add compatibility flags to retain those behaviors. See the
[README example](../README.md#calling-from-python) and full publication contract.

Clippy SARIF consumers can also remove their separate Cargo installers and Just
version variables after adopting a release with the catalog additions. Move the
exact `clippy-sarif` and `sarif-fmt` pins to the managed Cargo table; keep Clippy's
arguments, managed Rust component declaration, SARIF upload, and pipeline failure
handling in the consumer.
