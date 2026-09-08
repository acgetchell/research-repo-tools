# Shared changelog contract

The public commands always operate on root-level `CHANGELOG.md`. Completed
minor series go to `docs/archives/changelog/MAJOR.MINOR.md`. There is no
repository-specific implementation or filename setting.

The installed templates comprise a git-cliff configuration, a starter
changelog, small just wrappers, consumer TOML, and optional rumdl rules.
Generation uses the packaged git-cliff template unless the consumer explicitly
sets `changelog.cliff-config`. Owner and repository identify links; they do not
select a policy profile. Generation is offline and disables template commands.
A prospective tag requires an explicit ISO date, keeping output independent
of an accidental wall-clock date.

Normalization uses UTF-8 and LF, one final newline, consistent list markers,
160-column prose reflow, intact Markdown links/code spans, and level-four
entry headings beneath release categories. Breaking-change and pull-request
summaries retain their links. Fenced code is opaque to prose transformations;
backtick and tilde delimiters, delimiter lengths, and fence-contained examples
of releases are preserved. A missing code language becomes `text`.

This adopts level-four headings for entry-local titles instead of the bold
prose used by some sources. Shared tests cover text, duplicate-heading,
link, nesting, and idempotence assertions with the common heading syntax.
It avoids keeping separate formatter implementations for individual consumers.

`changelog.formatter` optionally names the consumer's rumdl configuration.
The candidate must pass both the fixing invocation and a final check before
replacement. Formatter failure or unexpectedly empty output preserves the
original. The base package does not require rumdl for pure normalization.

Archive parsing rejects malformed versions/dates, duplicate release headings,
out-of-order versions, and misplaced Unreleased sections. SemVer prerelease
and build labels and inline/reference release links are supported. Fenced
example headings do not create release blocks. Body reference definitions are
retained with their notes. Cross-volume paths that cannot form portable links
fail before publication rather than embedding developer-specific absolute paths.

Archive publication stages every candidate and backup before replacing any
file. Caught failures trigger rollback; an incomplete rollback preserves recovery
files and reports their locations and each failure. Multiple files are not
crash-atomic: process termination or power loss still requires inspection.

Incremental archiving merges previously retained patches and keeps existing
archive introductions and index links. Different content for an already
retained release fails before publication. Relative Markdown links are rebased
when notes move into the archive; fenced examples and inline code stay intact.

`changelog notes TAG` finds the release in the root file or its conventional
archive and emits only notes and their reference definitions. `changelog tag`
checks the package version and release date before any tag mutation. By default
the date must be today's UTC date; `release.date-policy = "declared"` explicitly
permits historical release dates. Oversized annotations link to the complete
notes. `--dry-run` previews notes. `--force` uses Git's ref replacement and never
deletes the old tag before creating its replacement. Tagging is local only.

Regression tests live under `tests/changelog/`, including real Git and git-cliff
scenarios using minimal disposable repositories.
