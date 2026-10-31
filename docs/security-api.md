# Security scanning and SARIF APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Security and documentation scan API](#security-and-documentation-scan-api)
- [SARIF API](#sarif-api)

## Security and documentation scan API

`research_repo_tools.security` exports:

- `scan_osv(settings, lockfiles, *, output="target/security", configuration=None)`:
  explicit relative `uv.lock`/`Cargo.lock` paths from the shared inventory. Each
  produces a numbered native JSON/SARIF pair with verified source coverage.
- `scan_secrets(settings, *, output="target/security", configuration=None, exclude=())`:
  full reachable history plus current files; rejects shallow history and selected
  links/submodules. Native patch/binary/archive limitations remain applicable.
- `security_inventory(root, *, include=(), exclude=())`: sorted tracked/nonignored
  regular files, excluding standard generated/environment directories. Inclusion
  patterns are Git pathspecs and exclusions are case-sensitive POSIX globs.

Scans require declared, already installed exact managed binaries. They return
zero only for complete scans without findings, otherwise the first native
nonzero status, 1 for invalid successful reports/findings, or 124 on timeout.
Invalid arguments/preconditions raise `ValueError`; I/O and Git errors propagate.
Old OSV reports at the selected output names are removed before scanning. OSV
also removes all prior numbered JSON/SARIF reports in its output directory,
including symlinks, so smaller inventories leave no stale reports. OSV and
Semgrep reject output directories with symlinks or Windows
junctions in any path component before creating directories or removing reports.
OSV preserves unrelated files and symlink targets. Semgrep owns and replaces
its complete output directory through `files.publish_directory`. Native
schemas and locations are retained, with Gitleaks source excerpts/commit messages
redacted in addition to native detected-secret redaction. Report directories are
caller-owned outputs. These commands are not an assurance that unknown secrets,
ignored inputs or unsupported binary formats are detected.

`semgrep_tool.setup(root, *, python=None)` verifies the exact scanner pin from
the installed `semgrep` extra in the executing Python environment and returns
its absolute executable `Path`. It checks the shared `python_baseline.DEVELOPMENT_PYTHON`
(currently 3.15), distribution version and executable version. `python` is an
explicit interpreter override used by top-level setup to verify the freshly
synchronized project environment. It never selects a separate scanner Python,
resolves dependencies, installs tools or falls back to an ambient scanner.
Missing extras/version drift raise `RuntimeError` with setup guidance.

Consumers request `research-repo-tools[semgrep]` in their locked dev graph.
`semgrep_tool.constraints()` returns the packaged compatibility overrides;
`semgrep_tool.adopt_manifest(text)` adds them for exact shared-package group pins
requesting that extra, preserving unrelated constraints and rejecting conflicts.
`toolchain adopt` includes this migration in its normal candidate validation and
rollback. uv overrides are root-project policy and do not propagate from package
metadata. Both `semgrep.check` and `semgrep_scan.scan` select the interpreter's
scanner directly; `semgrep setup` / `just semgrep-setup` verify the same selection.
Top-level setup synchronizes dev before verifying a declared scanner. Native
Linux/macOS/Windows validation is required for pin and override changes.

`semgrep_docs.rust_blocks(path)` returns line-padded Rust fence source strings
from UTF-8 Markdown or line/block rustdoc comments. It does not execute code or
change the source. Hidden Rust lines remain code; unclosed selected fences raise
`ValueError`. Macro-generated documentation and `#[doc = ...]` are not extracted.

`semgrep_scan.scan(settings, *, include, exclude=(), output="target/security/semgrep",
rust_docs=False, batch_size=None, failure_reports=None, inline_suppressions=None,
jobs=None, report_category=None, report_layout=None, rustdoc_include=None,
rustdoc_exclude=None, target_timeout=None)` runs the native
scanner once per bounded batch, generating JSON and SARIF in the same invocation.
Optional overrides follow `config.SemgrepSettings`: positive integer batch size,
jobs and target timeout; boolean inline suppressions; `aggregate` or `numbered`
layout; filename-safe report category. Defaults are 100 files, one job, 120
seconds per rule/target, disabled suppressions, aggregate layout and `semgrep`
category. Existing `semgrep.timeout` is the positive process/fixture timeout
(default 300 seconds). CLI flags use corresponding hyphenated names.

Both report formats must be fresh finite JSON and agree on active rule/path/line
findings. JSON must declare exactly the selected scanned inputs and no errors.
Explicit source files include tests normally ignored by Semgrep, while native
rule path filters remain active. Excluded fixtures are never selected.
With `rust_docs=True`, originals (including all Markdown) are scanned first and
each Rust fence is independently scanned with Rust rules at the same logical
repository-relative path. Root-anchored includes/excludes retain native Semgrep
semantics. Markdown examples require Rust rule scopes covering their `.md`
paths; generic/regex prose rules run on originals. Coverage is checked against
each physical input before projection, so one scanned block cannot mask another
skipped block. Failures include a bounded reason and missing/unexpected source
and block identities. Original line/column/byte positions are restored, and
identical overlapping rule/span/suppression findings are retained once.
Accepted SARIF suppressions and JSON `extra.is_ignored` findings are inactive
only when inline suppressions are enabled. Invalid pairs or process timeouts
clear reports by default (`failure_reports="clear"`). Opt-in `"retain"` preserves
the prior directory byte-for-byte on these failures. Generation/publication
exceptions preserve or restore it under either policy. Valid reports remain
available when findings or native status make the gate fail.

Every started scan invalidates `<output>.attempt.json` before native execution.
Only a complete committed generation marks `published=true`, with an attempt ID,
exit code and SHA-256 inventory. `require_current_reports(destination)` checks
that marker and exact report bytes; it raises on a failed attempt or changed
reports. The CLI equivalent is `semgrep check-reports --output PATH`. CI must
require both a successful scan gate and this check before SARIF upload when
retention is enabled. A valid report containing findings is current but its
scan status remains nonzero. Outputs and sidecars require exclusive ownership;
this does not serialize writers or provide crash atomicity.

Aggregate layout publishes `semgrep.json` and `semgrep.sarif`. JSON contains
combined `results`, `errors`, `paths.scanned`, `input_coverage`, `extraction_scope` and per-batch JSON
with native metadata in `batches`. Each coverage record has a logical `path` and
`representation` (`source` or `rust-block`); blocks also have a one-based `block`
number and inclusive `start_line`/`end_line`. Per-block coverage survives finding
deduplication. Numbered JSON reports carry the same coverage records per batch.
SARIF merges one run with deduplicated rule descriptors and reindexed rule,
artifact and invocation references; conflicting metadata/descriptors reject
aggregation. Unrelated run metadata must agree across batches. The declared
category followed by `/` becomes `automationDetails.id`, leaving the run ID
empty so GitHub recognizes the category. Numbered layout publishes `N.json` /
`N.sarif` pairs per batch, retaining native metadata and assigning distinct
categories. Both layouts replace the whole owned output directory, superseding
0.1.7's per-file double launches and partial numbered-file cleanup.

`check_documentation_fixtures(settings)` (also `semgrep.check(settings,
rust_docs=True)`) validates mixed native, Markdown prose and Rust-fence fixtures.
An annotation targets the next nonblank, non-fence line. A block-enabled rule
assertion targeting a selected Rust block belongs to that block; source-only
rules (including raw-comment regexes) retain original-text ownership even there.
Every selected block needs block-rule annotations. A block-only rule annotation
outside selected extraction scope fails. Positive counts and
negative assertions are checked per representation, without double-counting
matches owned by a block. Mismatches identify the original source and block.
Annotated fixtures ignore production path filters, as ordinary fixture checking
does. Count-based expectations remain in a separate fixture gate.

Rule `metadata.research-repo-tools.representations` optionally selects a nonempty,
distinct array of `source` and/or `rust-block`. Defaults retain source checks and,
for Rust rules, extracted checks. `rust-block` requires a Rust-language rule.
`rust-block-paths` optionally supplies validated native Semgrep `include`/`exclude`
patterns for the extracted representation only; otherwise original rule paths
apply. IDs and native/source rule definitions stay stable. Source/prose scanning
remains additive and requires source rules with coverage of every original.

`rustdoc_include` (default `("**",)`) and `rustdoc_exclude` (default `()`) use
case-sensitive root-relative POSIX globs to choose extraction independently of
original inventory. An empty include selects no extraction. JSON `extraction_scope`
records enabled status, patterns, selected paths and excluded paths; selected
physical blocks still require complete native coverage. Rust extraction and
fixture ownership use the same rule/scope policy. Native syntax diagnostics are
blocking; this is a Semgrep scan, not a Rust compiler or syntax-validity proof.

## SARIF API

`sarif.SarifPolicy(drivers: Mapping[str, tuple[str, ...]], category_prefix="analysis")`
selects exact driver names and rule-ID prefixes. The mapping is frozen; empty
prefix tuples keep all rules for that driver. TOML `[sarif]` supports
`category-prefix` and a `drivers` table of name-to-prefix arrays (prefixed with
`tool.research-repo-tools` in pyproject.toml). At least one driver is required.

`sarif.transform(document: object, policy) -> tuple[SarifOutput, ...]` validates
the whole document before selection and returns serialized immutable outputs
with `filename`, `category` and UTF-8 byte `payload`. It requires finite JSON,
version 2.1.0, a runs array, named drivers, unique rule IDs, object result/message
shapes and consistent rule references. Missing results/rules mean empty arrays;
explicit null is invalid. Legacy `ruleId`/`ruleIndex` and nested `rule.id` /
`rule.index` are supported, including ID-only findings without descriptor tables.
Extension `toolComponent` rule references are rejected. Invocations, when
present, must report successful execution. This validates the transform's
supported subset rather than every optional SARIF schema field.

Namespace-selected rules are retained even without findings. Runs without rules
or results are omitted. Rule indices in retained results, descriptor relationships,
notification `associatedRule` metadata and invocation `ruleConfigurationOverrides`
are reindexed. Supplied rule IDs and indices must agree; metadata referencing removed indices
rejects the transform. Arbitrary `properties` are preserved. Other root/run
metadata and automation fields remain unchanged except `automationDetails.id`.
Categories include the prefix, driver slug, driver-name digest and occurrence
among all input runs, including empty ones. Repeated names and slug collisions
have distinct categories; ordering same-name runs defines their stable identity.
Each `automationDetails.id` is the category followed by `/`, leaving the run ID
empty for GitHub code scanning. `SarifOutput.category` and filenames do not
include that separator.

`sarif.split(source: Path, destination: Path, policy) -> tuple[SarifOutput, ...]`
parses duplicate-free strict UTF-8 JSON, transforms and serializes all outputs
before publishing a complete directory generation. The source must be outside
the destination directory, including symlink and portable case/Unicode aliases. Invalid input leaves
the old generation unchanged; empty output removes stale files. CLI `sarif split SOURCE
--output DIRECTORY` uses the configured policy. Optional `--github-output PATH`
calls `ci.export_environment` after publication, exporting `SARIF_DIRECTORY`,
`SARIF_HAS_UPLOADABLE_RUNS` and `SARIF_RUN_COUNT`. That append is a separate
operation: failure reports nonzero after the SARIF directory has committed.
