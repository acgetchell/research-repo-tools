# Shared scope and adoption

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
commands and release-pinned links are not rewritten during metadata updates.

Tests exercise the shared implementation with small synthetic inputs grouped by
capability, including representative edge and failure cases. Fixes and release
history are recorded in the generated
[CHANGELOG.md](../CHANGELOG.md).
Historical feature-name or sentence migrations remain consumer-owned;
normalization preserves those names in prose, code spans, and link destinations.

[Shared toolchain setup](INSTALLING.md) provides generated uv launchers and
explicit installation and verification of declared Python, Rust, and Cargo tools.
Native installer and isolated installed-package validation are required before
closing issue #2. Issue #1 covers publication and a clean PyPI bootstrap check;
consumer migrations are separate downstream issues. Generic notebook infrastructure remains
planned: environment/kernel setup, execution, cleanup, and validation. Scientific
notebook content and experiment choices remain consumer-owned.

Notebook, benchmark, plotting, and evidence tooling are deferred. Future additions should
first establish a common contract and its minimal representative fixtures. A
common evidence contract should cover source identity, environment, inputs,
measurement validity, and comparison compatibility; scientific case definitions
belong to the consumer.

To adopt the package:

1. Configure the tooling group and bootstrap using the [toolchain guide](INSTALLING.md).
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

## CI ownership after adoption

This repository owns tests, linting, type checks, and distribution checks for the
shared Python implementation. Consumers install a pinned release from PyPI and
keep thin recipes, local configuration, and focused integration checks that
exercise the commands they use. They can remove tests and checks for scripts
replaced by the package.

Rust builds, domain tests, repository-specific Python or notebook checks, and
validation of each consumer's generated artifacts remain in that consumer.
