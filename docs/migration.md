# Shared scope and adoption

The first version provides changelog generation, normalization, archiving, release
notes and local tags; release metadata synchronization; dependency pin maintenance;
Semgrep fixture validation; Markdown line checks; and coverage summaries.

Consumers install one package and retain thin wrappers that invoke its commands.
Common defaults live here: `CHANGELOG.md`, `docs/archives/changelog/`, canonical
Cargo SemVer pins (including prereleases and build metadata), stable uv pins,
and staged file replacement with rollback diagnostics.

Repository-specific scientific code, benchmark cases, notebooks, custom commands,
and Semgrep rules remain in consumers. This package checks supplied Semgrep rules;
it does not distribute a rule set copied from a particular repository. Custom
commands and release-pinned links are not rewritten during metadata updates.

The initial extraction copied too much. Copied repository trees, reference
implementations, historical test runners, consumer profiles, and one-time
migration scripts have been removed. Tests now execute only the shared package
and use synthetic inputs grouped by capability. Equivalent regression cases were
merged; applicable edge and failure cases were retained. Attribution is recorded
in [provenance](provenance.json), without shipping the original repositories.

Notebook, benchmark, plotting, and evidence tooling are deferred. Their previous
implementations and tests are not part of this package. Future additions should
first establish a common contract and its minimal representative fixtures. A
common evidence contract should cover source identity, environment, inputs,
measurement validity, and comparison compatibility; scientific case definitions
belong to the consumer.

To adopt the package:

1. Install a local wheel or editable checkout with uv and lock the dependency.
2. Replace one duplicate script with a thin call to the shared command.
3. Run the consumer's own checks against that command before removing its old script.
4. Keep special scientific and deployment behavior in the consumer.

No consumer has been changed by this consolidation. Local package validation
does not establish that every consumer workflow has migrated successfully.
