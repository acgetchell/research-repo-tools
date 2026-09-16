# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-16

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

### Fixed

- Harden dependency updates and release preparation
  [`dd22de4`](https://github.com/acgetchell/research-repo-tools/commit/dd22de49348d5436ec272bf1d3bffc818593bcf3)

  - Retain manifest and lockfile recovery backups when rollback fails, and honor configured uv executables throughout Python pin updates.
  - Match normalized Python distribution names in uv.lock and derive runtime and CLI versions from installed package metadata.
  - Preserve Markdown tables during normalization and support changelog generation before the first release tag.
  - Render coverage paths consistently across platforms.
  - Add just update and changelog recipes using the shared tooling, and regenerate the repository changelog from commit history.
  - Extend type checking to scripts and tests, and refresh Ruff, ty, and locked dependencies.

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

[0.1.0]: https://github.com/acgetchell/research-repo-tools/tree/v0.1.0
