# Shared capability migration

The baseline capabilities below shipped in v0.1.7. Consumers adopt
an exact registry pin after publication and run their complete native gate;
they do not depend on sibling checkouts. Release history belongs in
[CHANGELOG.md](../CHANGELOG.md).

| Consumer implementation | Shared replacement | Retained consumer responsibility |
| --- | --- | --- |
| Repeated Python baseline literals and migration scripts | Installed `python_baseline`, `python_adoption`, and `toolchain adopt` | Opt-in declaration, exact package pin, public runtime support, lint policy and exceptions |
| Cargo deny installer/version wrapper | `toolchain.cargo.cargo-deny`, setup/check/run/export/upgrade | `deny.toml`, accepted advisories/licenses and scan scope |
| First-release special cases and fake predecessor tags | `release update --first-release`, validated release plans | Canonical target, release date, offline intent and publication |
| Notebook dependency installation checker | `notebooks.prohibit-installs`, blocking notebook lint | Opt-in policy, dependencies in locked groups, unsupported dynamic-call review |
| `tooling/security_tools.py`, `tooling/security-tools.toml` | Exact `toolchain.binaries`, verified release installation | Selected versions and consumer setup/cache wrappers |
| `tooling/security_scan.py` | `security osv`, `security secrets`, shared portable inventory | Lockfile selection, allowlists, schedules, workflow permissions and report upload |
| Generic inventory/report and Rust-doc parts of `tooling/semgrep.py` | `semgrep scan --rust-docs`, `semgrep check-fixtures --rust-docs` | Rules, scientific/model-loading restrictions, annotated fixtures and selected paths |
| Old package-managed tool installation cleanup | `toolchain clean` through the packaged `just clean` recipe | Retention roots for other consumers sharing the store |

Move representative shared contracts upstream: checksum/asset/version failures,
archive rejection, stale reports, status preservation, redaction, shallow history,
uncommitted-file coverage, Python migration rollback, first-release preparation,
and notebook/static-analysis source preservation. Delete duplicate generic tests
only once focused integration tests pass with the exact installed release.
Keep scientific fixtures and consumer-specific assertions in the consumer.

La-stack's former `clean` recipe removes build and coverage artifacts. Keep that
repository-specific work in a separate recipe when adopting the shared `clean`
contract. Shared cleanup handles obsolete owned installations. New package-managed
Python installs use the private store; existing user-wide uv interpreters remain
user-owned. See the [installation guide](INSTALLING.md#cleaning-obsolete-installations).

The former independent Python target mirrors are superseded only for opted-in
consumers. Explicit lower lint/type targets remain consumer policy. Unreleased-only
changelogs are accepted only during explicit first-release preparation; final
release validation remains strict. Native scanner schemas and Semgrep assertion
semantics remain upstream-owned rather than becoming consumer-specific variants.

Review adoption on Linux, macOS and Windows. Local deterministic models supplement
native installed-package checks; a successful host-only check does not establish
another platform's support. See [README](../README.md) for operational commands
and [CONTRIBUTING](../CONTRIBUTING.md) for package development.

## SARIF and batched scan adoption

These capabilities target v0.1.8 ([#72](https://github.com/acgetchell/research-repo-tools/issues/72),
[#74](https://github.com/acgetchell/research-repo-tools/issues/74)). Pin an exact
published PyPI version containing the interfaces, then validate the paired
delaunay adoption before retiring its working implementations. An upstream merge
or local wheel evaluation alone does not satisfy that publication prerequisite.

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

The new Semgrep contract replaces 0.1.7's two launches per file with one launch
per bounded batch. Its dedicated directory replaces every prior member rather
than preserving unrelated files; choose a separate output directory. Aggregate
SARIF uses one run/category. Configure reviewed suppressions explicitly instead
of retaining the former unconditional `--disable-nosem` policy. Defaults are
one job and a 120-second rule/target timeout; rules and exclusions stay local.

## Paper and raw line adoption

These interfaces target v0.1.8 ([#70](https://github.com/acgetchell/research-repo-tools/issues/70),
[#76](https://github.com/acgetchell/research-repo-tools/issues/76)). Adoption requires
an exact published PyPI release containing them. This change does not implement
[delaunay#625](https://github.com/acgetchell/delaunay/issues/625),
[delaunay#633](https://github.com/acgetchell/delaunay/issues/633), or the separate
[rollout](https://github.com/acgetchell/research-repo-tools/issues/69).

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
