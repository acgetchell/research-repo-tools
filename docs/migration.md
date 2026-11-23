# Migrating consumer repositories

Use this guide when upgrading a pinned release or replacing local helpers.
For first-time setup, follow the [consumer quick start](../README.md#quick-start).
For a helper-by-helper comparison, use the [capability migration map](#shared-capability-migration).

## Upgrading from v0.3.1 to v0.3.2

After publication, follow [shared Python adoption](UPDATING_DEPENDENCIES.md#shared-python-adoption)
with `VERSION=0.3.2`. Merge `changelog-rebuild` from the matching Justfile
to [preview and apply a full history rebuild](GENERATING_CHANGELOGS.md#rebuilding-retained-history).
Benchmark consumers can [preflight a preparation commit](candidate-reuse-api.md#offline-preparation-preflight)
or [start a series for a new workload](REUSING_BENCHMARKS.md#starting-a-benchmark-series-after-earlier-releases).

Update any automation that enumerates
CodeRabbit outcomes. An included review with partial coverage now emits
`status="incomplete"` in the terminal `rrt_review_result` record and exits 1.
A paid attempt with partial coverage reports `status="uncertain"` and requires
billing reconciliation. Both carry the finding count and unreviewed file details.
Keep the push gate closed. See the [Python results and CLI fields](review-api.md#calls-and-results).

## Upgrading from v0.3.0 to v0.3.1

The v0.3.0 Justfile and `python-bootstrap.py` can perform this upgrade. Follow
[shared Python adoption](UPDATING_DEPENDENCIES.md#shared-python-adoption) with
`VERSION=0.3.1`, including the preview, update and consumer checks. That guide
also covers recovering either template from the current pin. Once v0.3.1 is
installed, merge its [supplier recipe and configuration](UPDATING_DEPENDENCIES.md#automatically-adopting-a-published-supplier)
to opt into subsequent automatic upgrades.

## Mandatory Python support inheritance

Starting with v0.1.8, an installable consumer's `project.requires-python` must
enforce the installed tooling release's published minimum, including installations
without development dependencies. Previously consumers could update only their
development settings. A stricter application minimum, upper bound or exclusion
still applies. Opting out of development interpreter mirroring or the tool profile
keeps the minimum in force. See [selector and range handling](UPDATING_DEPENDENCIES.md#shared-python-adoption).

## Python 3.15 validation contract

Migrate legacy Python 3.14 selectors using the bootstrap from the pinned release.
It can start on an older interpreter and select a compatible one for the target.
After updating the metadata, selector and lock, build the consumer's wheel and
sdist. Check their `Requires-Python` and test installation on supported and
unsupported interpreters.

Replace copied validation runners with the [packaged Python checks](RUNNING_CHECKS.md#workflow-security-and-complete-python-validation).
The gate runs format, lint and strict types (`ty --error all`) in order, stopping
on failure. Fixes run lint fixes before formatting. Keep local rules and fixture
exceptions, and include the same selected files in every check.

## Python API changes for v0.2.7

Release v0.2.7 changed these public Python constructors to represent valid
alternatives explicitly. Update custom callers when upgrading from earlier
releases; do not depend on the old combinations of optional fields.

| Previous Python call | v0.2.7 replacement | Contract |
| --- | --- | --- |
| `Estimate(point, lower, upper, confidence_level)` | `Estimate(point, ConfidenceInterval(lower, upper, confidence_level))`, or `Estimate(point)` without an interval | [Timing estimates](performance-api.md) |
| `TrialSchedule(6, order="randomized", seed=42)` | `TrialSchedule(6, RandomizedOrder(42))`; use `AlternatingOrder()` or `SequentialOrder()` for the other orders | [Trial schedules](complete-run-api.md#scheduled-trials) |
| `ReleaseRule(..., value=text)` or `ReleaseRule(..., source="tag")` | `ReleaseRule(..., replacement=FixedValue(text))` or `replacement=ReleaseSource.TAG` | [Release rules](publishing-api.md) |

The corresponding TOML/JSON transports retain their documented field names;
their parsers construct the typed alternatives. Read the current signatures
and invariants in the linked contracts, and validate the exact installed pin
with the consumer's integration checks. Internal scanner and notebook helper
classes are not public compatibility surfaces.

## Shared and consumer ownership

Install an [exact published release](../README.md#release-availability), merge
the recipes your project uses, and exercise each replacement in consumer CI
before retiring its helper. Choose the migration guide for the local code.

| Local implementation | Migration guide |
| --- | --- |
| Tool installers, scanners, paper checks and CI helpers | [Capability map and ownership](#shared-capability-migration) |
| Notebook checkers that extract Python files | [Notebook adoption](RUNNING_NOTEBOOKS.md#adoption) |
| Benchmark measurements, retained evidence or published figures | [Performance migration](performance-migration.md) and [document publication](PUBLISHING_REPORTS.md#document-publication-migration) |
| Release discovery, metadata and publication wrappers | [Release policy migration](release-policy-migration.md) |
| Static Cargo example lists | [Cargo example migration](cargo-examples-api.md#migrating-static-cargo-example-validation) |

## CI ownership after adoption

The shared package tests generic behavior from source, wheel and sdist. Replace
local tests of a retired helper with focused checks of the installed pin,
recipes and configuration. Keep native builds, scientific workloads, notebook
content, generated artifacts and deployment approvals in the consumer.

## Dependency and tool update adoption

Merge the [current update recipes](UPDATING_DEPENDENCIES.md#dependency-and-tool-updates)
into the consumer's Justfile. An installed upgrade does not rewrite a copied recipe.

| Retired recipe | Replacement |
| --- | --- |
| `changelog-unreleased` | `changelog-release` |
| `help-workflows` | `help` |
| `update-python-deps` | `update-python-dependencies` |

Keep Cargo exclusions and extra resolution roots in `update-cargo-dependencies`.
Declare independent uv projects in `deps.python-projects` so the shared Python
recipe updates all their locks. Before removing source-index workarounds or audit
wrappers, verify named indexes and overrides, downgrade rejection, all-project
rollback and the final audit against the consumer's maintained locks. A failed
post-update audit leaves the updated files available for repair.

## CodeRabbit review adoption

Replace local review wrappers with the [review recipes](RUNNING_CHECKS.md#coderabbit-review).
The default base moved from local `main` to verified `origin/main`. Pass a local
base explicitly when needed. Keep `AGENTS.md` and exactly one CodeRabbit YAML
configuration at the configured root. Run live reviews only when authorized.

## Migrating Python helpers

| Local helper | Shared replacement and migration detail |
| --- | --- |
| Executable lookup and child processes | Use [the process API](process-api.md#python-process-api). Choose captured text, bytes or live streams explicitly. A supplied `env` replaces the inherited environment. |
| Git status and changed-path lists | Use [`git_status_bytes` and `git_diff_paths_bytes`](process-api.md#python-git-inspection-api). Split stdout on NUL. Handle diff statuses 0 and 1 and propagate fatal errors. Keep merge-base selection and staging decisions local. |
| Multi-file writers | Use [`replace_many`](files-api.md#python-file-publication-api) with explicitly encoded bytes. Output symlinks and overlapping targets fail. Surface any `RecoveryError` and its retained files after incomplete rollback. |

## Portable update and Actions policy adoption

- Run setup explicitly after changing tool declarations. Aggregate updates now
  upgrade uv through its installation owner and reconcile the project pin.
  Just comes from the tooling package's pinned `rust-just` dependency.
- Move prebuilt dprint and rumdl pins out of Just variables and into managed
  declarations where a binary is available. Managed Cargo tools no longer need
  `cargo-update`. Repair missing current installations with setup before upgrading.
  Keep user-wide Cargo upgrades and conditional Homebrew steps in the
  [owner-specific recipes](UPDATING_DEPENDENCIES.md#mixed-installation-owners-on-linux-and-macos).
- For Clippy SARIF, move `clippy-sarif` and `sarif-fmt` pins into the managed
  Cargo table. Retain the Rust component, Clippy arguments, upload and pipeline
  failure handling. Provision [native prerequisites](INSTALLING.md#additional-cargo-tools-and-native-prerequisites)
  for the tools your consumer selects.
- Replace duplicated allowlist regexes with the consumer's strict selected-actions
  JSON. Preserve exact SHA restrictions, list each action subpath and reusable
  workflow, and set both broad approval flags to false. Include composite metadata
  in both the check and updater inputs. Exercise the shared gate before retiring
  the local rules. Pin updates remain [opt-in by identity, scope and target](UPDATING_DEPENDENCIES.md#actions-allowlists-and-opt-in-pin-updates).

## Shared capability migration

| Consumer implementation | Shared replacement | Retained consumer responsibility |
| --- | --- | --- |
| Repeated Python baseline literals and migration scripts | Installed `python_baseline`, `python_adoption`, and `toolchain adopt` | Development opt-ins, exact package pin, application compatibility, lint policy and exceptions |
| Cargo deny installer/version wrapper | `toolchain.cargo.cargo-deny`, setup/check/run/export/upgrade | `deny.toml`, accepted advisories/licenses and scan scope |
| First-release special cases and fake predecessor tags | `release update --first-release`, validated release plans | Canonical target, release date, offline intent and publication |
| Notebook dependency installation checker | `notebooks.prohibit-installs`, blocking notebook lint | Opt-in policy, dependencies in locked groups, unsupported dynamic-call review |
| `tooling/security_tools.py`, `tooling/security-tools.toml` | Exact `toolchain.binaries`, verified release installation | Selected versions and consumer setup/cache wrappers |
| `tooling/security_scan.py` | `security osv`, `security secrets`, shared portable inventory | Lockfile selection, allowlists, schedules, workflow permissions and report upload |
| Generic inventory/report and Rust-doc parts of `tooling/semgrep.py` | `semgrep scan --rust-docs`, `semgrep check-fixtures --rust-docs` | Rules, scientific/model-loading restrictions, annotated fixtures and selected paths |
| Isolated scanner-project launchers | Shared compatibility range and overrides, with the exact scanner selected by the consumer lock and verified by `semgrep setup` | Rule configuration, representation/extraction scope, fixtures, suppression policy and current-report upload gate |
| Old package-managed tool installation cleanup | `toolchain clean`, included in the packaged `just clean` recipe | Retention roots for other consumers sharing the store |
| Python coverage shard/run cleanup | `coverage clean` through `just clean-coverage`, also included in `just clean` | Custom artifact layouts and the decision to discard failed-run evidence |

The [CI ownership guide](#ci-ownership-after-adoption) explains
which tests to move upstream when retiring a helper.

If a consumer's existing `clean` recipe removes build or custom coverage artifacts,
keep that repository-specific work in a separate recipe when adopting the shared `clean`
contract. Shared cleanup handles obsolete owned installations and the
[standard retained Python coverage layout](INSTALLING.md#cleaning-retained-coverage),
while preserving current coverage reports. New package-managed
Python installs use the private store; existing user-wide uv interpreters remain
user-owned. See the [installation guide](INSTALLING.md#cleaning-obsolete-installations).

For projects upgrading from v0.1.7, check [mandatory Python support inheritance](migration.md#mandatory-python-support-inheritance).
For first-time publication, an Unreleased-only changelog is accepted during
[first-release preparation](UPDATING_RELEASE_METADATA.md#first-release-preparation).
Final release validation requires the dated target heading.

### SARIF and batched scan adoption

| Consumer implementation to remove | Shared replacement | Retained consumer responsibility |
| --- | --- | --- |
| `scripts/ci/filter_codacy_sarif.py` parsing, filtering, splitting, staging and command-file writer | `sarif split`, consumer `sarif.drivers`, `files.publish_directory`, `ci.export_environment` | Codacy driver names/namespaces, upload conditions, permissions and category prefix |
| `_copy_file`, `_replace_path` and generic directory swap in `scripts/notebook_validation_rendering.py` | `files.publish_directory`, copying or rendering into the yielded directory | Figure names, rendering, complete-set validation and scientific assertions |
| Shell target-array and native launch/report plumbing in `just semgrep-scan` | `semgrep scan` with the same shared inventory for the findings gate and upload | Include/exclude patterns, deliberate fixture exclusions, inline suppression review and rule YAML |
| Native scan launch in `.github/workflows/semgrep-sarif.yml` | Thin consumer recipe calling the configured shared scanner and uploading `semgrep.sarif` | Actions pins, permissions, fork/upload conditions and stable category policy |

Exercise the consumer's Codacy selections, upload conditions and scientific
figure assertions against the installed package.

Batched Semgrep replaces older two-launch-per-file wrappers with one launch
per bounded batch. Its dedicated directory replaces every prior member rather
than preserving unrelated files; choose a separate output directory. Aggregate
SARIF uses one run/category. Configure reviewed suppressions explicitly instead
of retaining the former unconditional `--disable-nosem` policy. Defaults are
one job and a 120-second rule/target timeout; rules and exclusions stay local.

### Paper and raw line adoption

Adopt these interfaces through a published pin and focused paper/build
integration checks. Scientific content, native TeX builds, and retained
artifact refresh decisions remain consumer-owned.

| Consumer implementation to remove after published adoption | Shared replacement | Retained consumer responsibility |
| --- | --- | --- |
| `scripts/paper_source_date.py`, its entry point and generic date tests | `papers source-date`, `paper_dates` | Explicit TeX dates, source paths and build recipe exporting `SOURCE_DATE_EPOCH` |
| `scripts/paper_pdf_normalize.py`, its entry point and generic metadata/atomic-write tests | `papers normalize`, `paper_pdf.normalize_pdf` and shared file publication | Stable identity, candidate/output paths, refresh intent and supported builder profile |
| `scripts/paper_check.py`, its entry point and generic parsing/text/geometry tests | `papers check`, `paper_pdf.check_pdf` and `compare_structure` | Title/reference expectations, forbidden text, minimum pages and scientific/visual assertions |
| `scripts/tectonic_native_dependencies.sh` generic discovery and shell-specific tests | `tectonic discover`/`export`, `tectonic.discover_environment` | Explicit host provisioning, SDK/library versions, permissions and environment application |
| `just markdown-check` target arrays and raw-line shell loop | `files check-lines` using shared selection | Active Markdown includes, generated/history excludes and 160-character limit |
| Duplicated Markdown discovery in check/fix recipes | `files run` with consumer include/exclude declarations | rumdl policy and explicit formatting intent; the raw gate never formats |
| Generic paper launches and discovery exports in recipes and `.github/workflows/papers.yml` | Thin configured `papers`/`tectonic` wrappers | TeX lint/format, compilation, figures, cache ownership, CI triggers and uploads |

Test the consumer's native build, title and references, figures and scientific
assertions. The [paper guide](BUILDING_PAPERS.md) covers the build environment
and artifact checks.

The shared normalizer validates complete input/candidate PDFs before replacement;
the former malformed metadata-only byte stubs no longer qualify as PDFs. Info
dates must match the source epoch, including compressed objects. Unsupported
profiles fail without replacing output. Build into staging paths before deciding
to refresh retained artifacts, rather than deleting valid outputs before success.
The strict `files check-lines` gate counts tables, fences and URLs; the existing
`docs check-lines` Markdown table exemption remains its separate policy.
Native PDF bytes may differ while page text, boxes, rotation and units match.

### Performance and release helper adoption

Replace generic support code by capability; keep scientific and deployment
policy in the consuming repository. Follow the [performance migration](performance-migration.md)
and [release-policy example](release-policy-migration.md) for detailed contracts.

| Consumer implementation | Shared or native replacement | Retained consumer responsibility |
| --- | --- | --- |
| Release-pair discovery, source capture and worktree orchestration | Prepared performance workflow, explicit source/harness modes and mutation controls | Independent release selection, scientific cases, native commands and gates |
| Criterion sample copying and comparison helpers | Configured acquisition, saved-sample comparison and provenance | Workload definitions, sample selection and timing interpretation |
| Archive download, extraction and legacy evidence conversion | Authenticated assets, bounded extraction and explicit conversion | Repository/asset declarations, original historical bytes and conversion acceptance |
| Report/index promotion and README rendering | Retained-report and document publication | Report paths, selected rows, scientific interpretation and figure policy |
| Release metadata discovery, staging and output parsing | Declarative release rules and structured plans | DOI assertions, required files, historical exclusions and publication decisions |
| Draft lookup, attachment and retry wrappers | Captured release targets and verified asset commands; v0.2.7 adds draft preparation | Signing, trusted handoffs, workflow credentials, triggers and approval |
| Generic setup, file discovery and process checks | Managed toolchains, file selection and command validation | Native prerequisites, cache declarations, output markers and recipe ordering |
| Notebook, example, coverage and scan wrappers | Shared notebook/example commands and native Cargo/scanner tools | Trace/figure assertions, domain tests, exclusions, permissions and report upload |

Keep focused consumer tests for benchmark names, fixtures outside timed regions,
scientific result interpretation, notebook/figure behavior, release configuration,
and installed-command wiring. Generic parsing, Git byte transport, rollback,
asset retry and command-file safety belong in the shared package's tests.
Remove a consumer's Python package scaffolding only if it no longer distributes
Python functionality; preserve notebook/tool groups and domain Python modules.

Before deleting a helper or test group, identify its shared replacement,
configuration/native replacement, or independently justified obsolescence.
Deletion counts are not an acceptance criterion. Retire historical converters
only after retained artifacts have verified shared-format companions and no
active workflow invokes the conversion path; keep immutable originals.
Shared native wheel/sdist checks establish package behavior. Consumer integration
checks establish adoption, and neither substitutes for the other.
