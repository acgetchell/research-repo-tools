# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.3]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/acgetchell/research-repo-tools/compare/v0.1.9...v0.2.0
