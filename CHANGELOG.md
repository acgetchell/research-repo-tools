# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.4] - 2026-10-28

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

[0.2.4]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.9...v0.2.0
