# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- An installable Python package for shared changelog handling, release metadata,
  dependency pins, Semgrep fixture validation, Markdown checks, and coverage summaries.
- Packaged templates and isolated wheel/sdist installation checks using uv.
- One common contract per capability, tested with small synthetic inputs.

### Changed

- Standardize on `CHANGELOG.md` and `docs/archives/changelog/`.
- Accept canonical Cargo prereleases and build metadata while requiring stable uv pins.
- Limit this first version to shared maintenance workflows; defer notebook,
  benchmark, plotting, and evidence tooling.

### Removed

- Copied repository fixtures, historical implementations, duplicate test suites,
  consumer profiles, custom release rewrites, and extraction/publishing drafts.
