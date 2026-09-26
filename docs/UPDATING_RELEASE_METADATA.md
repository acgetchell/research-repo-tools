# Updating release metadata

These commands maintain consumer metadata. For publishing this tooling package
itself, follow [Releasing research-repo-tools](RELEASING.md).

Normal release commands need no separate configuration file:

```sh
research-repo-tools release check
research-repo-tools release update 1.2.3 --previous-release v1.2.2 --dry-run
research-repo-tools release update 1.2.3 --previous-release v1.2.2 --date 2026-09-07
research-repo-tools release check --final-release
```

The owning version comes from a root Cargo package, Cargo workspace package, or
Python project. Declared Python package versions follow that version. A Python
dependency environment beside Cargo with `[tool.uv] package=false` is excluded
from version synchronization because it is not another releasable package. Existing
local Cargo and uv lock entries are synchronized; a lockfile is not required.
Cargo workspace members inheriting the workspace version participate, while
registry dependencies and independently versioned members retain their versions.
Python lock entries are selected by normalized distribution name and editable or
virtual source. Name matching ignores case and treats runs of hyphens, underscores,
and periods alike. Multiple matching local entries fail validation.

Existing CITATION version and date fields are synchronized. The citation's
top-level DOI is the source of truth for existing README DOI badges and
REFERENCES DOI entries. Dates default to today's UTC date; `--date` accepts an
explicit ISO date. Active Cargo dependency snippets and `cargo add` examples
track the owning package. Archived documentation under `docs/archives/` is excluded.
Other links and custom commands retain their original contents.

DOI references are optional. When present, the supported forms are a README
`[![DOI](badge-image-url)](https://doi.org/...)` badge and a REFERENCES entry
`- DOI: <https://doi.org/...>`. Each file may contain at most one such reference;
malformed or duplicate references fail validation. Other bibliography entries
are left alone and do not create a requirement to add a project DOI.

Without `--previous-release`, GitHub CLI discovers the previous published stable
release, excluding drafts and prereleases. An explicit prior release allows
offline preparation. Release metadata updates require canonical stable SemVer;
changelog parsing and tags also support prereleases and build metadata.

Preparation may precede the new changelog section. Existing current-version
headings have their dates synchronized; old release notes remain intact.
`--final-release` additionally requires the generated current heading.

Every candidate is validated in a temporary tree before replacement. `--dry-run`
performs the same validation and reports the same changed paths. Failed writes
trigger rollback; incomplete rollback reports retained recovery files. Multi-file
replacement is not crash-atomic.

These commands prepare local files. Changelog generation, tagging, dependency
updates, and publication are separate operations. Repository profiles and command hooks are not supported. Explicit selectors and
Python adapters extend preparation as described below.


## First-release preparation

The first-release mode added for v0.1.7 prepares metadata without inventing a
predecessor. After adopting that release and its consumer template, run
`just release-first v0.1.0 YYYY-MM-DD` with the intended target and UTC date.
The command checks GitHub's published stable history and refuses first-release
preparation if any stable release exists. Authentication or lookup failures also
fail; they do not establish empty history.

The underlying `release update --first-release` command supports `--dry-run` for
preview. Add `--offline` only when empty history has been independently reviewed;
it accepts that explicit intent and performs no discovery. `--previous-release`
cannot accompany `--first-release`. For ordinary releases, `--offline` requires
an explicit predecessor, which already avoids discovery.
Policies using `source = "previous-tag"` are invalid without a predecessor.

An Unreleased-only changelog is accepted during this preparation step. Generate
the dated first release afterward, then run final-release validation: published
releases still require complete generated notes. Existing release preparation
with a real predecessor remains unchanged.

## Declarative consumer policies

These additions require a published package newer than `0.1.2` containing the
release-plan API. Existing defaults remain unchanged when no rules are declared.
Put the following under `[tool.research-repo-tools.release]` in `pyproject.toml`,
or use `[release]` in a standalone configuration:

```toml
[tool.research-repo-tools.release]
tag-policy = "canonical-stable"
required-files = ["CITATION.cff", "README.md", "REFERENCES.md"]
exclude = ["docs/evidence/**"]

[[tool.research-repo-tools.release.rules]]
path = "CITATION.cff"
pattern = '^doi: (?P<value>[^\s]+)[ \t]*\r?$'
value = "10.5281/zenodo.20033111"
```

Use TOML multiline literal strings when a pattern contains single quotes; the
[worked migration](release-policy-migration.md) shows complete selectors.
`tag-policy` defaults to `normalized-stable`, accepting `X.Y.Z` or `vX.Y.Z`.
`canonical-stable` requires the target argument to use canonical `vX.Y.Z` syntax.
`required-files` lists existing regular files. Paths are normalized relative POSIX
names; absolute paths, `..`, `.git`, and symlink components fail. `exclude` uses
root-relative POSIX glob patterns, including `**`, to remove historical Markdown
from automatic dependency-snippet updates. Built-in archive/test exclusions still
apply. Exclusions do not disable structured metadata or DOI checks. An explicit
rule cannot select an excluded path, and adapters cannot edit excluded files.

Each rule selects one file and exactly `count` matches (default `1`). `pattern`
is a Python regular expression evaluated with MULTILINE, with a named nonempty
`(?P<value>...)` capture. Only that capture is replaced; surrounding bytes and
line endings survive. Patterns must accommodate CRLF explicitly when using `$`.
`exclude`, when set on a rule, is a regex applied to the whole match before
counting, for example to leave measured artifact URLs unchanged. Missing,
ambiguous, empty, and overlapping mutable captures fail both checking and planning.
Declare the expected count instead of silently accepting newly added references.

A rule declares exactly one of:

| Field | Meaning |
| --- | --- |
| `source = "previous-tag"` | Previous stable tag; checked against the explicit or discovered previous release |
| `source = "release-date"` | Prepared ISO date; checks use the citation date, current changelog date, or today's UTC date in that order |
| `source = "tag"` | Target stable tag, such as `v1.2.4` |
| `source = "version"` | Target stable version, such as `1.2.4` |
| `value = "..."` | Fixed assertion; never repaired as part of an update |

Version and stable-tag captures must contain the previous or target release
before preparation. A tag selector may also explicitly match a symbolic revision
or commit hash; that selected value is promoted to the target tag. A previous-tag
capture must already be a canonical stable tag older than the target; date
captures must already be ISO dates. Other malformed input is rejected rather than
normalized incidentally. All fixed assertions run before adapters or publication.

Checks remain offline unless a `previous-tag` rule needs discovery and no explicit
`--previous-release vX.Y.Z` is supplied. Preparation uses the same previous-release
discovery as before. The `date-policy` setting continues to govern changelog
publication; it does not replace `--date` for release preparation.

## Structured plans and adapters

The [release API](api.md#python-release-api) exposes discovery, checking, planning,
and application without CLI-output parsing. A plan contains exact before/after
bytes for the entire selected file set. Preview creates and validates the plan;
apply publishes its changed bytes in one shared transaction. There is no second
edit calculation between preview and apply. A changed source file, missing input,
or changed discovery inventory rejects an old plan before publication.

`ReleaseAdapter` declares any extra existing `input_files`. Its optional `prepare`
callback reads the isolated candidate after shared and declarative edits and
returns relative paths mapped to replacement bytes. Its optional `validate`
callback reads the complete candidate and returns diagnostic strings. Any
message rejects it. Both callbacks must leave their input tree unchanged; shared
validation runs after contributed edits. All edit targets must already belong
to the selected input tree. File creation/deletion and arbitrary shell hooks are
outside this adapter contract.

`check_release` runs the same discovery, rules, and adapter validation on an
isolated snapshot, without calling `prepare`. Preparation permits the previous
changelog heading until generation; final-release validation requires the target
heading. Scientific evidence selection and interpretation remain consumer code.
