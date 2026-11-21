# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Merged Pull Requests

- Automate supplier adoption and release verification [#262](https://github.com/acgetchell/research-repo-tools/pull/262)

### Changed

- Automate supplier adoption and release verification [#262](https://github.com/acgetchell/research-repo-tools/pull/262)
  [`780ecdd`](https://github.com/acgetchell/research-repo-tools/commit/780ecdd89d47f5144ed5d29d5b635e00fc8477b1)
  Closes [#259](https://github.com/acgetchell/research-repo-tools/issues/259), [#260](https://github.com/acgetchell/research-repo-tools/issues/260),
  [#261](https://github.com/acgetchell/research-repo-tools/issues/261).

#### feat(release): automate supplier adoption and streamline release checks

- Opt in to exact PyPI supplier upgrades and attested Git-pin promotion
  while keeping the manifest, lock, installed tools, and source policy aligned.
- Verify the reviewed commit during tagging, report the new tag and notes,
  and share the managed Cargo cache between locked fetch and release checks.
- Add a configurable registry GET budget and identify the slow package or
  artifact in connection, stalled-read, and deadline diagnostics.
- Reuse authenticated dependency observations and cached crate bytes where
  allowed while refreshing registry yank status before publication.
- Keep GitHub and registry approvals separate and verify the published
  crate checksum against the retained archive on the immutable release.
- Preserve concurrent notebook edits, bind legacy baselines to the bytes
  extracted, and fix timing-only reports and incomplete release discovery.

#### feat(security): add Gitleaks finding and exception review

  Summarize redacted scan reports and compare proposed repository policies against the same working-tree snapshot and Git history. Ship both commands in the
  consumer Justfile and document the exception review workflow.

  Preserve normalized Python pin names and exercise retained crate verification through Cargo upload-timeout recovery.

#### fix(deps): honor Python support when selecting supplier releases

  Filter registry upgrades and automatic Git promotions by the Python range of the supplier declaration, including group constraints. Use the packaging 26.3
  range API to identify releases whose available artifacts cover that range.

#### fix(deps): compare equivalent supplier pin versions

  Accept equivalent PEP 440 spellings in manifest declarations while requiring one exact source and matching lock version.

#### fix: address release and security review findings

  Apply registry deadlines across address attempts and proxy TLS, reject late absence evidence, and normalize the public PyPI origin before transport selection.

  Recheck source policy absence and Python promotion coverage, preserve tag preview recovery options, and improve Cargo cache diagnostics. Clarify Gitleaks scan
  target names identified by CodeQL and put supplier setup in the README.

#### fix: complete registry and Gitleaks review follow-ups

  Bound DNS wait time with a capped set of native resolver workers and cover build-only declarations when selecting a promotion candidate.

  Run the working scanner from its snapshot and retain native TOML policies with their local extension closure. Document the separate PyPI verification step and
  validate anchored paths with the pinned scanner.

#### fix(security): reject ambient Gitleaks ignores

  Fail closed when Gitleaks could load an ambient ignore file. Describe matched locations without implying equal detected bytes, and identify invalid reports
  without exposing their contents. Run the native fixture with isolated Git configuration.

#### fix: bind review inputs and diagnostics to captured paths

  Select supplier versions from the observed manifest bytes, name invalid Gitleaks configs and extensions without printing their contents, and isolate tag
  fixtures from global Git settings.

#### fix: finish timeout and recovery review checks

  Reset the registry socket timeout after TLS and before the request write. Preserve explicit checkout and policy paths in tag retries. Reject scanner reports
  whose rendered bytes would exceed the reader limit.

#### fix(deps): honor Python markers in supplier selection

#### fix(security): retain only completed Gitleaks reports

#### fix: close scanner, registry, and dependency review findings

#### fix: preserve scan failures and workspace Python support

#### fix(security): verify complete exception scan inputs

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
  Closes [#254](https://github.com/acgetchell/research-repo-tools/issues/254), [#255](https://github.com/acgetchell/research-repo-tools/issues/255).

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

[Unreleased]: https://github.com/acgetchell/research-repo-tools/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.9...v0.3.0
