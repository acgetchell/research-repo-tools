# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.8] - 2026-10-09

### ⚠️ Breaking Changes

- replace tag and tag-release with release-tag, and
  tag-preview with release-tag-preview. Remove tag-force and require TAG
  for release-check. The untagged release check CLI remains available.
- Semgrep scan owns its complete output directory and defaults
  to aggregate semgrep.json/semgrep.sarif reports. Use a dedicated output
  directory. Numbered reports are now emitted per batch.
- managed consumers must enforce the installed release's
  Python minimum in application metadata regardless of inheritance settings.
  The current Python 3.14 baseline remains unchanged.

### Merged Pull Requests

- Bump hatchling [#68](https://github.com/acgetchell/research-repo-tools/pull/68)
- Bump the github-actions group with 4 updates [#67](https://github.com/acgetchell/research-repo-tools/pull/67)

### Added

- Centralize validation and shared tool versions [`b8da6c1`](https://github.com/acgetchell/research-repo-tools/commit/b8da6c1773a6bcc9ded1150d6d15c4fdb3bf65ea)

  - Add check, fix, and typecheck commands for complete Python inventories
    while preserving native policies and read-only checks.
  - Offer opt-in Ruff, ty, and pytest versions through the installed
    release's python-tools extra.
  - Preview and apply tool adoption with drift checks, rollback, and
    preserved consumer runtime support and configuration.
  - Retain inherited versions during dependency updates and provide thin
    Just recipes with adoption and opt-out guidance.
- Add notebook fixtures and Just inspection APIs [`030f121`](https://github.com/acgetchell/research-repo-tools/commit/030f121d7179973033cf2adf4bd916b2e05600c7)

  - Run notebook integrations in isolated projects with the caller's locked
    interpreter, preserving manifests and avoiding native toolchain setup.
  - Expose pinned Just metadata and dry-run results for consumer recipe policy.
  - Reject junction aliases in evidence, measurement, publication, release,
    and worktree paths, and reject nonportable release filenames.
  - Refresh uv and notebook development dependencies and document the public
    integration contracts and portable path boundaries.
- Add complete runs and host profiling [`3cd0d52`](https://github.com/acgetchell/research-repo-tools/commit/3cd0d520e7c26a9c2f26f8327bc5ecb37b3c8d53)

  - Apply a captured common harness to both revisions with independent gates,
    complete Criterion samples, and explicit phase provenance.
  - Retain immutable runs with validated latest selection and named reference
    series for offline reports.
  - Capture typed host observations and configured native Rust/Cargo profiling
    declarations while preserving unknown values and original TOML.
  - Print validated OSV, Gitleaks, and Semgrep finding summaries without
    exposing sensitive scanner output.
- [**breaking**] Add reviewed publishing and shared release recipes
  [`9c9be75`](https://github.com/acgetchell/research-repo-tools/commit/9c9be752aa544ba44dcdbac3caec3ee97c4b2de3)

  - Gate stable GitHub Releases on reviewed source, protected-branch ancestry,
    successful exact-commit checks, and required assets.
  - Ship a consumer-owned crates.io OIDC workflow using a temporary token
    with native Cargo packaging and bounded publication verification.
  - Share release recipes and ordered release instructions across registries,
    with account-specific setup kept in a private maintainer task.
  - Preserve authored dependency bodies and links through the shared
    changelog policy while retaining common grouping and breaking notes.
  - Keep the declared changelog generator consistent across setup and CI.
  - Honor explicit task-specific authorization for Git operations while
    retaining a read-only default.
- Add Actions maintenance and portable tool updates
  [`63b94f7`](https://github.com/acgetchell/research-repo-tools/commit/63b94f74f5fef4315a17d57ba955dd38a7fc97d0)

  - Add opt-in Actions updates with immutable commit pins, version reports,
    and compatibility checks for retained tool wrapper actions.
  - Check steps and reusable workflows against consumer-owned GitHub
    selected-actions policies with precise source diagnostics.
  - Support verified uv-tool and Homebrew owners and managed dprint/rumdl
    binaries while preserving authoritative pins and installation constraints.
  - Keep tool updates separate from shell setup, protect staged file
    publication from concurrent edits, and run cheap checks before workflows.
- [**breaking**] Add SARIF policies and batched Semgrep reports
  [`0fb811e`](https://github.com/acgetchell/research-repo-tools/commit/0fb811e1a7604878a78f8d2dda49da591edc3796)

  Add strict driver and namespace selection for SARIF, preserving metadata
  and correcting indexed rule references with distinct upload categories.
  Publish complete SARIF and figure generations through a shared directory
  transaction with rollback and retained recovery data.

  Scan bounded Semgrep batches once for paired JSON/SARIF reports. Expose
  consumer suppression, jobs, target timeout, category and layout policies;
  verify exact coverage and active findings agreement before publication.
  Document the supported interfaces and downstream implementation deletion map.
- Add ID policies and opt-in launch/reset workflows
  [`f1fdb1b`](https://github.com/acgetchell/research-repo-tools/commit/f1fdb1baaa27f8acb9b59c399ecf1a628664a53b)

  Support configurable full-match cell-ID policies while preserving the
  default nbformat contract and existing source IDs.

  Launch locked JupyterLab with explicit browser behavior and private
  session caches. Preview tracked-notebook restoration from the index or
  a selected revision, applying only declared cleanup after opt-in and
  path validation.
- [**breaking**] Enforce the installed Python support minimum
  [`5ed34f1`](https://github.com/acgetchell/research-repo-tools/commit/5ed34f1a0cd72183caef697a21325fe0183dec73)

  Reconcile application Requires-Python alongside exact shared-package pins,
  extras, tooling constraints and development settings during adoption.
  Preserve stricter consumer ranges and reject incompatible constraints.

  Reject stale packaging metadata in ordinary Python, toolchain and setup
  gates without mutation or an opt-out. Bootstrap an exact target release
  under older consumer interpreters using its published support metadata.
- Add reproducible papers and raw line limits [`1ffd3c7`](https://github.com/acgetchell/research-repo-tools/commit/1ffd3c75a6dc67fe9ea3496e3fc1cb33fa0935d5)

  Expose optional paper date and PDF checks with deterministic Tectonic metadata normalization, consumer-declared policy, and validated artifact publication.
  Discover native build environments without provisioning host packages.

  Add configurable all-line UTF-8 validation over shared Git selection while preserving the existing Markdown table exemption.
- Run Cargo examples with discovery and live output
  [`bd0fd33`](https://github.com/acgetchell/research-repo-tools/commit/bd0fd332a06b24c20bac4b63192e2dd4483aee8b)

  Discover binary examples through Cargo metadata and execute native build
  artifacts with consumer-owned selection, features, deadlines, and assertions.
  Build ordinary examples once and run each feature override immediately after
  its build so shared binary paths cannot inherit a later feature policy.

  Add optional exact stdout assertions to live execution while preserving
  nonzero exits and deadlines, including when the output sink is blocked.
  Document the public contracts and migration from static validation plans.

### Changed

- Name oversized payload cases explicitly [`3ef6c34`](https://github.com/acgetchell/research-repo-tools/commit/3ef6c34d7bad189baaabe816ade9878bd7310841)

  - Use short case names so pytest can set its current-test environment
    variable on Windows, including for the one-megabyte response fixture.
- Cover installed group-constraint diagnostics [`6516808`](https://github.com/acgetchell/research-repo-tools/commit/65168086de417a467285ee675c292c98ebcc1606)

### Documentation

- Centralize Windows development guidance [`759f8a3`](https://github.com/acgetchell/research-repo-tools/commit/759f8a3749e6b0ddb29b413f718b54eead217df9)

  - Require agents to read the Windows guidance in CONTRIBUTING.md before
    changing paths, subprocesses, or package checks.
- Clarify capability test directory paths [`a639d2e`](https://github.com/acgetchell/research-repo-tools/commit/a639d2e29bb6908849a07197845dff11f01f21f9)

  State that the capability directory names in the contributor guide are
  relative to tests/.

### Fixed

- Verify tool reachability and isolate install checks
  [`4f01dcd`](https://github.com/acgetchell/research-repo-tools/commit/4f01dcdcebe5a68076e83788ad84eb59388d551d)

  - Reject inherited tool locks whose matching Ruff, ty, or pytest records
    are unreachable from the selected dev dependency graph.
  - Keep offline package-update fixtures self-contained by resolving against
    a wheel built from the tested artifact and using a fresh test cache.
- Reject linked samples and extra Just recipes [`06c8b6f`](https://github.com/acgetchell/research-repo-tools/commit/06c8b6fb05672e745e27fe6c3f607e5aac0f123f)

  - Reject symlink and junction Criterion roots before reading estimates.
  - Limit Just dry runs to one recipe invocation while preserving dependency
    previews.
  - Correct Windows package checks to preserve temporary cleanup and compare
    native Just invocation paths.
- Preserve UTF-8 output in isolated package checks
  [`a89da02`](https://github.com/acgetchell/research-repo-tools/commit/a89da02fd91acd79229d2db5ee71aa9812fa4e9c)

  - Select UTF-8 explicitly for isolated Python consumers while retaining
    startup isolation and the import checks' no-bytecode policy.
  - Document Windows encoding, native path, and fixture cleanup rules for
    contributors and agents.
- Keep kernel history in memory [`825fa17`](https://github.com/acgetchell/research-repo-tools/commit/825fa17bada66ece0b199be06de58fa3a01c7275)

  Apply in-memory IPython history explicitly for the project kernel so
  Windows can remove temporary execution files after the kernel exits.
- Isolate managed-release lookup credentials [`11e29c7`](https://github.com/acgetchell/research-repo-tools/commit/11e29c7b543c44d6c6b4474405b5443b2d00c2d0)

  - Add toolchain sync-binaries to install exact release pins without
    package synchronization or dependency builds.
  - Withhold GITHUB_TOKEN and GH_TOKEN from setup commands and version
    probes while preserving credentials for explicit toolchain run commands.
  - Redact lookup credentials in diagnostics and scope GitHub Actions
    authentication to installed-package release operations.
- Preserve SARIF references and deduplicate tests [`77abfdc`](https://github.com/acgetchell/research-repo-tools/commit/77abfdcbda786b3255bcf47b367542fd428f2343)

  Keep GitHub upload categories distinct and reindex SARIF rule metadata,
  artifact locations, and invocation provenance when reports are combined.
  Reject output aliases that could replace selected source trees, and
  preserve read-only generated file permissions during publication.

  Consolidate 23 redundant test definitions across the repository while
  retaining distinct failure cases and installed-package regressions.
  Clarify Semgrep migration guidance and fix documentation links.
- Diagnose malformed Python group constraints [`0357e32`](https://github.com/acgetchell/research-repo-tools/commit/0357e32601e8c909e50ed8ba375de91b0cd9083a)

  Reject malformed uv dependency-group tables and Python range values with
  field-specific diagnostics instead of exposing attribute errors from
  ordinary shared Python checks.
- Identify invalid TeX sources during normalization
  [`3b621e6`](https://github.com/acgetchell/research-repo-tools/commit/3b621e6a2c8245b05846c411a8b907df9ac2bbc6)

  Include the TeX path when decoding or parsing its declared date fails. Preserve the source and PDF while reporting a consistent diagnostic through Python and
  the CLI.
- Preserve deadlines and portable Cargo output [`556106e`](https://github.com/acgetchell/research-repo-tools/commit/556106ed52a45dfa82e287687c42d92a91ba6bd2)

  - Frame Cargo JSON on LF without splitting Unicode characters in native paths.
  - Avoid flushing caller buffers outside the deadline and share delete-on-close
    spool handles so descendant stdout cannot mask direct-child results on Windows.
  - Strengthen installed process and feature-only selection regressions.
  - Route the documented recipe through managed tools and clarify navigation and
    authorized Git-fixture validation guidance.
- Harden shared tooling input and notebook policy handling
  [`637d1ae`](https://github.com/acgetchell/research-repo-tools/commit/637d1ae92d89c997c44295d15003b44883ec597b)

  - Preserve physical notebook source lines and distinguish literal commands
    from shell programs across POSIX and Windows invocation forms.
  - Reject malformed Python baseline tables, duplicate Semgrep result keys,
    scalar argument vectors, and invalid release event objects.
  - Protect SARIF inputs from output-directory replacement, surface release
    documentation traversal errors, and preserve UTF-8 shell output paths.
  - Validate packaged workflow templates and clarify consumer migration,
    Python baseline, and release metadata guidance.

### Maintenance

- Bump the github-actions group with 4 updates [#67](https://github.com/acgetchell/research-repo-tools/pull/67)
  [`58649a8`](https://github.com/acgetchell/research-repo-tools/commit/58649a81e313548790344f00a16c62b74daf9aae)

  Bumps the github-actions group with 4 updates: [astral-sh/setup-uv](https://github.com/astral-sh/setup-uv),
  [github/codeql-action/init](https://github.com/github/codeql-action), [github/codeql-action/analyze](https://github.com/github/codeql-action) and
  [github/codeql-action/upload-sarif](https://github.com/github/codeql-action).

  Updates `astral-sh/setup-uv` from 10.1.0 to 10.2.0

  - [Release notes](https://github.com/astral-sh/setup-uv/releases)
  - [Commits](https://github.com/astral-sh/setup-uv/compare/bec219d24cd3e171d82865faccec33120bb574f4...c18668ad3cf93ea998bef934396af7bb5c839dc7)

  Updates `github/codeql-action/init` from 4.38.1 to 4.38.2
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/1c5b675653bb5c22dbe9b12b556ec555138e09fd...2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2)

  Updates `github/codeql-action/analyze` from 4.38.1 to 4.38.2
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/1c5b675653bb5c22dbe9b12b556ec555138e09fd...2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2)

  Updates `github/codeql-action/upload-sarif` from 4.38.1 to 4.38.2
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/1c5b675653bb5c22dbe9b12b556ec555138e09fd...2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2)
- Bump hatchling [#68](https://github.com/acgetchell/research-repo-tools/pull/68)
  [`b972cbf`](https://github.com/acgetchell/research-repo-tools/commit/b972cbfd717319abaa68d9b1373711ad6f163294)

  Bumps the python group with 1 update: [hatchling](https://github.com/pypa/hatch).

  Updates `hatchling` from 1.32.3 to 1.32.4

  - [Release notes](https://github.com/pypa/hatch/releases)
  - [Commits](https://github.com/pypa/hatch/compare/hatchling-v1.32.3...hatchling-v1.32.4)

## [0.1.7] - 2026-09-26

### Merged Pull Requests

- Bump hatchling in the python group [#53](https://github.com/acgetchell/research-repo-tools/pull/53)
- Bump the github-actions group with 4 updates [#52](https://github.com/acgetchell/research-repo-tools/pull/52)

### Added

- Add reusable patch approval and auto-merge [`2ef9ba8`](https://github.com/acgetchell/research-repo-tools/commit/2ef9ba8acdf35f7302b6c9515504814a9c40c874)

  - Approve allowlisted uv and Cargo patch updates using repository-owned dependency and file policies, including every member of grouped updates.
  - Bind approval to a single GitHub-signed Dependabot head commit and require active rulesets with stale-review dismissal and strict checks.
  - Replace CodeRabbit approval polling and the personal token requirement with GITHUB_TOKEN and native squash auto-merge.
  - Provide settings payloads and document consumer SHA pinning, setup, rollout, and post-merge verification.
- Add shared Python adoption, security scans, and cleanup
  [`d975bb2`](https://github.com/acgetchell/research-repo-tools/commit/d975bb293262457f44ab9e0d6484af3622911a00)

  - Add opt-in Python baseline inheritance and recoverable preview/apply migration while preserving consumer runtime and lint policies.
  - Manage cargo-deny and checksum-verified OSV/Gitleaks binaries, with explicit scan inputs, redacted reports, and blocking failure handling.
  - Share Semgrep inventory, Rust documentation scans, and fixture checks.
  - Support first-release preparation without a fabricated predecessor and opt-in blocking of dependency installation in notebooks.
  - Add just clean for obsolete package-owned installations, with previews and retention roots; keep user-wide installations untouched.
  - Preserve executable helpers and reject linked adoption environments; handle relative cleanup roots and TOML tables without final newlines.
  - Correct Windows Bash CRLF assumptions in the Dependabot test harness.
  - Document public configuration factories and consumer migration, cleanup, and release contracts; repair README links for PyPI.
  - Update platformdirs to 4.11.15.

### Fixed

- Make Dependabot workflow tests portable [`3f1bfc9`](https://github.com/acgetchell/research-repo-tools/commit/3f1bfc92244c75bffbe6269532f37ac64ed30f60)

  - Use jq's portable -b option for Ubuntu compatibility while preserving  LF output on Windows.
  - Pass Bash scripts through binary stdin to preserve embedded quoting and prevent Windows newline translation.
  - Add a dynamic PyPI version badge to the README linking to the package.
- Isolate Dependabot test scripts from child stdin
  [`937f151`](https://github.com/acgetchell/research-repo-tools/commit/937f15197a9fe0bcc5d04f1d19d9a42cb1a4f9ec)

  - Run Bash from temporary files with LF line endings so child processes cannot consume the workflow script through stdin.
  - Normalize jq output before simulating Windows CRLF behavior.
  - Document that GITHUB_TOKEN approval replaces CodeRabbit polling and personal tokens while required CodeRabbit status checks still apply.
- Preserve exact CRLF bytes in Dependabot test fixtures
  [`a5aa0f5`](https://github.com/acgetchell/research-repo-tools/commit/a5aa0f5e75e4e1f89f795cb85aab4d9cab5c1b27)

  - Replace sed-based newline simulation with Bash builtins to avoid platform-dependent text conversion.
  - Assert exact LF and CRLF bytes before and after command substitution, including when external text filters normalize line endings.
- Authenticate scanner setup and detect notebook installs
  [`d0ae1bd`](https://github.com/acgetchell/research-repo-tools/commit/d0ae1bd92108696c642f0758e63463389f4348f1)

  - Authenticate GitHub release metadata with GITHUB_TOKEN or GH_TOKEN and pass the workflow token to native setup to avoid anonymous API rate limits.
  - Detect nested sudo/env wrappers and Windows executable paths and casing in notebook installation commands.
  - Inspect literal subprocess calls after notebook magics while preserving original source line numbers.
  - Remove stale numbered OSV and Semgrep JSON/SARIF reports, including symlinks, while preserving unrelated files and symlink targets.
- Reject linked scanner report directories [`0f98931`](https://github.com/acgetchell/research-repo-tools/commit/0f98931586b15cb3767e53d09021bfa8baca8667)

  - Reject symlinks and Windows junctions in OSV and Semgrep output paths, including parent components, before creating directories  or removing reports.
  - Prevent cleanup from deleting reports through directory links while preserving numbered-report cleanup and report-symlink removal.
- Honor configured updates and verified base merges
  [`cbb2ea6`](https://github.com/acgetchell/research-repo-tools/commit/cbb2ea6dee8866b3f0547bca935aef48fdd71707)

  - Approve configured uv, Cargo, and GitHub Actions updates, including minor and major versions, without duplicate dependency-name filters.
  - Accept verified GitHub base merges only when ancestry and original dependency-file contents are preserved.
  - Document file allowlists, shared workflow adoption, and retirement of CodeRabbit approval requests and personal tokens.

### Maintenance

- Bump hatchling in the python group [#53](https://github.com/acgetchell/research-repo-tools/pull/53)
  [`ac5efcc`](https://github.com/acgetchell/research-repo-tools/commit/ac5efcc665828b7fcb7fcdab66f00672e720d3d7)

  Bumps the python group with 1 update: [hatchling](https://github.com/pypa/hatch).

  Updates `hatchling` from 1.32.0 to 1.32.3

  - [Release notes](https://github.com/pypa/hatch/releases)
  - [Commits](https://github.com/pypa/hatch/compare/hatchling-v1.32.0...hatchling-v1.32.3)
- Bump the github-actions group with 4 updates [#52](https://github.com/acgetchell/research-repo-tools/pull/52)
  [`d2469f6`](https://github.com/acgetchell/research-repo-tools/commit/d2469f6ffa513d4674a3311473c266167d0ae728)

  Bumps the github-actions group with 4 updates: [codecov/codecov-action](https://github.com/codecov/codecov-action),
  [github/codeql-action/init](https://github.com/github/codeql-action), [github/codeql-action/analyze](https://github.com/github/codeql-action) and
  [github/codeql-action/upload-sarif](https://github.com/github/codeql-action).

  Updates `codecov/codecov-action` from 7.1.0 to 7.1.1

  - [Release notes](https://github.com/codecov/codecov-action/releases)
  - [Changelog](https://github.com/codecov/codecov-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/codecov/codecov-action/compare/0b35c9ecc4f0529d0eb674914510c22f85b196b4...303a32d7a59b442fa8d48b6a1cc6825c09c847a5)

  Updates `github/codeql-action/init` from 4.38.0 to 4.38.1
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/b96794f015dfd88f77b49b1c93e0fa7110f94c63...1c5b675653bb5c22dbe9b12b556ec555138e09fd)

  Updates `github/codeql-action/analyze` from 4.38.0 to 4.38.1
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/b96794f015dfd88f77b49b1c93e0fa7110f94c63...1c5b675653bb5c22dbe9b12b556ec555138e09fd)

  Updates `github/codeql-action/upload-sarif` from 4.38.0 to 4.38.1
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/b96794f015dfd88f77b49b1c93e0fa7110f94c63...1c5b675653bb5c22dbe9b12b556ec555138e09fd)

## [0.1.6] - 2026-09-23

### Added

- Share zizmor audits and complete Python checks [`638a797`](https://github.com/acgetchell/research-repo-tools/commit/638a797393d4d937174f1b105fe89fdc693e6a93)

  - Add zizmor check with a verified scanner pin, explicit persona, token discovery and redaction, and required-online or offline modes.
  - Fail CI on workflow findings while preserving SARIF generation and restricting privileged uploads for fork and Dependabot runs.
  - Apply full configured Ruff and ty checks to tracked and nonignored Python files, including fixtures, through shared file selection.
  - Ship an opt-in annotation policy and adoption guidance covering Python 3.14, precise fixture exceptions, and canonical validation gates.

### Fixed

- Allow SARIF uploads from private repositories [`56e0e31`](https://github.com/acgetchell/research-repo-tools/commit/56e0e313bfd4c45ff98d51cab1938015e28fd1b4)

  - Grant actions: read in the packaged workflow for private-repository SARIF uploads, preserving existing permissions.
  - Update the locked wcwidth dependency from 0.8.4 to 0.9.0.

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

[0.1.8]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/acgetchell/research-repo-tools/tree/v0.1.0
