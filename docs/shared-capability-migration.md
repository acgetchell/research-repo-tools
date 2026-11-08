# Shared capability migration

These maps describe current replacements and retained consumer responsibilities.
Check [release availability](../README.md#release-availability), adopt an exact
registry pin, and run the consumer's native checks before deleting local helpers.
Development pins and local wheels support rehearsal. Release history belongs in
[CHANGELOG.md](../CHANGELOG.md).

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

Move representative shared contracts upstream: checksum/asset/version failures,
archive rejection, stale reports, status preservation, redaction, shallow history,
uncommitted-file coverage, Python migration rollback, first-release preparation,
and notebook/static-analysis source preservation. Delete duplicate generic tests
only once focused integration tests pass with the exact installed release.
Keep scientific fixtures and consumer-specific assertions in the consumer.

If a consumer's existing `clean` recipe removes build or custom coverage artifacts,
keep that repository-specific work in a separate recipe when adopting the shared `clean`
contract. Shared cleanup handles obsolete owned installations and the
[standard retained Python coverage layout](INSTALLING.md#cleaning-retained-coverage),
while preserving current coverage reports. New package-managed
Python installs use the private store; existing user-wide uv interpreters remain
user-owned. See the [installation guide](INSTALLING.md#cleaning-obsolete-installations).

Starting in v0.1.8, the installed package's Python support minimum applies to
managed consumers regardless of development opt-ins. This supersedes v0.1.7
policies that allowed application metadata below that minimum. Development-selector
mirroring remains opt-in; explicit lower lint/type targets remain consumer policy.
See [shared Python adoption](UPDATING_DEPENDENCIES.md#shared-python-adoption) before updating
the exact package pin. Unreleased-only
changelogs are accepted only during explicit first-release preparation; final
release validation remains strict. Native scanner schemas and Semgrep assertion
semantics remain upstream-owned rather than becoming consumer-specific variants.

Review adoption on Linux, macOS and Windows. Local deterministic models supplement
native installed-package checks; a successful host-only check does not establish
another platform's support. See [task guide](../README.md) for operational commands
and [CONTRIBUTING](../CONTRIBUTING.md) for package development.

## SARIF and batched scan adoption

Pin a published version containing these interfaces and verify the consumer's
scan and upload paths before retiring its working implementations. An upstream
merge or local wheel evaluation does not establish installed consumer behavior.

| Consumer implementation to remove | Shared replacement | Retained consumer responsibility |
| --- | --- | --- |
| `scripts/ci/filter_codacy_sarif.py` parsing, filtering, splitting, staging and command-file writer | `sarif split`, consumer `sarif.drivers`, `files.publish_directory`, `ci.export_environment` | Codacy driver names/namespaces, upload conditions, permissions and category prefix |
| `_copy_file`, `_replace_path` and generic directory swap in `scripts/notebook_validation_rendering.py` | `files.publish_directory`, copying or rendering into the yielded directory | Figure names, rendering, complete-set validation and scientific assertions |
| Shell target-array and native launch/report plumbing in `just semgrep-scan` | `semgrep scan` with the same shared inventory for the findings gate and upload | Include/exclude patterns, deliberate fixture exclusions, inline suppression review and rule YAML |
| Native scan launch in `.github/workflows/semgrep-sarif.yml` | Thin consumer recipe calling the configured shared scanner and uploading `semgrep.sarif` | Actions pins, permissions, fork/upload conditions and stable category policy |

Generic upstream regressions cover finite/duplicate-free input, metadata and rule
index preservation, repeated driver names, empty/stale generations, staging and
commit failure, retained recovery trees, bounded native launches, paired-report
agreement, exact coverage and suppression policy. Keep focused consumer recipe,
pinned-package, Codacy selection and figure/scientific policy tests downstream.

Batched Semgrep replaces older two-launch-per-file wrappers with one launch
per bounded batch. Its dedicated directory replaces every prior member rather
than preserving unrelated files; choose a separate output directory. Aggregate
SARIF uses one run/category. Configure reviewed suppressions explicitly instead
of retaining the former unconditional `--disable-nosem` policy. Defaults are
one job and a 120-second rule/target timeout; rules and exclusions stay local.

## Paper and raw line adoption

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

Upstream regressions use synthetic TeX and complete PDFs. They cover date/comment
and calendar failures, deterministic/idempotent metadata, malformed/encrypted
PDFs, per-page differences, validated refresh/publication failures, optional
extras, read-only discovery, all-line Unicode counts, missing final newline,
empty selection and fail-closed discovery. Both installed distributions run
these contracts on each supported native CI host. Native pkg-config fixtures
use the actual executable; Windows fixtures use native triplet-directory paths.
They verify discovery rather than native library compilation. Keep focused
consumer recipe, exact-pin, title/references, figure and scientific tests local.

The shared normalizer validates complete input/candidate PDFs before replacement;
the former malformed metadata-only byte stubs no longer qualify as PDFs. Info
dates must match the source epoch, including compressed objects. Unsupported
profiles fail without replacing output. Build into staging paths before deciding
to refresh retained artifacts, rather than deleting valid outputs before success.
The strict `files check-lines` gate counts tables, fences and URLs; the existing
`docs check-lines` Markdown table exemption remains its separate policy.
Native PDF bytes may differ while page text, boxes, rotation and units match.

## Performance and release helper adoption

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
