# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- GitHub security configuration, CodeRabbit-gated Dependabot auto-merge,
  Python and Actions CodeQL analysis, workflow linting, and dependency auditing.
- An installable Python package for shared changelog handling, release metadata,
  dependency pins, Semgrep fixture validation, Markdown checks, and coverage summaries.
- Packaged templates and isolated wheel/sdist installation checks using uv.
- The pinned `just` executable and source attribution in every installation.
- One common contract per capability, tested with small synthetic inputs.

### Changed

- Standardize on `CHANGELOG.md` and `docs/archives/changelog/`.
- Accept canonical Cargo prereleases and build metadata while requiring stable uv pins.
- Limit this first version to shared maintenance workflows; defer notebook,
  benchmark, plotting, and evidence tooling.

### Removed

- Copied repository fixtures, historical implementations, duplicate test suites,
  consumer profiles, custom release rewrites, and extraction/publishing drafts.

### Fixed

- Preserve full breaking-change footers in the packaged git-cliff template and
  add missing PR summaries without replacing existing breaking descriptions.
- Keep release validation paths contained, including workspace paths using `..`.
- Preserve changelog history on invalid generator output, archive introduction
  references, and consumer feature names in prose, code, and links.
- Share strict ASCII SemVer and fence-aware release heading/date parsing across
  generation, archiving, and release metadata; preserve linked headings.
- Locate release keys under commented or quoted TOML headers without interpreting
  multiline value text as metadata.
- Reject incomplete Semgrep scans consistently and resolve configured executables
  from the consumer root.
- Keep DOI references optional, correct Cargo tool-name documentation, and remove
  POSIX permission and shell-command assumptions from portable tests.
