# Generating and maintaining changelogs

The public commands always operate on root-level `CHANGELOG.md`. Completed
minor series go to `docs/archives/changelog/MAJOR.MINOR.md`. There is no
repository-specific implementation or filename setting.

## Commands

The maintainer justfile and packaged consumer template expose the same changelog
recipes. `just changelog` generates history, normalizes it, and rotates completed
minor series in one operation. It keeps Unreleased and the newest minor series
in the root file and adds links to the archives. Before a second minor series
exists, there is nothing to archive.
Run `just help` for the complete command list and arguments.

| Recipe | Behavior |
| --- | --- |
| `just changelog` | Generate, normalize, and archive |
| `just changelog-archive` | Archive existing notes without regenerating Git history |
| `just changelog-check` | Validate the whole root changelog and all archives without writing |
| `just changelog-preview` | Validate all candidate files and print the resulting root changelog without writing |
| `just changelog-release TAG DATE` | Generate and archive a prospective release with an explicit ISO date |
| `just changelog-unreleased TAG DATE` | Alias for `changelog-release` |
| `just release-notes TAG` | Print notes from the root file or an archive |
| `just tag TAG` | Forward to `tag-release`; create a local annotated tag |
| `just tag-force TAG` | Explicitly replace an existing local tag |
| `just tag-release TAG` | Create a local annotated tag from validated release notes |

For a prospective-release preview, use
`just changelog-preview --tag v1.2.0 --date YYYY-MM-DD`.
Both release-generation names require the chosen date; they do not silently use
the current date or update other release metadata. Generation never creates tags.
Standalone `research-repo-tools changelog generate` runs the same complete workflow.

## Generation and normalization

The installed templates comprise a git-cliff configuration, a starter
changelog, small just wrappers, consumer TOML, and optional rumdl rules.
Generation uses the packaged git-cliff template unless the consumer explicitly
sets `changelog.cliff-config`. Owner and repository identify links; they do not
select a policy profile. Generation is offline and disables template commands.
A prospective tag requires an explicit ISO date, keeping output independent
of an accidental wall-clock date.
For existing releases, dated headings in the root changelog and retained archives
are authoritative: regeneration preserves them over Git-derived dates. Conflicting
declared dates, or a prospective date that disagrees with an existing declaration,
fail before publication. Undated history supplies no date authority. Regeneration
after tagging a prospective release retains its declared date; citation metadata
is never silently rewritten to match Git timestamps.
Generated output must contain at least one valid release or Unreleased section
before it can replace existing history.
Before the first release tag, the Unreleased reference links to the repository's
commit history. Once a release exists, it links to the comparison with that tag.

The packaged template preserves complete breaking-change footer descriptions,
including compiler requirements, multiline migration instructions, and dependency
breaks. Commits marked only with `!` use their subject as the fallback summary.
Across summaries and ordinary entries, angle brackets in prose are escaped while
inline code spans and fenced code retain their literal contents. Markdown links,
autolinks, and blockquote markers remain intact.
Postprocessing retains those descriptions and independently adds a missing
Merged Pull Requests summary. Existing summary text is not used as a source of
new PR entries.

Release tags must match the complete `vMAJOR.MINOR.PATCH` SemVer form, including
valid optional prerelease and build identifiers. Unrelated or malformed tags do
not delimit releases. Dependency bumps with `chore(deps)` or `chore(deps-*)`
scopes share the Dependencies category; breaking migration instructions remain
in the breaking-change summary.

Normalization uses UTF-8 and LF, one final newline, consistent list markers,
160-column prose reflow, intact Markdown links/code spans, and level-four
entry headings beneath release categories. Breaking-change and pull-request
summaries retain their links. Fenced code is opaque to prose transformations;
backtick and tilde delimiters, delimiter lengths, and fence-contained examples
of releases are preserved. A missing code language becomes `text`.
Markdown pipe tables retain their rows and cells, including rows longer than
the prose width limit.
Feature names and consumer-specific wording are preserved rather than migrated.

Embedded conventional headings such as `fix:` and `feat:` remain under their
authored parent entry, with their original prefix, wording, and context. They
do not become extra categorized release entries. Similarly worded entries and
repeated headings remain intact, even when they share a commit link. Normalization
does not infer semantic equivalence, remove contextual excerpts, or rename titles
with invented follow-up suffixes. This supersedes the earlier promotion and
contextual deduplication policy. Entry-local titles still use level-four Markdown
headings, and clearly labeled PR/breaking summaries remain supported.
All dependency bumps, including CI and development tools, remain concise entries
in the separate Dependencies category.

`changelog.formatter` optionally names the consumer's rumdl configuration.
Every generated root and archive candidate must pass both the fixing invocation
and a final check before any replacement. Formatter failure or unexpectedly empty
output preserves the originals. The base package does not require rumdl for pure
normalization.

Shared heading parsing rejects malformed versions/dates, duplicate release headings,
out-of-order versions, and misplaced Unreleased sections. SemVer prerelease
and build labels and inline/reference release links are supported. Fenced
example headings do not create release blocks. Version digits must be ASCII.
Generation and release metadata use the same parsed headings and date locations.
Body and archive-introduction reference definitions are retained with their text.
Cross-volume paths that cannot form portable links
fail before publication rather than embedding developer-specific absolute paths.

## Archive publication

Generation and standalone archiving share one planner. Dry-run generation checks
archive conflicts and formatting without creating directories or changing files.
Publication stages every candidate and backup before replacing any
file. Caught failures trigger rollback; an incomplete rollback preserves recovery
files and reports their locations and each failure. Multiple files are not
crash-atomic: process termination or power loss still requires inspection.

Incremental archiving merges previously retained patches and keeps existing
archive introductions and index links. Different content for an already
retained release fails before publication. When generation uses a formatter,
retained and incoming archive documents pass through the same normalization and
formatter at the archive output path before their release blocks are compared.
Formatting differences alone do not conflict, while changed content and reference
destinations still fail. Repeating generation with unchanged history and formatter
configuration produces the same root/archive bytes. Relative Markdown links are rebased
when notes move into the archive; fenced examples and inline code stay intact.

## Release notes and tags

`changelog notes TAG` finds the release in the root file or its conventional
archive and emits only notes and their required reference definitions. Extraction
validates the requested release and its interpretation, so unrelated historical
misordering, duplicate non-target releases, or malformed historical headings with
clear boundaries do not block valid notes. Duplicate target releases within a file
or across root/archive, malformed target headings, ambiguous section boundaries,
unclosed fences, and conflicting required references still fail. Fenced examples
never count as release headings. Extraction does not write any files.

Successful extraction is not whole-document validation. Run `just changelog-check`
(`research-repo-tools changelog check`) to validate every release heading, ordering,
duplicate release identity, reference-definition conflict, and archive minor-series
membership across the root and archives. This check is read-only. Generation and
publication retain strict validation; `changelog tag` performs the full changelog
check as well as checking package version and release date before any tag mutation. By default
the date must be today's UTC date; `release.date-policy = "declared"` explicitly
permits historical release dates. Oversized annotations link to the complete
notes. `--dry-run` previews notes. `--force` uses Git's ref replacement and never
deletes the old tag before creating its replacement. Tagging is local only.

Regression tests live under `tests/changelog/`, including real Git and git-cliff
scenarios using minimal disposable repositories.
