# Release metadata and registry publication APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Reviewed registry publication API](#reviewed-registry-publication-api)
- [Python release API](#python-release-api)

## Reviewed registry publication API

`research_repo_tools.release_publishing` exposes immutable `PublishingSettings`,
`RequiredCheck`, and `ReviewedRelease` values, `parse_publishing`, `validate_event`,
`require_checks`, `check_metadata`, `check_reviewed_release`,
`publish_reviewed_release`, and `verify_publication`. Configuration declares one
registry (`pypi` or `crates-io`), package, GitHub repository, a nonempty list of
check name/provider IDs, and optional required asset filenames. Unknown fields,
invalid identities, duplicates, and missing checks raise `ValueError`.

`check_metadata(config, tag, previous_tag=None)` reuses final release metadata and
changelog checks. An explicit predecessor is validated and passed through to
release-policy checks; tagged CLI checks retain `--previous-release` semantics.
`validate_event(event, repository, commit, ref)` parses a stable published event;
`check_reviewed_release(config, tag, event=...)` binds it to the clean checkout and
remote tag, release lifecycle, protected-default-branch ancestry, complete latest
check evidence and configured assets. These operations are read-only.
`require_checks(pages, commit, checks)` consumes paginated/slurped REST check-runs
pages: one exact-SHA successful latest result is required for each name/app ID.
It rejects incomplete/changing pagination, duplicate IDs and ambiguous matches.
Provider IDs must be positive integers; booleans and floating-point IDs reject.
All unmet requirements are reported together as pending/running, missing,
unsuccessful or ambiguous, with observed status/conclusion, provider ID and
GitHub check URL when available. Pending checks advise waiting for the existing
run to succeed before manually retrying; the gate never accepts them or
automatically resumes publication. Invalid pagination or identity still rejects
before interpreting individual check outcomes.
Release events, GitHub evidence and registry responses use strict UTF-8 JSON
parsing that rejects duplicate fields, non-finite numbers and excessive nesting.

The [release evidence core](release-evidence-api.md) provides configurable signed
source validation, retained Cargo archive verification, approval handoffs and
shared provider/signature primitives. These guarantees remain distinct from
registry publication. The separate [verified CI reuse contract](REUSING_CI.md)
provides opt-in source-check equivalence for PR merge and squash release trees.
`PublishingSettings.reuse` is optional; `check_reviewed_release` and
`publish_reviewed_release` accept keyword-only `reuse_evidence=None`. Omitting it
retains exact-commit checks. `require_checks` always retains exact-SHA semantics.

`publish_reviewed_release(config, tag)` validates a stable draft twice and publishes
that GitHub Release as the invoking maintainer. Calling it constitutes approval;
the CLI additionally requires `--approve`. It never changes Git state or uploads
registry packages. Consumers retain native Cargo packaging/upload commands.
It captures the reviewed commit, revalidates the exact release ID and tag commit
before mutation, and validates the publication response through the shared
[release-target contract](workflow-api.md#release-assets). An invalid/unavailable
publication response raises `release_assets.ReleasePublicationUnknownError`;
inspect the captured ID before retrying because publication may have succeeded.
`verify_publication(config, tag, attempts=1, interval=10)` requires a published
stable GitHub Release, configured assets and the exact registry version.

`research_repo_tools.registry` exposes `RegistryVersion`, `RegistryLookupError`,
`lookup_version(registry, package, version)` and `wait_for_version(...)`.
`present=False` means only a proven exact-endpoint 404. Every unavailable,
malformed, yanked, mismatched or partial-upload response raises an error.
Verification asserts metadata visibility, not archive-byte provenance. Waits
retry only absence, with 1..31 requests, a 0..10 second interval and a
15-second timeout per request. PyPI requires both wheel and sdist upload evidence;
crates.io requires an archive checksum. Neither lookup retries uploads.

The canonical recipe surface is identical in root and consumer templates:
`release-check TAG`, `release-first TAG DATE`, `release-notes TAG`,
`release-publish TAG`, `release-tag TAG`, `release-tag-preview TAG`,
`release-update TAG PREVIOUS DATE`, and `release-verify TAG [ARGS...]`.
The low-level CLI also retains untagged `release check` for metadata inspection.

## Python release API

`research_repo_tools.releases` is a supported public interface. All roots are
`Path` objects. Policies default to the consumer configuration when omitted.
These functions do not print or parse CLI output.

| Import | Contract |
| --- | --- |
| `apply_release(plan) -> ReleaseResult` | Publish a validated, unchanged plan using the file-publication API |
| `check_release(root, *, policy=None, previous_tag=None, adapter=None) -> ReleaseCheckResult` | Check shared metadata and consumer rules without writing source files |
| `discover_release(root, *, policy=None, adapter=None) -> ReleaseDiscovery` | Read package identity, selected files, and standard version references; validate explicit selector cardinality without GitHub access |
| `plan_release(root, tag, *, previous_tag=None, release_date=None, policy=None, adapter=None, first_release=False, offline=False) -> ReleasePlan` | Prepare all shared, declarative, and adapter edits in an isolated tree and validate them before returning |
| `published_releases(root, *, repository=None) -> tuple[PublishedRelease, ...]` | Query `gh release list`, excluding drafts/prereleases, sorted by descending numeric stable version |

By default `plan_release` accepts `X.Y.Z` or `vX.Y.Z` and normalizes to `vX.Y.Z`, including
an explicit previous tag. `tag_policy="canonical-stable"` requires canonical
`vX.Y.Z` on input. Omit `previous_tag` to discover the greatest published
stable release below the target. Discovery rejects a target below any published
stable release and fails if no predecessor exists. Supplying `previous_tag`
bypasses remote discovery; it must precede the target. Dates default to UTC today.
A `previous-tag` rule also triggers discovery during `check_release` unless the
caller supplies the previous tag. `published_releases` examines up to 1,000
GitHub releases, matching the existing CLI discovery limit.

The module exports these frozen dataclasses:

- `ReleasePolicy(date_policy="today", final_changelog=False, required_files=(), exclude=(), rules=(), tag_policy="normalized-stable")`
  and `ReleaseRule(path, pattern, value=None, source=None, count=1, exclude=None)`
  follow the [declarative contract](UPDATING_RELEASE_METADATA.md#declarative-consumer-policies).
- `ReleaseAdapter(input_files=(), prepare=None, validate=None)` uses trusted Python
  callbacks, each receiving `(candidate: Path, context: ReleaseContext)`.
  `prepare` returns `Mapping[str, bytes]`; `validate` returns `Sequence[str]`.
  Select all callback inputs explicitly; callbacks cannot assume a full repository
  copy or a `.git` directory. They must not mutate the source or candidate.
- `ReleaseContext(tag, previous_tag, release_date)` also exposes `version` without
  the `v` prefix. During checks, `previous_tag` can be `None` when no rule or
  explicit argument requires it.
- `ReleaseDiscovery(root, package, files, references, policy)` exposes an absolute
  root, `PackageInfo(name, version)`, sorted relative file paths, and standard
  `VersionReference(path, line, version, kind, text)` records. Reference paths are
  absolute and lines are one-based; `kind` is string-valued. Workspace-only
  package names can be `None`. Custom selectors are available through `policy.rules`.
- `ReleaseCheckResult(discovery, problems)` exposes diagnostic strings and `ok`.
- `ReleaseEdit(path, before, after)` stores a relative path and exact bytes.
  `ReleasePlan(discovery, context, files, adapter_inputs)` contains every selected
  file, including unchanged validation inputs. Its `edits` property filters changed
  files. Pass plans returned by `plan_release` unchanged to `apply_release`.
- `ReleaseResult(context, changed_paths)` reports relative paths in publication order.
- `PublishedRelease(tag, published_at)` contains a normalized tag and a timezone-aware
  `datetime` from GitHub.

Malformed configuration, selectors, metadata, dates, or paths raise `ValueError`.
I/O and UTF-8 decoding errors propagate. Synchronization mismatches appear in
`ReleaseCheckResult.problems`; candidate mismatches raise
`ReleaseValidationError(ValueError)` with a `problems` tuple. A stale plan raises
`StaleReleasePlanError(ValueError)`. Adapter exceptions propagate before publication;
adapter diagnostics become validation failures. Remote discovery follows the
process API's executable, command, and timeout errors. Publication uses the
[`OSError`/`ExceptionGroup`/`RecoveryError` contracts](files-api.md#python-file-publication-api). Diagnostic strings are
for humans, not machine parsing.

Planning writes only temporary files. Application rejects changed selected inputs,
then publishes the exact planned bytes. This is not a concurrent-writer lock or a
crash-atomic multi-file commit; callers must serialize writers. A plan is a
short-lived in-process value, not a persisted authorization token. See the
[worked consumer migration](release-policy-migration.md).

### First releases

First-release intent is explicit. `plan_release(root, "v0.1.0", first_release=True,
release_date="2026-09-25")` requires a successfully fetched empty stable GitHub
release history. Drafts and prereleases do not count; network, authentication,
and malformed-response failures propagate. For a reviewed offline declaration,
also pass `offline=True`. No predecessor or sentinel tag is invented, and
`context.previous_tag` is `None`. Policies with `previous-tag` selectors are
incompatible and fail before publication.

An Unreleased-only changelog is valid during this preparation. Generate the
target release notes afterwards; `final_changelog=True` and final-release checks
still require the actual target heading. Previewing remains non-mutating and
`apply_release(plan)` retains the normal stale-input and rollback guarantees.

The CLI equivalents are `research-repo-tools release update v0.1.0
--first-release --date 2026-09-25 --dry-run` and the same command without
`--dry-run` to apply. Add `--offline` only when empty history has been reviewed.
The template's `just release-first TAG DATE` uses online discovery. Existing
`just release-update TAG PREVIOUS DATE` continues to use an explicit predecessor.

## Preparing a structured release

The release API replaces wrapper-owned discovery, staging, CLI-output parsing
and rollback:

```python
from pathlib import Path

from research_repo_tools.releases import apply_release, plan_release

plan = plan_release(
    Path.cwd(), "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-20",
)
for edit in plan.edits:
    print(edit.path)  # Inspect edit.before and edit.after for the exact byte diff.
# Omit apply_release for a fully validated preview.
result = apply_release(plan)
```

Declare required files, fixed metadata, and selected active references in
[release configuration](UPDATING_RELEASE_METADATA.md). Add a small Python adapter when validation
needs consumer-owned evidence rules. See the
[worked MCMC-style migration](release-policy-migration.md)
and [typed API contract](api.md). Historical evidence stays excluded from updates.
