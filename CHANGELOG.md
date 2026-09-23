# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.5] - 2026-09-23

### Added

- Add read-only inspection and configurable advice
  [`9946fc7`](https://github.com/acgetchell/research-repo-tools/commit/9946fc7212513d9ca6c358c8afb071e7f53abf38)

  - Add text and versioned JSON inventories for nbformat 4 notebooks, with source-preview controls and structural repair diagnostics.
  - Add descriptive-ID warnings, configurable native Ruff rules, and optional subprocess timeout advice with opt-in strict mode.
  - Reject invalid Unicode before notebook execution and prevent raw JSON values from leaking through parser diagnostics.
  - Honor the consumer root for executable lookup, export paths, and uv synchronization despite ambient project selectors.
  - Preserve UTF-8 and newlines in generated CLI output, retain subprocess diagnostics, and handle grouped expected failures.
  - Validate supplied release asset digests before downloading and reject portable aliases of Git metadata paths.
  - Update the uv pin to 0.12.18 and document notebook adoption, release publication, and shared workflow ownership.

### Fixed

- Suppress parser warnings during timeout advice [`3b6cff7`](https://github.com/acgetchell/research-repo-tools/commit/3b6cff7520576279519ad6a065031f17f2a0419b)

  - Suppress SyntaxWarning only while parsing cells, preventing stderr leakage and false syntax skips under warnings-as-errors.
  - Preserve genuine syntax-error handling and caller warning filters.
  - Clarify that download_release_asset saves verified assets and requires a separate extract_archive call to unpack them.

## [0.1.4] - 2026-09-22

### Added

- Add configured benchmark and release workflows [`4384fce`](https://github.com/acgetchell/research-repo-tools/commit/4384fcefce0db09c434791649027fc1e324ff3af)

  - Select release pairs and measure benchmarks in isolated worktrees with source, harness, toolchain, and dependency provenance.
  - Manage authenticated baseline assets and draft release uploads with hash verification, safe retries, and optional publication.
  - Convert legacy evidence while preserving original hashes, and support report promotion, archival, offline rendering, and CSV export.
  - Prepare future-release documents with source inventory rechecks and exact verification against existing release tags.
  - Add portable file selection, command batching, configured validation, and checked CI and toolchain environment exports.
  - Support canonical release tags and dependency-only Python environments, with consumer templates and MCMC migration guidance.

## [0.1.3] - 2026-09-22

### Added

- Add managed SARIF tools and public Python utility APIs
  [`5e1290e`](https://github.com/acgetchell/research-repo-tools/commit/5e1290e5dea6a32d908131f780b6b64821bbdaec)

  - Manage clippy-sarif and sarif-fmt with exact locked installation, checked execution, and upgrades limited to declared tools.
  - Expose executable resolution and text/byte command runners that preserve Git input bytes and original failure diagnostics.
  - Support transactional byte publication with target validation, preserved permissions, rollback, and structured recovery errors.
  - Document API compatibility, platform limits, and consumer migration.
- Add configurable policies and structured plans [`cf4331f`](https://github.com/acgetchell/research-repo-tools/commit/cf4331fa151234802d18b1ae11264688b065c51e)

  - Declare required files, fixed metadata, and active release references with exclusions for historical evidence.
  - Expose typed discovery, checks, plans, and consumer adapters for contributing edits and validating complete candidates.
  - Apply the exact previewed bytes with stale-input checks and transactional rollback; reject fixed DOI mismatches before editing.
  - Document replacing MCMC's release orchestration with configuration and small adapters while retaining consumer-owned evidence policy.
- Add Criterion comparison and evidence APIs [`b3de44f`](https://github.com/acgetchell/research-repo-tools/commit/b3de44f622f304a3b04ef7cb382165b88279939f)

  - Add validated timing comparisons with explicit units and complete common, added, and missing benchmark inventories.
  - Preserve exact evidence bytes with deterministic serialization, source and harness provenance, and explicit compatibility checks.
  - Support bounded asset retrieval, safe archive extraction, and recoverable publication with immutable evidence and alias protection.
  - Add performance commands for comparison, extraction, retrieval, retained-data rendering, and verification.
  - Document retained-evidence migration while keeping benchmark execution and scientific acceptance policies in consumers.
- Add evidence-backed document publication [`1ad1c56`](https://github.com/acgetchell/research-repo-tools/commit/1ad1c56d3c3a64a57fb278ac7ea553423a5a3d5c)

  - Add Python publication plans and a TOML-driven performance publish command with check and preview modes.
  - Render selected timing tables and optional SVGs with explicit labels, units, and links, without plotting dependencies.
  - Verify provenance, release references, and exact tagged artifact bytes before publishing documents and figures with snapshot checks and rollback.
  - Preserve surrounding document bytes, historical links, and UTF-8 preview output across platforms.
  - Document migration through consumer schema and renderer adapters.
  - Share the Git-mutation opt-out across checkout and installed consumer suites while retaining full coverage by default.

### Changed

- Make public API consumer checks portable [`08140cf`](https://github.com/acgetchell/research-repo-tools/commit/08140cf1b2ddf24de5565339130b5be782404218)

  - Resolve relative interpreter paths without crossing Windows drives.
  - Explicitly use UTF-8 for the Unicode subprocess probe so inherited encodings cannot cause decoding failures.

### Fixed

- Handle discovery errors and Windows check failures
  [`4114c94`](https://github.com/acgetchell/research-repo-tools/commit/4114c94f78c32b6ad028fa2f5af909841f9d8fff)

  - Report failed or timed-out release discovery on stderr and return  status 1 instead of propagating subprocess exceptions.
  - Describe release updates as transactional with rollback on failure, removing the inaccurate atomicity claim.
  - Use explicit LF and CRLF fixtures so release-policy checks preserve exact bytes consistently across platforms.
- Protect performance inputs and enforce portable text writes
  [`a494030`](https://github.com/acgetchell/research-repo-tools/commit/a494030723489565fc7f9c085806cc2d1b933f94)

  - Reject comparison outputs within either Criterion input root, including equivalent path aliases.
  - Reject Unicode surrogates in archive names before filesystem access.
  - Enforce explicit newline policies through just newline-check, included in just check and just ci.
  - Make generated scripts and fixtures portable while preserving intentional LF/CRLF data and malformed ZIP names on Windows.

## [0.1.2] - 2026-09-20

### Merged Pull Requests

- Bump astral-sh/setup-uv in the github-actions group [#18](https://github.com/acgetchell/research-repo-tools/pull/18)

### Added

- Add managed Cargo upgrades and notebook workflows
  [`2ad1023`](https://github.com/acgetchell/research-repo-tools/commit/2ad1023e994071452ce2a9f0e72ce9b03b74d2c4)

  - Add toolchain upgrade and update-cargo-tools, publishing Cargo pins only after installation succeeds and preserving prior pins on failure.
  - Include managed Cargo upgrades in the consumer update workflow.
  - Add optional notebook environment setup, validation, output cleanup, and fresh-kernel execution with source-preserving reports.
  - Check notebook syntax, Ruff rules and formatting, and ty types with diagnostics tied to stable cell IDs.
  - Honor configured notebook groups and reject numeric overflow before execution or file changes.
  - Fix draft release creation using annotated tag notes.
- Complete dependency and tool update workflows [`edf7e34`](https://github.com/acgetchell/research-repo-tools/commit/edf7e3436b9f499fb28a412d2fae9a9a85feb8bd)

  - Add aggregate, dependency-only, Cargo, Python, and tools-only recipes while preserving support for Python-only consumers.
  - Keep Cargo exclusions and additional resolution roots under consumer control.
  - Upgrade the full Python lock and synchronize dev with managed tools, retaining update-python-deps as an alias.
  - Bootstrap updates from the tooling group so native consumer builds wait until the final checked sync.
  - Support cargo-audit, cargo-machete, samply, tectonic, and tex-fmt with executable verification and native prerequisite guidance.
  - Document the superseded uv, Just, and cargo-update policies.

### Fixed

- Guard Cargo pin publication and Windows notebook sync
  [`f28204b`](https://github.com/acgetchell/research-repo-tools/commit/f28204b014782b83ff735ac9933d62af56a8dd64)

  - Recheck source content and symlink targets after staging Cargo pin updates, refusing publication when either has changed.
  - Start notebook-sync with managed Python to prevent Windows from replacing the environment while its CLI is running.
- Require a cargo-edit pin for cargo upgrade [`0dc0034`](https://github.com/acgetchell/research-repo-tools/commit/0dc00346c1885b8742da0a7d502099b4e27817d4)

  - Reject cargo upgrade through toolchain run when cargo-edit is undeclared, even if an unmanaged copy is available on PATH.
  - Direct consumers to declare an exact pin and run just setup to install and verify the tool before upgrading dependencies.
- Enforce cargo-edit pins for direct upgrades [`bd74456`](https://github.com/acgetchell/research-repo-tools/commit/bd744568008f7f072565718cc39fe700f188dd77)

  - Require a declared cargo-edit pin for direct cargo-upgrade calls and clarify exact version and setup requirements.
  - Exercise real Cargo requirement and lockfile updates from installed wheels in Linux, macOS, and Windows CI.
  - Prevent false Windows failures when comparing executable paths.

### Maintenance

- Bump astral-sh/setup-uv in the github-actions group [#18](https://github.com/acgetchell/research-repo-tools/pull/18)
  [`9867b46`](https://github.com/acgetchell/research-repo-tools/commit/9867b467bc97e6f8e098ebe8f14b1b9437595664)

  Bumps the github-actions group with 1 update: [astral-sh/setup-uv](https://github.com/astral-sh/setup-uv).

  Updates `astral-sh/setup-uv` from 10.0.1 to 10.1.0

  - [Release notes](https://github.com/astral-sh/setup-uv/releases)
  - [Commits](https://github.com/astral-sh/setup-uv/compare/20cfd1bf945f4377ade1205e4dbc17946fc9a30d...bec219d24cd3e171d82865faccec33120bb574f4)

## [0.1.1] - 2026-09-19

### Added

- Add CodeRabbit review and publish verified release assets to PyPI
  [`e9f9b7d`](https://github.com/acgetchell/research-repo-tools/commit/e9f9b7d04740f792da9bb218dccbfaa5edb98282)

  - Add shared branch and uncommitted review commands with thin Just  recipes, verified origin/main freshness, and streamed CodeRabbit output.
  - Require repository instructions and unambiguous review configuration; keep live reviews opt-in and outside routine validation.
  - Attach validated distributions and signed provenance to draft GitHub releases, then publish the verified assets to PyPI without rebuilding.
  - Preserve environment approval and verify release identity, asset inventory, and provenance before upload.
  - Add release-update and tag-preview recipes, and consolidate metadata, changelog, tagging, and publication guidance in docs/RELEASING.md.

### Fixed

- Preserve authored history and stabilize regeneration
  [`c22d1be`](https://github.com/acgetchell/research-repo-tools/commit/c22d1be4a95d4393c2a3f95932b7487ed4883165)

  - Retain declared release dates instead of replacing them with Git dates.
  - Preserve squash-entry structure and wording without promoting embedded headings or removing semantically similar content.
  - Compare archives through the same formatter to make regeneration idempotent while retaining genuine conflict detection.
  - Validate extracted notes against the requested release, rejecting duplicate targets, ambiguous boundaries, and conflicting references.
  - Add changelog check and just changelog-check for strict validation of the root changelog and all archives.

## [0.1.0] - 2026-09-17

### ⚠️ Breaking Changes

- Remove toolchain bootstrap and its generated shell and PowerShell launchers. uv must already be installed. Consumers must invoke research-repo-tools setup
  through their locked tooling group before using just recipes. Python callers must use typed configuration fields and ReleasePolicy instead of section
  dictionaries.

### Merged Pull Requests

- Bump hatchling in the python group across 1 directory [#8](https://github.com/acgetchell/research-repo-tools/pull/8)
- Bump the github-actions group across 1 directory with 3 updates [#6](https://github.com/acgetchell/research-repo-tools/pull/6)

### Added

- Add shared research repository tooling [`f9bd795`](https://github.com/acgetchell/research-repo-tools/commit/f9bd795c746ec2e82b3d3f709cd534b1141880aa)
- Bundle just and add repository security automation
  [`a91717e`](https://github.com/acgetchell/research-repo-tools/commit/a91717e30fd62661a044d4ddfadb47ae44811324)

  - Supply the pinned just executable and source attribution in every installation.
  - Add CodeQL, workflow checks, dependency auditing, and CodeRabbit-gated Dependabot auto-merge.
  - Preserve changelog content and consistently parse release versions, dates, and TOML metadata.
  - Reject incomplete Semgrep scans and resolve configured tools from the consumer root.
  - Document PyPI distribution and the shared-tooling adoption process.
- Add uv bootstrap and managed Rust/Python setup [`c74a1a9`](https://github.com/acgetchell/research-repo-tools/commit/c74a1a995a6a4f292fc451f1ae330d1d80a9c2e7)

  - Generate POSIX and PowerShell launchers that obtain uv and start the locked tooling environment before installing project dependencies.
  - Add read-only checks, explicit synchronization, and managed execution for declared Python, Rust, Cargo components, and development tools.
  - Isolate versioned tool installations and verify executable selection without replacing user-owned tools.
  - Add thin just recipes for setup, tool checks, and bootstrap generation.
  - Support included dependency groups while preserving their pinned tools during Python dependency updates.
  - Document installation through uv from PyPI and require publication before separate consumer adoption.
  - Update Ruff to 0.16.8.
- Unify changelog workflows and command discovery [`b1737d0`](https://github.com/acgetchell/research-repo-tools/commit/b1737d05607f0c5621807474fdcd2b6321bc5028)

  - Generate, normalize, and archive completed minor series together, validating candidates before replacement with recoverable rollback.
  - Share preview, release-note, archive, and local-tag recipes between maintainers and consumers.
  - Preserve Rust code in changelog entries, group dependency bump scopes, and recognize only complete v-prefixed SemVer tags.
  - Preserve consumer Python dependencies during managed execution and resolve minor Python pins within project constraints.
  - Roll back bootstrap launcher updates together and support unattended PowerShell downloads with TLS 1.2.
  - Suppress dry-run failure warnings when the toolchain is complete.
  - Add lexicographically sorted Just and CLI help, with bare just displaying available commands.
  - Separate consumer usage from contributor guidance and replace provenance and fix-history logs with current documentation.
- [**breaking**] Replace bootstrap launchers with explicit setup
  [`314227a`](https://github.com/acgetchell/research-repo-tools/commit/314227a6f09a97825f6ecfbc6e0ecf2a548bc6a2)

  - Install user-level Just, configure PATH, and synchronize declared tools before installing consumer project dependencies.
  - Include uv upgrades through its installation owner in just update, reconcile the project pin, and use that pin in CI.
  - Reject invalid dependency groups and incompatible Python selections before installation, and prioritize verified managed Cargo tools.
  - Keep dependency updates in the selected repository and preserve complete Python version constraints during resolution.
  - Preserve release headings and dates during changelog processing.
  - Respect Cargo workspace exclusions during release preparation.
  - Parse configuration and Semgrep findings into immutable records; reject ambiguous fixture paths, unrelated findings, and empty rule selections.
  - Add required native setup checks on Linux, macOS, and Windows for managed execution, Rust compilation, user Just, and repeat setup.
  - Document PyPI installation, explicit setup, and bare just commands.
- Display project status badges in README [`bf8814e`](https://github.com/acgetchell/research-repo-tools/commit/bf8814e6f3193890591de7f80fb50858cf4db8ba)

  Add badges for license, CI, CodeQL, Zizmor, Codecov, and dependency audit
  workflows to provide immediate visibility into project health.

### Changed

- Finalize 0.1.0 release notes with breaking changes
  [`1969816`](https://github.com/acgetchell/research-repo-tools/commit/1969816f5e6befa4f23f97eb1b3e87010d4c9906)

  This release formalizes version 0.1.0, including significant functional
  additions, fixes, and a major breaking change.

  The toolchain bootstrap and its generated launchers have been removed.
  Consumers must now ensure `uv` is pre-installed and explicitly invoke
  `research-repo-tools setup` via their locked tooling group. Python
  callers are required to use typed configuration fields and the
  `ReleasePolicy` instead of previous dictionary-based sections.

### Fixed

- Harden dependency updates and release preparation
  [`dd22de4`](https://github.com/acgetchell/research-repo-tools/commit/dd22de49348d5436ec272bf1d3bffc818593bcf3)

  - Retain manifest and lockfile recovery backups when rollback fails, and honor configured uv executables throughout Python pin updates.
  - Match normalized Python distribution names in uv.lock and derive runtime and CLI versions from installed package metadata.
  - Preserve Markdown tables during normalization and support changelog generation before the first release tag.
  - Render coverage paths consistently across platforms.
  - Add just update and changelog recipes using the shared tooling, and regenerate the repository changelog from commit history.
  - Extend type checking to scripts and tests, and refresh Ruff, ty, and locked dependencies.
- Verify uv ownership and stabilize native checks [`95a2686`](https://github.com/acgetchell/research-repo-tools/commit/95a26864c0cfa20589cebfbfc648496ad00be845)

  - Require a standalone receipt matching the active uv executable before self-update, with recovery guidance for other package managers.
  - Reuse successful uv and Python probes within each operation, invalidating them after installation attempts or environment changes.
  - Compare executable paths using Windows case rules and remove inherited shell markers from native POSIX setup checks.
  - Reject Semgrep findings that do not belong to the selected fixture.
  - Clarify that uv add --no-sync records the dependency and the later setup invocation installs the package.
- Enforce recipe prerequisites and dependency groups
  [`8909370`](https://github.com/acgetchell/research-repo-tools/commit/89093702275c77f2f430acf59f6b2d9e0be9d13b)

  - Check for a working POSIX shell before installing tools and explain the Git for Windows PATH requirement.
  - Select the dev dependency group explicitly in consumer recipes when default groups are disabled.
  - Shorten native CI setup paths to avoid Windows linker path limits.
  - Add just coverage for branch and Python subprocess coverage.
  - Upload Linux coverage through a reusable Codecov workflow using OIDC, with advisory statuses and retained reports.
  - Avoid duplicate package checks for branch pushes with open PRs.

### Maintenance

- Bump the github-actions group across 1 directory with 3 updates [#6](https://github.com/acgetchell/research-repo-tools/pull/6)
  [`91239fe`](https://github.com/acgetchell/research-repo-tools/commit/91239fe1aae3fbb451211392f0304a5abf14da76)

  Bumps the github-actions group with 3 updates in the / directory: [github/codeql-action/init](https://github.com/github/codeql-action),
  [github/codeql-action/analyze](https://github.com/github/codeql-action) and [github/codeql-action/upload-sarif](https://github.com/github/codeql-action).

  Updates `github/codeql-action/init` from 4.37.9 to 4.38.0

  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/cdf488f595d80d6e07e03d4674febd5ab45fa938...b96794f015dfd88f77b49b1c93e0fa7110f94c63)

  Updates `github/codeql-action/analyze` from 4.37.9 to 4.38.0
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/cdf488f595d80d6e07e03d4674febd5ab45fa938...b96794f015dfd88f77b49b1c93e0fa7110f94c63)

  Updates `github/codeql-action/upload-sarif` from 4.37.9 to 4.38.0
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/cdf488f595d80d6e07e03d4674febd5ab45fa938...b96794f015dfd88f77b49b1c93e0fa7110f94c63)
- Bump hatchling in the python group across 1 directory [#8](https://github.com/acgetchell/research-repo-tools/pull/8)
  [`d891eee`](https://github.com/acgetchell/research-repo-tools/commit/d891eeee9ed6e30be2f73f917fd70c945da4c360)

  Bumps the python group with 1 update in the / directory: [hatchling](https://github.com/pypa/hatch).

  Updates `hatchling` from 1.31.0 to 1.32.0

  - [Release notes](https://github.com/pypa/hatch/releases)
  - [Commits](https://github.com/pypa/hatch/compare/hatchling-v1.31.0...hatchling-v1.32.0)
- Prepare PyPI releases with Trusted Publishing [`2036a6c`](https://github.com/acgetchell/research-repo-tools/commit/2036a6c63983b29bc80053e0917bc0fc116485a6)

  - Add tagged publication using OIDC credentials and attestations.
  - Build distributions once and publish the artifacts validated across Linux, macOS, and Windows.
  - Gate publication on main-branch ancestry, synchronized release metadata, generated notes, and dependency auditing.
  - Reject incomplete distribution sets and preserve attribution bytes across Windows checkouts.
  - Document supported CLI and Python interfaces, consumer just recipes, and publisher setup.
  - Generate the initial 0.1.0 changelog from committed history.

[0.1.5]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/acgetchell/research-repo-tools/tree/v0.1.0
