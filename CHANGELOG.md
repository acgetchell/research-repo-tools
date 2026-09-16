# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Prepare PyPI releases with Trusted Publishing [`2036a6c`](https://github.com/acgetchell/research-repo-tools/commit/2036a6c63983b29bc80053e0917bc0fc116485a6)

  - Add tagged publication using OIDC credentials and attestations.
  - Build distributions once and publish the artifacts validated across Linux, macOS, and Windows.
  - Gate publication on main-branch ancestry, synchronized release metadata, generated notes, and dependency auditing.
  - Reject incomplete distribution sets and preserve attribution bytes across Windows checkouts.
  - Document supported CLI and Python interfaces, consumer just recipes, and publisher setup.
  - Generate the initial 0.1.0 changelog from committed history.

[Unreleased]: https://github.com/acgetchell/research-repo-tools/commits/HEAD
