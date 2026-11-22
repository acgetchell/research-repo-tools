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
| `just changelog-rebuild [--apply]` | Preview the complete rebuild diff or explicitly apply it |
| `just changelog-release TAG [DATE]` | Generate and archive with a citation-owned or explicit ISO date |
| `just release-notes TAG` | Print notes from the root file or an archive |
| `just tag TAG [--commit SHA] [--force]` | Create the validated annotated tag and print its target and annotation |
| `just tag-preview TAG [--commit SHA] [--force]` | Run the tag checks and print the proposed annotation |

For a prospective-release preview, use
`just changelog-preview --tag v1.2.0`.
This repository and the packaged consumer template set
`changelog.date-source = "citation"`
to use the matching prepared citation date when DATE is omitted. The optional
`changelog.citation` path defaults to `CITATION.cff`. Then both
`just changelog-release "$TAG"` and `just changelog-preview --tag "$TAG"`
consume the same date. Without that setting the default remains `"explicit"`.
Neither mode uses today's date or updates metadata. Generation never creates tags.

Citation lookup requires exactly one top-level scalar `version` matching the tag
and one nonempty `date-released` in valid calendar `YYYY-MM-DD` form. Quoted and
unquoted dates, including leap days, work. Duplicate, missing, malformed, empty,
nonscalar and mismatched data fail before invoking the generator. Explicit dates
bypass citation lookup; untagged history never consults it. `changelog.resolve_date`
is the public API shared by generation and preview. Existing declared changelog
and archive dates still reject conflicts.

An explicit troubleshooting override remains available:

```sh
RELEASE_DATE="$(date -u +%F)"
just changelog-release "$TAG" "$RELEASE_DATE"
```

Here `date -u` deliberately chooses today's UTC date. The maintainer must ensure
it agrees with prepared citation metadata; an explicit override does not perform
citation lookup or rewrite that metadata.
Standalone `research-repo-tools changelog generate` runs the same complete workflow.

## Rebuilding retained history

When updated rendering rules affect archived notes, run `just changelog-rebuild`
from a checkout with full Git history (v0.3.2 or later). Review the diff for
the root changelog and each affected minor-series archive, including added and
removed files. `just changelog-rebuild --apply` regenerates from the inputs
present at invocation. If the history, tags, renderer, configuration, or retained
files change, preview again before applying. After applying, verify an empty diff.
Both commands accept `--tag TAG` and the same date options as generation.
Rebuild retains declared dates and requires every retained release to appear
exactly once in the result. Ordinary generation and release preparation continue
to check for conflicts with archived entries.

## Generation and normalization

The installed templates comprise a git-cliff configuration, a starter
changelog, small just wrappers, consumer TOML, and optional rumdl rules.
Generation uses the packaged git-cliff template unless the consumer explicitly
sets `changelog.cliff-config`. Owner and repository identify links; they do not
select a policy profile. Generation is offline and disables template commands.
A prospective tag requires an explicit ISO date or a configured citation-owned
date, keeping output independent of an accidental wall-clock date.
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
breaks. Namespace paths such as `Error::Variant` continue the description when
git-cliff misclassifies them as trailers; genuine trailers such as `Refs` remain
separate. Commits marked only with `!` use their subject as the fallback summary.
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

Set `changelog.dependency-bodies = "preserve"` to retain complete authored bodies
and their release-note, changelog and comparison links in all dependency scopes.
The default `"concise"` suppresses dependency bodies while retaining complete
breaking descriptions. This explicit choice also applies to grouped bot updates;
the package does not infer authorship or delete content by meaning. It changes
only body inclusion in the common template, retaining categories, formatting,
code spans/fences, dates, tags and links. Remove `cliff-config` before selecting
`"preserve"`; unsupported values and conflicting template overrides fail clearly.
After adopting the published package, remove the copied scope-specific
template condition, declare owner/repository, select the shared body policy,
and regenerate and review the history. Consumers retain configuration/wiring
checks; the shared suite owns generic generation regressions.

Normalization uses UTF-8 and LF, one final newline, consistent list markers,
160-column prose reflow, intact Markdown links/code spans, and level-four
entry headings beneath release categories. Each heading starts a new block.
Lists below a heading start at the left margin and retain their nested items.
Lists with multiple paragraphs in an item use blank lines between peers.
Breaking-change and pull-request summaries retain their links.
Fenced code is opaque to prose transformations;
backtick and tilde delimiters, delimiter lengths, and fence-contained examples
of releases are preserved. A missing code language becomes `text`.
Markdown pipe tables retain their rows and cells, including rows longer than
the prose width limit.
Feature names and consumer-specific wording are preserved rather than migrated.

The shared template includes the body of a squash commit even when its PR
title has no Conventional Commits prefix. The Merged Pull Requests summary
lists the title and link, and the categorized entry carries the authored details.
The shared generator links explicitly closed issues beside the entry's commit
hash in every release, including archived releases. It reads standalone
`Closes #123`, `Fixes owner/repo#456` and `Resolves` directives from local Git
history, including comma-separated targets and GitHub issue URLs. Each target
appears once per entry. Code examples, including multiline code spans, and ordinary mentions are ignored.
Custom `cliff-config` templates control their own issue rendering.

Embedded conventional titles such as `fix:` and `feat:` retain their original
prefix, wording and order under their authored parent entry. A title that occurs
once in a release category uses a level-four heading. If a title repeats in the
same category, each occurrence becomes a list item with its own details.
A horizontal rule separates the last detail subsection from the next commit in the same category.
When the document links to headings by fragment or local path and fragment, repeated titles keep their headings
and receive numbered suffixes so each occurrence remains reachable.
The same title in another release or category can keep its heading.
All dependency bumps, including CI and development tools, stay in the separate
Dependencies category. The selected body policy controls their detail.

`changelog.formatter` optionally names the consumer's rumdl configuration.
The packaged rumdl policy checks for duplicate sibling headings (MD024)
and unused reference definitions (MD053).
Generation formats the root and each archive, then checks their links together
before writing any files. Formatter failure or unexpectedly empty
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
archive introductions and index links. The packaged generator restores missing
squash details when a retained release matches its earlier title-only rendering
from the same Git history. Other content conflicts fail before publication.
When generation uses a formatter, retained and incoming archive documents pass through the same normalization and
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
notes. Pass the full reviewed commit SHA to `--commit` to require a clean
checkout at that commit. A successful `--dry-run` writes only the annotation
to stdout. For an [unpublished replacement](RECOVERING_RELEASES.md#4-replace-the-superseded-draft-and-tag),
pass the same `--commit` and `--force` arguments to the preview and the actual
tag recipe. The tag recipe replaces the local ref in one Git operation.

Regression tests live under `tests/changelog/`, including real Git and git-cliff
scenarios using minimal disposable repositories.
