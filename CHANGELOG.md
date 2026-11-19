# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-11-19

### Merged Pull Requests

- Harden release evidence and tooling edge cases [#257](https://github.com/acgetchell/research-repo-tools/pull/257)
- Recover release drafts and reuse benchmark preparation [#256](https://github.com/acgetchell/research-repo-tools/pull/256)

### Fixed

- Make unpublished candidate replacement resumable
  [`8a3c04c`](https://github.com/acgetchell/research-repo-tools/commit/8a3c04ca2c02b4755442d43fafee3cff4cb82570)

  - Forward `--force` through the maintainer and consumer `just tag` recipes so validation runs before the local tag is replaced.
  - Record tag and draft identities so recovery can resume after local tag replacement, draft deletion, or the guarded tag push.
  - Clarify when the replacement push starts draft preparation and when to resume publication.
  - Update locked click-option-group to 0.5.10 and python-multipart to 0.0.34.
- Recover release drafts and reuse benchmark preparation [#256](https://github.com/acgetchell/research-repo-tools/pull/256)
  [`c8ee092`](https://github.com/acgetchell/research-repo-tools/commit/c8ee092deba9d9efff15bcec87c2d11e65216e84)

  - Wait for the captured GitHub draft to become visible without creating a
    second draft. Report exhausted registry retries with recovery guidance
    that preserves the original dependency pins and CI attempt.
  - Add `release visibility` to verify an immutable GitHub release, its
    required assets, and the exact registry version from outside a checkout.
  - Reuse verified benchmark exports and executables within an opt-in
    storage budget while preserving preflight checks and completed pairs.
  - Update the development uv pin to 0.13.9.
- Harden release evidence and tooling edge cases [#257](https://github.com/acgetchell/research-repo-tools/pull/257)
  [`ee5264d`](https://github.com/acgetchell/research-repo-tools/commit/ee5264dd4e1c1192e9b56ec58ab245ad8ab9ae7b)

#### fix: harden release evidence and tooling edge cases
  - Reject aliased Cargo archive members and overlapping file and directory paths.
  - Summarize command timings at nanosecond precision.
  - Separate CI environment assignments and honor Python source encodings in the newline check.
  - Document the published tooling pin and the v0.3.0 command and recovery contracts.
  - Refresh the locked dependencies.
#### fix: bound archive member path depth

  Reject archive member paths over 128 components before building ancestor spellings for portable path checks.

## Archives

Older releases are archived by minor series:

- [0.2.x](docs/archives/changelog/0.2.md)
- [0.1.x](docs/archives/changelog/0.1.md)

[0.3.0]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.9...v0.3.0
