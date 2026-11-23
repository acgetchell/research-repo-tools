# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] - 2026-11-23

### Merged Pull Requests

- Support benchmark series, candidate preflight, and changelog rebuilds [#271](https://github.com/acgetchell/research-repo-tools/pull/271)

### Added

- Support benchmark series, candidate preflight, and changelog rebuilds [#271](https://github.com/acgetchell/research-repo-tools/pull/271)
  [`50411be`](https://github.com/acgetchell/research-repo-tools/commit/50411bece810360c831413ccb35751b6f8dcd61d)

#### feat(release): support benchmark series and offline candidate checks

  Capture a new workload baseline before tagging, bind later paired acquisitions to published series history, and verify candidate source and report evidence
  before merge.

  Add a preview-first changelog rebuild, retain skipped-file details for incomplete CodeRabbit reviews, and consolidate consumer and release documentation.

#### fix(release): reject shallow rebuilds and recheck candidate source after history lookup

  Cover shallow preview and apply, and reject a tracked edit made during final candidate verification. Clarify how to restore the release commit and reconcile
  an incomplete draft.

#### docs(release): verify evidence before resuming and reconcile partial drafts

  Recover the post-tag verification summary before watching the draft workflow. Identify failed uploads by draft and asset ID, preserve the signed attachment,
  and specify when staging can safely retry.

- docs(release): route failed draft creation to retry checks

---

- Save verified evidence and capture draft targets with Just
  [`64d0194`](https://github.com/acgetchell/research-repo-tools/commit/64d0194c51dfb41ce57939dc8ad4dfd1fd2d372a)
- Wait for draft preparation within the capture recipe
  [`fc64e46`](https://github.com/acgetchell/research-repo-tools/commit/fc64e469cfb16a61464003d09dc681d13fb09e6b)

### Changed

- Merge the release handoff recipes into release/v0.3.2
  [`090652a`](https://github.com/acgetchell/research-repo-tools/commit/090652a4801a0b249a4be6cc8e12da3dc569efc2)

### Documentation

- Clarify consumer setup and release steps [`9bc4cc3`](https://github.com/acgetchell/research-repo-tools/commit/9bc4cc38c9c28fca7c3e1d95edb1ce45aff08dcb)

  Document fresh installs and upgrades to v0.3.1 with the matching Justfile and bootstrap helper. Show maintainers how to find the reviewed merge commit and CI
  run and reuse the captured draft ID through publication and verification.

### Fixed

- Retain evidence automatically with or without an issue tracker
  [`7e4c2d9`](https://github.com/acgetchell/research-repo-tools/commit/7e4c2d91077a2371c68eb28afc7aadf012f74dc6)
- Preserve tagged dependency evidence and reuse the saved draft target
  [`1246c94`](https://github.com/acgetchell/research-repo-tools/commit/1246c9400e00159f81b22c95ae04c5428c68547e)
- Correct recovery for v0.3.2 [`a96f38c`](https://github.com/acgetchell/research-repo-tools/commit/a96f38c3cee3db7743521d9f7f5a5da09184c039)

## [0.3.1] - 2026-11-21

### Merged Pull Requests

- Preserve squash details and link closed issues [#263](https://github.com/acgetchell/research-repo-tools/pull/263)
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

### Fixed

- Preserve squash details and link closed issues [#263](https://github.com/acgetchell/research-repo-tools/pull/263)
  [`f913d83`](https://github.com/acgetchell/research-repo-tools/commit/f913d83c9f126c1128b50e29b3c65e463eb288ba)

#### fix(changelog): preserve squash details and link closed issues

  Render full squash bodies and link explicit issue closures from the original commits in current and archived releases. Preserve distinct entries with repeated
  titles and validate Markdown links across the complete output before writing.

  Regenerate the project history with the shared rules and use the same 160-column limit in the editor.

#### fix(changelog): retain nested details and resolve escaped archive links

  Keep child subsections attached to repeated titles and recognize generated commit links across wrapped title lines. Validate escaped relative links against
  the existing target when planning new archives.

#### fix(changelog): preserve heading targets and wrapped issue notes

  Retain navigable numbered headings when release notes contain local fragment links. Recognize closing annotations whose first link wraps and reject
  conflicting link destinations on regeneration.

#### fix(changelog): retain subsection prose after link definitions

  Keep body reference definitions and subsequent prose in their generated list item while retaining document-level definitions outside the final item.

#### fix(changelog): decode escaped fragment delimiters before checking paths

  Resolve Markdown escapes before separating the path from its fragment or query. Keep percent-encoded delimiters in filenames and exercise both forms with real
  archive validation.

#### fix(changelog): preserve fenced issue examples and formatted heading targets

  Protect closing directives during commit parsing and distinguish literal examples during Markdown normalization. Use parsed inline heading text to preserve
  navigation, and recognize existing issue notes after blank separators.

#### fix(changelog): retain literal titles and nested archive links

  Preserve Unicode and marker-like subject text when reading issue references. Use CommonMark block boundaries to rebase nested list links while keeping code
  examples intact.

#### fix(changelog): resolve character references in archived links

  Use CommonMark destination decoding for both punctuation escapes and character references before checking archive targets. Cover named and numeric ampersands
  during generation and regeneration.

#### fix(changelog): restore archived squash details and preserve custom issue notes

  Compare title-only archives against the same captured Git history before restoring missing details. Preserve custom closing directives and separate commit
  entries from preceding detail lists.

#### fix(changelog): preserve literal issue examples and linked headings

  Recognize multiline code when collecting and rendering closing directives. Retain heading targets reached by local Markdown links, including path-qualified
  references.

#### fix(changelog): match archived closing directives before migration

  Verify the original archived directive against the prior rendering before replacing it with a link. Reject a changed issue number or edited release text.

#### fix(changelog): remove only directives covered by generated issue links

  Use the original commit to match every target in a closing directive before removing its raw text. Keep custom templates, literal examples and unmatched
  references intact.

#### fix(changelog): attach issue links to their original entries

  Annotate commit entries before their subsections are formatted. Reconstruct retained releases with the notes they already contain, resolve reference-style
  heading labels, and preserve literal indented fence examples during generation and extraction.

#### fix(changelog): preserve authored issue paragraphs and Markdown targets

  Keep linked prose when adding an independent closing note. Count rendered headings when preserving fragment links and keep literal code indented under
  repeated subsections.

#### fix(changelog): retain paragraph breaks and link community repositories

  Preserve paragraph boundaries when removing linked directives, and recognize GitHub repository names such as .github in issue references and shared templates.

#### fix(changelog): keep issue examples in preformatted HTML literal

  Distinguish issue directives in prose from code rendered by Markdown or HTML before adding a closing link.

#### fix(changelog): keep cited commits and code examples in their subsections

  Preserve the displayed indentation of literal examples when a list moves. Keep commit citations in the authored detail so repeated normalization lists only
  genuine entries in the pull request summary.

#### fix(changelog): retain archive heading targets and indented code

  Number repeated headings within each generated document so links from the root or an archive reach the intended subsection. Preserve literal code when nesting
  repeated subsections.

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

[0.3.2]: https://github.com/acgetchell/research-repo-tools/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/acgetchell/research-repo-tools/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/acgetchell/research-repo-tools/compare/v0.2.9...v0.3.0
