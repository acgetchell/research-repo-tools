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
Python project. Declared Python package versions follow that version. Existing
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
updates, and publication are separate operations. Arbitrary regex targets,
repository profiles, and special benchmark-command rewrites are not supported.
