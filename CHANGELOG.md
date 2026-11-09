# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.7] - 2026-11-09

### ⚠️ Breaking Changes

- ReleaseRule now takes FixedValue or ReleaseSource;
  TrialSchedule takes an order variant; Estimate takes an optional complete
  ConfidenceInterval. Python callers must migrate for v0.2.7. Existing valid
  TOML, JSON, CSV, and retained evidence formats keep their contracts.

#### docs(release): clarify the draft writer isolation boundary

  Limit the Just recipe example to a trusted local project environment and
  direct CI writers with repository-write credentials to the isolated,
  exactly pinned no-checkout workflow.

### Merged Pull Requests

- Preserve publication snapshots and Semgrep fixture lines [#210](https://github.com/acgetchell/research-repo-tools/pull/210)
- Retrieve draft receipts, clean coverage, and refresh docs [#209](https://github.com/acgetchell/research-repo-tools/pull/209)
- Prepare retained drafts, enforce typed states, and parallelize validation [#207](https://github.com/acgetchell/research-repo-tools/pull/207)
- Resume scheduled suites and separate phase inputs [#205](https://github.com/acgetchell/research-repo-tools/pull/205)
- Bump astral-sh/setup-uv [#202](https://github.com/acgetchell/research-repo-tools/pull/202)
- Preserve exact Git development pins during updates [#201](https://github.com/acgetchell/research-repo-tools/pull/201)
- Honor suite provenance and release floors [#199](https://github.com/acgetchell/research-repo-tools/pull/199)
- Verify Python and Cargo dependency sources [#198](https://github.com/acgetchell/research-repo-tools/pull/198)
- Verify development pins and published tooling [#195](https://github.com/acgetchell/research-repo-tools/pull/195)
- Report rejected gates as failed [#192](https://github.com/acgetchell/research-repo-tools/pull/192)
- Support committed benchmark evidence and release summaries [#190](https://github.com/acgetchell/research-repo-tools/pull/190)

### Added

- Support committed benchmark evidence and release summaries [#190](https://github.com/acgetchell/research-repo-tools/pull/190)
  [`1308688`](https://github.com/acgetchell/research-repo-tools/commit/130868893b17497b610395054fc9222771648864)

#### feat: support committed benchmark evidence and release summaries
  - Accept exact publisher-generated benchmark evidence with independently
    captured acquisition identity, pinned receipts, and preserved run history.
  - Present readable release-evidence summaries while preserving JSON handoffs,
    signatures, integrity checks, freshness checks, and approval revalidation.
  - Retain raw acquisitions across failed report promotion and allow inspection
    and retries without repeating measurement.
  - Reject malformed process, selection, Cargo documentation, and release-policy
    inputs before effects, and retain complete coverage evidence on failure.
  - Close failed HTTP responses and preserve portable output bytes and diagnostics.
#### fix: clarify candidate receipts and narrow benchmark evidence scope
  - Document the paired publication receipt arguments and independently trusted
    digest required when candidate policy declares a report.
  - Remove unrelated Cargo documentation and security inventory changes from
    the benchmark evidence follow-up, retaining their existing base behavior.

- Verify development pins and published tooling [#195](https://github.com/acgetchell/research-repo-tools/pull/195)
  [`a4640b0`](https://github.com/acgetchell/research-repo-tools/commit/a4640b0efd8f2a7c2632f834998fc4b46e5c0000)

#### feat(release): verify development pins and published tooling
  - Accept exact Git tooling pins for development while requiring published
    dependencies at release acquisition, evidence, and publication boundaries.
  - Verify declarations, locks, installed payloads, and isolated runtime sources
    without executing consumer environment startup or build hooks.
  - Share typed release and GitHub commit selection through the standalone
    bootstrap, Just recipe, and GitHub Action, rejecting unavailable sources.
#### fix(release): harden source lookups and dependency gates
  - Preserve recovery diagnostics for truncated registry and tooling responses.
  - Authenticate GitHub API lookups without forwarding credentials to raw
    content, package indexes, or redirect destinations.
  - Carry standalone dependency policy through performance and candidate APIs,
    including candidate revalidation before returning acceptance.
#### fix(tooling): keep lookup credentials out of child processes
  - Give the shared Action a lookup-only GitHub credential and remove it from
    normal and legacy tooling launches.
  - Preserve explicitly supplied authentication for tooling commands that need it.
  - Reject malformed lookup credentials before HTTP transport without disclosing
    their values in diagnostics.

- Verify Python and Cargo dependency sources [#198](https://github.com/acgetchell/research-repo-tools/pull/198)
  [`9d0337f`](https://github.com/acgetchell/research-repo-tools/commit/9d0337fbfbd1130d7387f89664cb77ae8c95e4a5)

#### feat(deps): verify Python and Cargo dependency sources

  Add a shared source policy and typed selectors for exact PyPI/crates.io
  releases and GitHub development commits. Provide preview/apply transitions
  that resolve native locks before publishing manifests and locks together.

  Verify configured release dependency graphs and consumed package bytes
  against eligible registry artifacts, with explicit workspace ownership
  and unchanged-input checks at release boundaries.
#### fix(deps): harden source transitions and release verification

  Require an explicit source policy for dependency-bearing releases and retain
  both legacy and graph verification without duplicate identity results. Pace
  crates.io metadata requests and recognize legacy uv hooks by exact content.

  Preserve managed launcher settings, resolve Cargo source changes using the
  current lock's origin, and find inherited versions in verified workspace
  manifests. Bound checkout snapshots before copying and describe publication
  as recoverable across files.
#### fix(deps): accept uv tool launcher directories

  Allow uv tool installation directories during source verification so managed
  Windows launchers can run the release gate without permitting source overrides.

- [**breaking**] Prepare retained drafts, enforce typed states, and parallelize validation [#207](https://github.com/acgetchell/research-repo-tools/pull/207)
  [`5ad89e0`](https://github.com/acgetchell/research-repo-tools/commit/5ad89e0f8ea861deb140109c25a30a254b27189d)

#### feat(release)!: prepare retained drafts with typed domain states
  - Prepare unpublished stable drafts from retained signed evidence and exact
    generated notes through a package-neutral API and CLI.
  - Bind retries to durable intent and captured release IDs, and authenticate
    the isolated writer handoff without executing checkout code.
  - Model release rules, trial ordering, confidence intervals, fixture matching,
    authentication, and recovery with explicit alternatives.
  - Preserve notebook, scanner, measurement, and adoption-plan invariants across
    direct construction, nested aliases, and replacement.

- Retrieve draft receipts, clean coverage, and refresh docs [#209](https://github.com/acgetchell/research-repo-tools/pull/209)
  [`cd8594f`](https://github.com/acgetchell/research-repo-tools/commit/cd8594f658b5bf3ade063a685834565cc62e0cb3)

#### feat(release): retrieve original draft receipts and clean coverage
  - Authenticate the original tag-push receipt by source, workflow, successful
    gates, immutable artifact identity, digest, and exact retained files.
  - Freeze the original run and artifact for retries, with explicit manual
    recovery and an outside-checkout CLI handoff.
  - Export safe coverage cleanup and compose it into just clean while preserving
    current reports, unrelated files, and stale-plan safeguards.
  - Refresh consumer, API, release, and development guidance and retire the
    obsolete MCMC extraction document.
#### fix(clean): report partial cleanup and clarify release guidance
  - Preserve early validation errors and report completed removals when a late
    link or junction interrupts coverage cleanup.
  - Use an explicit date in the changelog preparation example and explain which
    directory supplies the release asset APIs' dependency policy.

### Fixed

- Report rejected gates as failed [#192](https://github.com/acgetchell/research-repo-tools/pull/192)
  [`ef783ca`](https://github.com/acgetchell/research-repo-tools/commit/ef783cac608bc0d2d4657052c20e663f2d45b232)

  Report gate completion only after the native command, input freshness, and forbidden timing-output checks pass. Preserve failure and interruption outcomes
  without accepting samples or publishing evidence.
- Honor suite provenance and release floors [#199](https://github.com/acgetchell/research-repo-tools/pull/199)
  [`a8dc578`](https://github.com/acgetchell/research-repo-tools/commit/a8dc578a7583f1e4d811a5bad1ceb47ae28949e4)

  Resolve dependency version labels from the selected suite and originating phase observation, including scheduled trials. Enforce each available suite minimum
  release during pair selection and preparation while respecting availability exclusions.
- Preserve exact Git development pins during updates [#201](https://github.com/acgetchell/research-repo-tools/pull/201)
  [`aadac36`](https://github.com/acgetchell/research-repo-tools/commit/aadac36cd8083a43ecbabec701c3414de78206fd)

  Retain immutable Git source identity, extras, included-group constraints,
  and inherited tool versions while advancing eligible registry dependencies.
  Reject unsupported sources even when no direct upgrade targets remain.

  Keep isolated Semgrep child processes in their installed environment and
  refresh the locked Semgrep and serialization dependencies.
- Resume scheduled suites and separate phase inputs [#205](https://github.com/acgetchell/research-repo-tools/pull/205)
  [`055353c`](https://github.com/acgetchell/research-repo-tools/commit/055353c630604b1ae3438c1eaf534ba85e067a7e)

  Retain completed paired suites within interrupted trial schedules, preserving
  raw observations, collection segments, and abandoned attempts. Revalidate
  retained preparation assertions before retrying an incomplete pair in full.

  Allow phase-specific source declarations so immutable baseline compiler inputs
  can coexist with current documentation outputs while protecting current source,
  harness, workload, and exact publisher inputs.

  Refresh the locked sse-starlette dependency to 3.7.0.
- Preserve publication snapshots and Semgrep fixture lines [#210](https://github.com/acgetchell/research-repo-tools/pull/210)
  [`22f4841`](https://github.com/acgetchell/research-repo-tools/commit/22f4841433050128c0ccf2b61077e29b9e76c60b)

  - Reject input or output edits detected after publication staging.
  - Match Semgrep fixture annotations using native LF line boundaries.
  - Refresh the locked bracex dependency to 3.2.2.
- Verify source-built Python dependencies [`a859edd`](https://github.com/acgetchell/research-repo-tools/commit/a859edd85c189ed3a4df3aca5c23ee0b1ffd9563)

  - Rebuild explicitly selected PyPI sources with hash-locked build tools
  - Authenticate retained native wheels and build evidence against current inputs
  - Verify pinned external executables, wheel relocation, and shared namespaces
  - Declare the release dependency graph and require native provenance in CI
  - Document source-build preparation and recovery before tagging v0.2.7

### Maintenance

- Bump astral-sh/setup-uv [#202](https://github.com/acgetchell/research-repo-tools/pull/202)
  [`13af6b6`](https://github.com/acgetchell/research-repo-tools/commit/13af6b60c9bc1712b329bfe9504c7a54840e043c)

  Bumps the github-actions group with 1 update: [astral-sh/setup-uv](https://github.com/astral-sh/setup-uv).

  Updates `astral-sh/setup-uv` from 10.3.2 to 10.3.3

  - [Release notes](https://github.com/astral-sh/setup-uv/releases)
  - [Commits](https://github.com/astral-sh/setup-uv/compare/07a047e608db84b3d8c06cd9a7c7dcc9c355c18e...68559e99cc807c1eb7e9238fd43a15bba460eec2)

## [0.2.6] - 2026-11-05

### ⚠️ Breaking Changes

- Read the selected scanner version from
  semgrep_tool.verify(root).version in place of semgrep_tool.VERSION.

### Merged Pull Requests

- Add consumer Python release evidence and harden shared tooling [#187](https://github.com/acgetchell/research-repo-tools/pull/187)
- Add scheduled benchmark reuse and harden shared tooling [#186](https://github.com/acgetchell/research-repo-tools/pull/186)
- Validate packaged Cargo docs and verify locked Semgrep [#184](https://github.com/acgetchell/research-repo-tools/pull/184)
- Preserve repository root API endpoints [#181](https://github.com/acgetchell/research-repo-tools/pull/181)

### Added

- [**breaking**] Validate packaged Cargo docs and verify locked Semgrep [#184](https://github.com/acgetchell/research-repo-tools/pull/184)
  [`0ac0791`](https://github.com/acgetchell/research-repo-tools/commit/0ac07912ca4c5e5766dd15bd5fa0d72383b6fb9f)

#### feat!: validate packaged Cargo docs and verify locked Semgrep
  - Build public docs from the packaged Cargo crate with the declared stable
    compiler, selected features, and warnings denied.
  - Let the consumer lockfile select a supported Semgrep version and verify
    it against the installed scanner and Python interpreter.
  - Run tests and subprocess coverage with four workers by default.
  - Default performance progress heartbeats to five minutes.
  - Refresh locked development tools and give offline update fixtures a
    shared temporary interpreter store.

- Add scheduled benchmark reuse and harden shared tooling [#186](https://github.com/acgetchell/research-repo-tools/pull/186)
  [`39b56e6`](https://github.com/acgetchell/research-repo-tools/commit/39b56e60442122bdc13105f2f257d1e8574c4abb)

#### feat(performance): reuse candidate benchmarks with scheduled trials

  Bind pre-tag benchmark acquisitions to reviewed release commits and freeze
  the accepted native evidence and archive bytes for publication retries.

  - Declare alternating or seeded randomized trials and retain actual phase
    order, collection intervals, raw samples, and original provenance.
  - Verify independent acquisition assertions and whole-tree source bindings
    while allowing only declared report and documentation output changes.
  - Keep report rendering, archive derivation, and approval revalidation free
    of benchmark acquisition, with strict checkpoint and trial validation.
  - Refresh the typing-inspection lock to 0.4.5.
- Add consumer Python release evidence and harden shared tooling [#187](https://github.com/acgetchell/research-repo-tools/pull/187)
  [`c04f61b`](https://github.com/acgetchell/research-repo-tools/commit/c04f61b0cb89b08a1a72521e7ad1bdca5f8ac549)

#### feat: add consumer Python release evidence and harden shared tooling
  - Select explicit Python identities in mixed workspaces while preserving
    Cargo-first automatic selection.
  - Bind consumer wheel/sdist inventories and exact bytes to trusted workflow
    evidence, approval handoffs, freshness, and configurable PyPI destinations.
  - Reject nonportable archive aliases and hidden dirty checkouts, and preserve
    immutable parsed evidence and policy assertions.
  - Retain acquisition, notebook, and scanner failures through interruption
    and cleanup, and publish workflow timing output atomically.
  - Preserve plot spacing and distinct labels for nearby or extreme values.
  - Refresh locked development dependencies.

### Fixed

- Preserve repository root API endpoints [#181](https://github.com/acgetchell/research-repo-tools/pull/181)
  [`f6b8665`](https://github.com/acgetchell/research-repo-tools/commit/f6b8665610095a57a11039d454192493e4c9ebfb)

  Avoid a trailing slash when release validation requests repository metadata. Cover empty and nested endpoints for default and caller-selected repositories in
  the source and installed consumer suite.

## [0.2.5] - 2026-11-02

### Merged Pull Requests

- Add verified reuse and fix shared generators [#176](https://github.com/acgetchell/research-repo-tools/pull/176)
- Add suite availability, progress, and resume [#175](https://github.com/acgetchell/research-repo-tools/pull/175)
- Share Python and scope documentation rules [#174](https://github.com/acgetchell/research-repo-tools/pull/174)
- Bump the github-actions group with 4 updates [#173](https://github.com/acgetchell/research-repo-tools/pull/173)

### Added

- Add index-preserving status and diff inspection [`17edec0`](https://github.com/acgetchell/research-repo-tools/commit/17edec0cc70fa80124d46b7054ed3ecd82ae5283)

  Expose fixed byte-oriented queries with literal paths and bounded deadlines
  while retaining the general Git runner's defaults. Derive changed paths from
  content-comparing numstat output to exclude stat-only false positives.

  Document configuration overrides, index-preservation limits, and migration
  for the planned v0.2.5 release. Update the PyPI publishing action to the
  patched v1.14.3 pin required by the workflow audit.
- Add suite availability, progress, and resume [#175](https://github.com/acgetchell/research-repo-tools/pull/175)
  [`39914eb`](https://github.com/acgetchell/research-repo-tools/commit/39914eb1ab4865a441ca840c8192b554111394cf)

#### feat(performance): add suite availability, progress, and resume
  - Declare workload availability from original revision receipts before compiling
    or discovering benchmarks, with explicit one-sided results and justified
    per-workload comparisons.
  - Report phase and suite budgets, accepted completion counts, and compatible
    remaining-time estimates through human or JSON progress events.
  - Resume atomically retained suite units after validating source, environment,
    host, toolchain, and raw evidence; recollect interrupted pairs in full.
  - Preserve offline report and README retries without repeating acquisition.
- Add verified reuse and fix shared generators [#176](https://github.com/acgetchell/research-repo-tools/pull/176)
  [`4a53d40`](https://github.com/acgetchell/research-repo-tools/commit/4a53d40f57f1895dff247bc5e285b1078c6f5140)

#### feat(performance): add verified benchmark reuse and bounded reports

  Derive deterministic release benchmark archives from independently asserted
  complete-run v4 acquisitions while preserving original bytes and provenance.
  Require exact tagged source identity and eligible comparisons, and retain the
  existing publication revalidation, artifact authentication, and approval
  boundaries.

  Wrap generated links, wide tables, and recorded provenance, and normalize
  generated list spacing. Introduce report renderer 3 while preserving immutable
  renderer-1 and renderer-2 histories and their original evidence.

### Changed

- Remove Semgrep isolation changes from release maintenance
  [`76d67c7`](https://github.com/acgetchell/research-repo-tools/commit/76d67c777107421ce31885fc1368ecaa4560c884)
- Verify checkout evidence with real Git [`56dfa82`](https://github.com/acgetchell/research-repo-tools/commit/56dfa8258230164688cd4b5ffcadcf9143aa83dc)

  Use disposable committed repositories for release-evidence checkout tests, including dirty trees, wrong roots, missing HEAD, and commit changes during
  verification. Keep remote evidence transport mocked and honor the Git-mutation test control.

  Record release evidence in PRs or review notes as required by repository guidance.

### Fixed

- Improve release evidence and Semgrep isolation [`57a4799`](https://github.com/acgetchell/research-repo-tools/commit/57a47993f9d0ef2ace1df86778108ad6f57912ca)

  Add no-argument inspection of signed main CI evidence while preserving
  explicit shared evidence commands and automatic release preparation.

  Clarify CI waits, exact workflow selection, and preparation timeout recovery.
  Add the Zenodo concept DOI to the README badge and citation metadata.

  Keep Semgrep's pysemgrep child in its verified tool environment and cover
  ambient executable conflicts in the public consumer regressions.
- Share Python and scope documentation rules [#174](https://github.com/acgetchell/research-repo-tools/pull/174)
  [`871cdff`](https://github.com/acgetchell/research-repo-tools/commit/871cdff1331c7cb190fe78e96afc377a388d1304)

  Use the pinned Semgrep extra in the shared Python 3.15 environment and apply one consistent representation, extraction, and report-currentness policy. Publish
  process completion endpoints without Windows read-sharing races. Export shared dependency constraints and security commands for consumer adoption.

### Maintenance

- Refresh locked Python dependencies [`ea261a8`](https://github.com/acgetchell/research-repo-tools/commit/ea261a8efe6d55a6dbb063ef7d489b34ba887d28)

  Update filelock to 4.2.5, IPython to 9.18.0, platformdirs to 4.14.0, and
  pypdf to 6.21.4 from the maintainer's dependency refresh.
- Bump the github-actions group with 4 updates [#173](https://github.com/acgetchell/research-repo-tools/pull/173)
  [`30e1342`](https://github.com/acgetchell/research-repo-tools/commit/30e1342490530e5178fd378b556022e75e7b419a)

  Bumps the github-actions group with 4 updates: [astral-sh/setup-uv](https://github.com/astral-sh/setup-uv),
  [github/codeql-action/init](https://github.com/github/codeql-action), [github/codeql-action/analyze](https://github.com/github/codeql-action) and
  [github/codeql-action/upload-sarif](https://github.com/github/codeql-action).

  Updates `astral-sh/setup-uv` from 10.3.0 to 10.3.2

  - [Release notes](https://github.com/astral-sh/setup-uv/releases)
  - [Commits](https://github.com/astral-sh/setup-uv/compare/1c37ad07a6a961277cf70c0d37d6f313000f5884...07a047e608db84b3d8c06cd9a7c7dcc9c355c18e)

  Updates `github/codeql-action/init` from 4.39.0 to 4.39.1
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/6f2ac81f5cd81fe351a197ba0b00d91d03cd2aee...9b3254085494b55a0f7c4c2b3d2d52cc712ce370)

  Updates `github/codeql-action/analyze` from 4.39.0 to 4.39.1
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/6f2ac81f5cd81fe351a197ba0b00d91d03cd2aee...9b3254085494b55a0f7c4c2b3d2d52cc712ce370)

  Updates `github/codeql-action/upload-sarif` from 4.39.0 to 4.39.1
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/6f2ac81f5cd81fe351a197ba0b00d91d03cd2aee...9b3254085494b55a0f7c4c2b3d2d52cc712ce370)
- Extend package validation timeout [`a66f34c`](https://github.com/acgetchell/research-repo-tools/commit/a66f34ceb62258ec3ad02658be00b4940429674d)

## [0.2.4] - 2026-10-28

### ⚠️ Breaking Changes

- Replace release-tag and release-tag-preview with tag
  and tag-preview. Replace release-update TAG PREVIOUS DATE with
  update-version TAG --previous-release PREVIOUS --date DATE.
  The removed recipe names have no compatibility aliases.

### Merged Pull Requests

- Protect retained evidence and release workflow outputs [#160](https://github.com/acgetchell/research-repo-tools/pull/160)
- Add verified Criterion suites and faster reports [#159](https://github.com/acgetchell/research-repo-tools/pull/159)
- Add prepared performance and evidence interfaces [#157](https://github.com/acgetchell/research-repo-tools/pull/157)
- Add independent acquisition and source exports [#154](https://github.com/acgetchell/research-repo-tools/pull/154)
- Improve launch, policy, and scoped execution [#153](https://github.com/acgetchell/research-repo-tools/pull/153)
- Manage scanner pin and preserve Rustdoc coverage [#152](https://github.com/acgetchell/research-repo-tools/pull/152)
- Enforce exact pins and inspect composite actions [#151](https://github.com/acgetchell/research-repo-tools/pull/151)
- Support verified opt-in CI evidence reuse [#149](https://github.com/acgetchell/research-repo-tools/pull/149)
- Add evidence foundation and actionable check diagnostics [#148](https://github.com/acgetchell/research-repo-tools/pull/148)
- Bump the github-actions group with 3 updates [#147](https://github.com/acgetchell/research-repo-tools/pull/147)

### Added

- Add evidence foundation and actionable check diagnostics [#148](https://github.com/acgetchell/research-repo-tools/pull/148)
  [`b837fba`](https://github.com/acgetchell/research-repo-tools/commit/b837fba7316345a7590e80ed4c5e10e12f6fe5a3)

#### feat(release): add evidence foundation and actionable check diagnostics
  - Report every unmet release check with status, provider identity and manual
    retry guidance for running CI.
  - Expose exact-commit evidence creation, verification and approval handoffs
    while keeping signed source validation and Cargo archive guarantees distinct.
  - Bind retained archives, native gates, signatures and release asset operations
    to their observed source and immutable provider identities.
  - Retain native Cargo publication after the bounded exact-archive uploader
    evaluation and document the public evidence contract.
  - Strengthen correctness guidance and refresh the staged uv, coverage and
    dependency lock updates, including pypdf 6.21.2.

- Support verified opt-in CI evidence reuse [#149](https://github.com/acgetchell/research-repo-tools/pull/149)
  [`1ba5a53`](https://github.com/acgetchell/research-repo-tools/commit/1ba5a530277b45982f59245da7ed1c1c83e4aa01)

#### feat(release): support verified opt-in CI evidence reuse

  Reuse signed native source checks from a reviewed PR merge tree after an
  equivalent squash release, with pinned execution inputs and approval evidence.
  Bind repository, workflow, checkout, provider, jobs, run attempt, freshness,
  and immutable artifacts while retaining exact-commit publication defaults.

  Expose explicit selection and revalidation through publishing configuration,
  release commands, shared recipes, and consumer migration guidance. Preserve
  exact-release package validation and archive identity.

- Preserve update policy and audit final dependency locks
  [`180add7`](https://github.com/acgetchell/research-repo-tools/commit/180add7a7b7c39cb729b6587d410b5374a7a9a8c)

  Preserve supported uv indexes, source mappings, overrides, and constraints
  while treating current development pins as minimum versions.

  Reject retained-package downgrades before synchronization and recover all
  selected Python manifests, locks, and environments when an update fails.

  Expose consumer audit and security recipes, audit final dependency locks,
  and document independent scanner environments and published adoption.
- Enforce exact pins and inspect composite actions [#151](https://github.com/acgetchell/research-repo-tools/pull/151)
  [`8b3972b`](https://github.com/acgetchell/research-repo-tools/commit/8b3972bb196af80338ace1d8c4e3dfbc794d23f5)

  - Retain exact-SHA allowlist constraints alongside wildcard refs, with
    case-normalized commit matching and diagnostics that preserve approvals.
  - Share composite-step discovery between allowlist checks and opt-in pin
    updates, retaining source locations and atomic, byte-preserving edits.
- Manage scanner pin and preserve Rustdoc coverage [#152](https://github.com/acgetchell/research-repo-tools/pull/152)
  [`e19435a`](https://github.com/acgetchell/research-repo-tools/commit/e19435ab31311e138a09ea0d3a8a27871d4588d9)

#### fix(semgrep): preserve Rustdoc scope and complete scan coverage

  Keep original Markdown and prose inputs alongside Rust blocks scanned at
  their logical source paths, retaining native rule includes and excludes.

  Restore source positions, remove identical overlapping findings, and
  identify incomplete scans by source and block without weakening report
  agreement or coverage checks.

  Support mixed native, prose, and Rust-fence fixture assertions with
  explicit annotation ownership.

- Improve launch, policy, and scoped execution [#153](https://github.com/acgetchell/research-repo-tools/pull/153)
  [`2955286`](https://github.com/acgetchell/research-repo-tools/commit/295528699aa1616c3147c6131a58c9533e40aa38)

#### feat(notebooks): improve launch, policy, and scoped execution
  - Open an explicitly selected notebook through the managed JupyterLab launcher
    while preserving literal paths, browser policy, and private session caches.
  - Detect literal installer wrappers and option values without rejecting
    read-only commands or matching installation examples in strings and comments.
  - Isolate notebook execution in an owned event loop, preserving caller state
    and cleaning up kernels after success, errors, timeouts, and partial startup.

- Add independent acquisition and source exports [#154](https://github.com/acgetchell/research-repo-tools/pull/154)
  [`2267dbf`](https://github.com/acgetchell/research-repo-tools/commit/2267dbf0313d5e17ad30a8d4a2c06e0a53cdacd4)

#### feat(performance): add independent acquisition and source exports

  Add schema-4 acquisition declarations and complete-run/v3 evidence with independent revision harnesses, exact raw retention, and explicit ratio eligibility.

  Capture committed and working-tree sources through read-only verified exports, with source identity rechecks and recoverable publication safeguards. Preserve
  historical files and document the new API and migration boundary.

  Run the maintainer dependency audit after dependency updates.

- Add stderr assertions and portable command timing
  [`6814c0a`](https://github.com/acgetchell/research-repo-tools/commit/6814c0a5d0c075af3d344ead490b94fdcc4848c9)

  - Support independent live stderr assertions in Cargo examples while
    preserving stdout semantics and native executable discovery.
  - Acquire exploratory command wall timings with complete observations,
    provenance, strict validation, and thin consumer recipes.
  - Require uv 0.13.6 and refresh the dependency lock.
- Add prepared performance and evidence interfaces [#157](https://github.com/acgetchell/research-repo-tools/pull/157)
  [`8c62533`](https://github.com/acgetchell/research-repo-tools/commit/8c62533f46dad6520ca242a7370ddf9bd9e7e81e)

#### feat(release): add prepared performance and evidence interfaces

  Separate performance preparation, measurement retention, and README
  publication with independent saved assertions. Resolve optional prospective
  changelog dates from prepared citation metadata and enforce declared
  first-use report inventories.

  Expose configurable release evidence and asset handoffs over the shared
  validation foundation. Reconcile installed consumer tests, templates and
  guides, and remove Just aliases in favor of canonical recipes.

- Add verified Criterion suites and faster reports [#159](https://github.com/acgetchell/research-repo-tools/pull/159)
  [`cea6d33`](https://github.com/acgetchell/research-repo-tools/commit/cea6d33a850eb9cd2f7131f22f9bc28497c9b138)

#### feat(performance): add verified Criterion suites and faster reports
  - Package suite-scoped Criterion captures and assemble complete datasets with
    independently asserted identity, original provenance, exact raw bytes, and
    atomic publication.
  - Index complete-run report cases and rows once while preserving validation
    and supported historical report bytes.
  - Reorganize consumer onboarding and capability guidance into concise indexes,
    task guides, and focused API references with explicit release availability.

### Changed

- Preserve managed Python discovery in update fixtures
  [`85835f3`](https://github.com/acgetchell/research-repo-tools/commit/85835f3edd7cfae39b19f38ef9b240c0351cfaf5)

  Retain setup-uv's managed installation directory when isolating dependency
  fixtures so native CI can find the provisioned Python interpreter offline.

  Require managed Python for scanner discovery and provision both scanner
  and wrapper versions when using a disposable fallback installation.
- Provision managed Python before offline update fixtures
  [`51e6b36`](https://github.com/acgetchell/research-repo-tools/commit/51e6b36bb29f2ccb5cca59137d5e5b888c5511ad)

  Discover a managed Python 3.15 interpreter before running dependency
  contracts, provisioning it in a disposable directory when the runner only
  provides system Python. Reuse that interpreter across the fixture suite
  and its offline update, synchronization, and rollback operations.
- Derive fixture uv requirement from active executable
  [`019b1f4`](https://github.com/acgetchell/research-repo-tools/commit/019b1f4a9fea35afc21641c5777fabf08b095ad2)

  Prepare the isolated environment before querying uv and use its reported
  version in the generated consumer manifest. Retain the exact-version
  requirement without duplicating the repository's current uv pin.

### Documentation

- Keep timing and stream guidance independent of release status
  [`3b02d5e`](https://github.com/acgetchell/research-repo-tools/commit/3b02d5e25dd63786f3374ed22c9df81dbe7a5f6c)

  Keep verified-version pinning and integration requirements in the active
  procedures while leaving release and adoption status in their trackers.

### Fixed

- Protect retained evidence and release workflow outputs [#160](https://github.com/acgetchell/research-repo-tools/pull/160)
  [`628ec38`](https://github.com/acgetchell/research-repo-tools/commit/628ec38958e7020a1bc9ccdf66fb0e7224a327a0)

#### fix: protect retained evidence and release workflow outputs
  - Reject invalid supplemental Criterion files and original ZIP member names
    before reading or publishing evidence.
  - Prevent SARIF command-file aliases from altering inputs or report generations.
  - Resolve CI-reuse paths from the configured consumer root and produce
    deterministic retained suite archives.
  - Clarify complete-run v3 comparison eligibility, Windows executable lookup,
    selection results, Semgrep setup, and reproducible paper-build guidance.
  - Refresh the SARIF upload pin and platformdirs, and strengthen independent
    native-tool asset-name expectations.
#### fix: keep FIFO regressions portable to Windows

  Resolve optional FIFO creation by capability so Windows type checking can
  validate the consumer suite while POSIX hosts retain the native regression.
  Clarify that ratio eligibility checks apply to v3 evidence; v1/v2 return
  an empty tuple without assessing comparability.
#### fix: reject SARIF command files linked to previous outputs

  Check existing output members for command-file inode aliases before replacing
  the report directory so environment exports cannot alter retained hard links
  to the previous generation.
#### fix: stop SARIF publication when output inspection fails

  Propagate directory traversal and member metadata errors during command-file
  alias checks so inaccessible prior outputs cannot bypass validation.

- Restore package checks and fix the PyPI citation link
  [`6dd1fc5`](https://github.com/acgetchell/research-repo-tools/commit/6dd1fc53f7e9b0b49a5b2a37d286e3a0448801aa)

  - Align CI recipe expectations with the build and publish-check sequence.
  - Cover publication-check failure propagation and package-name mismatches.
  - Use an absolute CITATION.cff URL so the citation link works on PyPI.

### Maintenance

- Bump the github-actions group with 3 updates [#147](https://github.com/acgetchell/research-repo-tools/pull/147)
  [`6a0448c`](https://github.com/acgetchell/research-repo-tools/commit/6a0448c009442e8cad395412752215cecb2b8225)

  Bumps the github-actions group with 3 updates: [github/codeql-action/init](https://github.com/github/codeql-action),
  [github/codeql-action/analyze](https://github.com/github/codeql-action) and [github/codeql-action/upload-sarif](https://github.com/github/codeql-action).

  Updates `github/codeql-action/init` from 4.38.3 to 4.39.0

  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/24c54180a607b1449ed407dd24f251e4e9147c8d...6f2ac81f5cd81fe351a197ba0b00d91d03cd2aee)

  Updates `github/codeql-action/analyze` from 4.38.3 to 4.39.0
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/24c54180a607b1449ed407dd24f251e4e9147c8d...6f2ac81f5cd81fe351a197ba0b00d91d03cd2aee)

  Updates `github/codeql-action/upload-sarif` from 4.38.3 to 4.39.0
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/24c54180a607b1449ed407dd24f251e4e9147c8d...6f2ac81f5cd81fe351a197ba0b00d91d03cd2aee)
- [**breaking**] Release v0.2.4 [`437679a`](https://github.com/acgetchell/research-repo-tools/commit/437679a9d142864ad99a41ccbe047a77d3c3a09f)

  - Bump package and citation metadata to v0.2.4.
  - Generate the release changelog.
  - Align release documentation and shared recipes with Delaunay's workflow.
  - Add CITATION.cff, adopt declared citation dates, and document Zenodo
    verification.
  - Add strict PyPI publication checks for wheel and sdist artifacts,
    including CI integration.
  - Require final metadata checks before tagging and default registry
    verification to seven attempts.

## [0.2.3] - 2026-10-21

### Changed

- Verify independent Python versions and overrides
  [`5e78500`](https://github.com/acgetchell/research-repo-tools/commit/5e7850095373359e656b8adc79033aac26bea7f5)

  Exercise the installed updater with Python 3.15 and 3.14 projects, retained scanner pins, transitive dependency overrides, and conflicting caller environment
  settings.
- Avoid user interpreter registration in install checks
  [`3b018e4`](https://github.com/acgetchell/research-repo-tools/commit/3b018e45ef375247ccc80faeb2dfda09a51c4c85)

  Use the existing managed-installer no-bin and no-registry policy when acquiring Python 3.14 for the mixed-interpreter update fixture. This keeps installation
  checks from publishing user commands or Windows registry entries.
- Cover sealing and retain generated v0.2.3 notes [`f00a2a8`](https://github.com/acgetchell/research-repo-tools/commit/f00a2a85cdf9ffebb1a2e8283deb88cc1135755c)

  Exercise original main evidence production and immutable validator retrieval. Generate the v0.2.3 changelog with its owning workflow while temporarily
  excluding only the unpublished local candidate tag, then restore the original tag object. Document that recovery procedure.

### Fixed

- Update declared independent Python projects [`bde1041`](https://github.com/acgetchell/research-repo-tools/commit/bde104133a5a1cb4bb4a674e46f8032a93ca6559)

  - Add an opt-in complete Python update workflow with explicit project declarations.
  - Refresh and sync each project in its own environment while retaining tool pins and Python constraints.
  - Route shared update recipes through the package and document consumer adoption.
  - Refresh the development lock to Tornado 6.6.
- Identify invalid independent project manifests [`44754a6`](https://github.com/acgetchell/research-repo-tools/commit/44754a60d37bfcfb112f2e23a0b8308d778a4952)

  Preserve main-project diagnostics while naming invalid independent projects before any updates. Cover malformed TOML, missing project metadata, invalid Python
  constraints and unchanged root diagnostics; exercise the public path context from wheel and sdist installs. Link consumer setup guidance from its README
  owner.
- Promote validated main artifacts without rebuilding
  [`6af125d`](https://github.com/acgetchell/research-repo-tools/commit/6af125d7c6401b22f8694746bacdf475a22d1936)

  Bind immutable distribution IDs, ZIP digests, native and installed validation job results, source commit and exact main-push attempt in signed evidence.
  Verify original validation and tag-promotion signatures before staging and approval-gated publication. Reject mismatched, expired, ambiguous or partial
  evidence with bounded waiting and recovery instructions.

  Retain PR/main native gates and dependency audit, extend release artifacts to a 30-day promotion window, and update the manual v0.2.3 procedure. Add source
  and installed public regressions. Related to #122; consumer adoption tracking remains open.
- Preserve fork CI and bounded recovery [`56d9d96`](https://github.com/acgetchell/research-repo-tools/commit/56d9d96db18b183e77f3f584ea33f67d59d927a0)

  Scope ordinary artifact consumption to the executing repository while keeping promotion restricted to the owning main-push workflow. Cover fork retrieval and
  reject external site startup in the active interpreter under isolated standard-library mode. Explain the 30-day rerun limit and explicit reviewed
  unpublished-candidate recovery after expiration.

### Maintenance

- Regenerate v0.2.3 notes after review fixes [`d508533`](https://github.com/acgetchell/research-repo-tools/commit/d50853392a88389af158a03b585206af5756c85d)

## [0.2.2] - 2026-10-21

### Fixed

- Enforce configured measurement release floor [`5040d77`](https://github.com/acgetchell/research-repo-tools/commit/5040d776be65a52f78b8f1c0f0ac2d3d8ef403c6)

## [0.2.1] - 2026-10-21

### Added

- Complete declarative benchmark workflows [`6d7039f`](https://github.com/acgetchell/research-repo-tools/commit/6d7039f3a987c7b9fdbc55c95d27f8a7c8c50fb5)

  - Validate retained runs against independent measurement declarations and
    resolve bounded series selectors from discovered phase inventories.
  - Support runtime saved-sample names, output directories, required IDs, and
    declared storage paths without consumer Python adapters.
  - Reuse publication declarations with independent release identities,
    recorded version labels, artifact-only rendering, and transactional
    Markdown formatting.
  - Document static CLI workflows, shared snapshot semantics, stricter
    eligibility requirements, and the downstream release handoff.
  - Refresh the uv pin and dependency lock from the tooling update.

### Fixed

- Preserve historical evidence readability [`984a18e`](https://github.com/acgetchell/research-repo-tools/commit/984a18ecce7fac98c559825f7992dd7d6a7afd52)

  Enforce distinct source identities for equal release labels during policy
  validation so existing retained runs remain readable and renderable.

  Validate declared Criterion storage paths by normalized component names,
  preserving alias and overlap rejection without consulting the filesystem.
- Preserve policy inputs and release eligibility [`82494a3`](https://github.com/acgetchell/research-repo-tools/commit/82494a3e5e1b3e671dc14119de28178c683ff1a0)

  Reject report outputs that alias the independent measurement policy and
  compare canonical release tags before policy promotion. Preserve retained
  evidence readability and clarify selector and formatter documentation.

## [0.2.0] - 2026-10-19

### Merged Pull Requests

- Bump the github-actions group with 6 updates [#111](https://github.com/acgetchell/research-repo-tools/pull/111)

### Added

- Support compact benchmark tables [`8769b86`](https://github.com/acgetchell/research-repo-tools/commit/8769b86becdcdf467b6409c1e997b3249258406a)

  - Add public and declarative controls for headings, interval placement,
    shared timing legends, comparison columns, and ratio suffixes.
  - Preserve default formatting, recorded evidence, and comparison semantics.
  - Keep confidence captions independent of caller Decimal settings.

### Fixed

- Preserve text and platform contracts in shared tooling
  [`1a27a5c`](https://github.com/acgetchell/research-repo-tools/commit/1a27a5c75cfd5c3ab2da62aeebf5dc27d36a6bc1)

  - Preserve Unicode prose, line endings, and literal changelog code during
    TOML and release-metadata edits.
  - Restore interrupted file publication and Python environment adoption through
    the shared recovery contract.
  - Reject notebook path conflicts before execution and honor supplied Windows
    PATH and PATHEXT values during executable discovery.
  - Preserve leading-dash coverage prefixes and same-repository Actions references.
  - Expose template recipes, use the locked Just formatter, and refresh packaged
    action pins, Python tooling dependencies, and operational documentation.

### Maintenance

- Bump the github-actions group with 6 updates [#111](https://github.com/acgetchell/research-repo-tools/pull/111)
  [`eb01d53`](https://github.com/acgetchell/research-repo-tools/commit/eb01d53e286a36853c26fc6c9a410178f2e6bc62)

  Bumps the github-actions group with 6 updates:

  | Package | From | To |
  | --- | --- | --- |
  | [astral-sh/setup-uv](https://github.com/astral-sh/setup-uv) | `10.2.0` | `10.3.0` |
  | [actions/upload-artifact](https://github.com/actions/upload-artifact) | `7.0.1` | `7.0.2` |
  | [actions/download-artifact](https://github.com/actions/download-artifact) | `8.0.1` | `8.0.2` |
  | [github/codeql-action/init](https://github.com/github/codeql-action) | `4.38.2` | `4.38.3` |
  | [github/codeql-action/analyze](https://github.com/github/codeql-action) | `4.38.2` | `4.38.3` |
  | [github/codeql-action/upload-sarif](https://github.com/github/codeql-action) | `4.38.2` | `4.38.3` |

  Updates `astral-sh/setup-uv` from 10.2.0 to 10.3.0

  - [Release notes](https://github.com/astral-sh/setup-uv/releases)
  - [Commits](https://github.com/astral-sh/setup-uv/compare/c18668ad3cf93ea998bef934396af7bb5c839dc7...1c37ad07a6a961277cf70c0d37d6f313000f5884)

  Updates `actions/upload-artifact` from 7.0.1 to 7.0.2
  - [Release notes](https://github.com/actions/upload-artifact/releases)
  - [Commits](https://github.com/actions/upload-artifact/compare/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a...cf430e030ddbb5b0abf93d22962f4752f3646cd9)

  Updates `actions/download-artifact` from 8.0.1 to 8.0.2
  - [Release notes](https://github.com/actions/download-artifact/releases)
  - [Commits](https://github.com/actions/download-artifact/compare/3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c...9000827ccba6bdab643e8b6fd33ac0654aef8333)

  Updates `github/codeql-action/init` from 4.38.2 to 4.38.3
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2...24c54180a607b1449ed407dd24f251e4e9147c8d)

  Updates `github/codeql-action/analyze` from 4.38.2 to 4.38.3
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2...24c54180a607b1449ed407dd24f251e4e9147c8d)

  Updates `github/codeql-action/upload-sarif` from 4.38.2 to 4.38.3
  - [Release notes](https://github.com/github/codeql-action/releases)
  - [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md)
  - [Commits](https://github.com/github/codeql-action/compare/2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2...24c54180a607b1449ed407dd24f251e4e9147c8d)

## Archives

Older releases are archived by minor series:

- [0.1.x](docs/archives/changelog/0.1.md)

[0.2.7]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.9...v0.2.0
