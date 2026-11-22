# Release metadata and registry publication APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Reviewed registry publication API](#reviewed-registry-publication-api)
- [Cargo publication policy](#cargo-publication-policy)
- [Python release API](#python-release-api)

## Reviewed registry publication API

`research_repo_tools.release_publishing` exposes immutable `PublishingSettings`,
`RequiredCheck`, and `ReviewedRelease` values, `parse_publishing`, `validate_event`,
`require_checks`, `check_metadata`, `check_release_source`, `check_reviewed_release`,
`publish_reviewed_release`, and `verify_publication`. Configuration declares one
registry (`pypi` or `crates-io`), package, GitHub repository, a nonempty list of
check name/provider IDs, and optional required asset filenames. Unknown fields,
invalid identities, duplicates, and missing checks raise `ValueError`.

Optional `expected-title="tag"`, `generated-notes=true`, and
`require-immutable=true` map to `expected_title`, `generated_notes`, and
`require_immutable` on `PublishingSettings`. Defaults preserve existing policy.
The same assertions run at the draft gate, immediately before publication,
on the publication response, during published-event checking and final registry
verification. Notes comparison normalizes CRLF and removes at most one terminal
LF on each side; meaningful whitespace remains significant. Missing or malformed
asserted fields reject. Drafts must be mutable; only published releases require
immutability when configured. These are assertions, never permission to rewrite.

`check_release_source(config, tag, *, commit=None, reuse_evidence=None)` provides
the source/metadata/protected-default-branch/check gate without looking up a
release, returning the observed full commit. The authenticated draft planner
uses it before any draft exists.

`check_reviewed_release`, `publish_reviewed_release` and `verify_publication`
accept the original `ReleaseTarget` as optional `target` to
bind the configured repository/tag and remote commit to its captured release ID.
The event gate also requires its event ID to match this target. Final verification
checks assertions before and after registry verification and rejects changed
release IDs. See the [consumer release procedure](../src/research_repo_tools/templates/RELEASING.md) for CLI usage.
Omitting the target preserves existing callers and binds the ID observed at entry
for that verification operation.

For MCMC adoption, declare these publishing settings and remove the redundant
`_release-notes-check` and separate immutability query after installed integration
passes. Keep consumer registry credentials, approval and workflow policy.

`check_metadata(config, tag, *, previous_tag=None)` reuses final release metadata and
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
The command checks dependencies on entry and refreshes them before the
publication request. The intermediate draft, tag, and CI recheck shares this
operation's entry proof.
It captures the reviewed commit, revalidates the exact release ID and tag commit
before mutation, and validates the publication response through the shared
[release-target contract](workflow-api.md#release-assets). An invalid/unavailable
publication response raises `release_assets.ReleasePublicationUnknownError`;
inspect the captured ID before retrying because publication may have succeeded.
`verify_publication(config, tag, *, attempts=1, interval=10, target=None,
require_crate_asset=False)`
rechecks upstream dependencies and requires a published stable GitHub Release,
configured assets, and the exact registry version.

For the packaged Cargo workflow, attach the exact `.crate` retained by validation
to the draft as `PACKAGE-VERSION.crate`. Enable immutable releases and require
that attachment in the publishing policy. `release gate --require-crate-asset`
requires the completed attachment and its GitHub-recorded SHA-256 before upload.
`release verify --require-crate-asset` checks that same digest against the
crates.io checksum for the configured package and exact version. A mismatch
fails verification. Both flags require an immutable GitHub Release and a
`crates-io` publication policy.

The Cargo workflow runs the dependency gate again after deployment approval and
before requesting its token. After upload it uses `release verify --publication-only`
with the checksum flag to verify the configured GitHub assertions and exact
registry version without another dependency pass. Use plain `release verify`
when the current upstream dependency proof is also needed.

`verify_publication_visibility(root, repository, tag, *, registry, package,
required_assets=(), attempts=1, interval=10, target=None)` is a v0.3.0 API.
It checks an immutable stable release, nonempty uploaded assets, and the exact registry version with
its checksum for crates.io. It returns `RegistryVersion` and uses the same
explicit inputs as `release visibility`. `root` supplies a working directory
for authenticated `gh` requests and resolves CLI `--target` paths. Visibility
checks run without checkout, generated notes, or dependency provenance.

`research_repo_tools.registry` exposes `RegistryVersion`, `RegistryLookupError`, `TransientRegistryError`,
`lookup_version(registry, package, version)` and `wait_for_version(...)`.
`present=False` means only a proven exact-endpoint 404. Every unavailable,
malformed, yanked, mismatched or partial-upload response raises an error.
Waits poll only absence, with 1 through 31 observations and an interval of 0
through 10 seconds. For an exact Cargo artifact check, compare the returned
registry checksum with the independently retained `.crate` digest. For PyPI,
`verify_published_distributions(package, version, files, *, index_url="https://pypi.org")`
takes the approved `{filename: sha256}` mapping. It requires the matching
non-yanked wheel and sdist inventory, downloads both, and compares their bytes
with those hashes. It returns the verified mapping. Supply the expected hashes
from independently trusted validation evidence.

Public registry metadata and artifact GETs make up to three attempts per
request. `RRT_REGISTRY_TIMEOUT_SECONDS` sets the total per-request budget in
whole seconds, including pacing and retries. It defaults to `60` and accepts
`1` through `900`. Connection/response-header and stalled-body waits each keep
a 30-second limit. The deadline also bounds socket reads within HTTP headers
and chunk framing, as well as time waiting for system DNS.
The isolated release runtime inherits the setting. For
example, run `RRT_REGISTRY_TIMEOUT_SECONDS=180 just release-verify "$TAG"` to
retry a delayed verification of the same candidate.
`TransientRegistryError` identifies exhausted transient retries, and
`RegistryVersion.checksum` exposes the crates.io archive
checksum for a present version. Release dependency verification recommends
retrying the read-only check with the same pins and retaining the original CI
source and attempt assertions. PyPI requires both wheel and sdist upload evidence.
Crates.io requires a valid archive checksum.

The root and consumer Justfiles share these release recipes.
`release-check TAG`, `release-first TAG DATE`, `release-notes TAG`,
`release-publish TAG [ARGS...]`, `release-registry TAG --expect absent|present`,
`release-reuse COMMIT ARGS...`,
`release-verify TAG [ARGS...]`, `release-version-check`, `tag TAG [--commit SHA] [--force]`,
`tag-preview TAG [--commit SHA] [--force]`, and `update-version TAG [ARGS...]`.
`release-verify` defaults to seven attempts at ten-second intervals; optional
flags override those defaults. `release-version-check` validates the current
prepared version without a tag argument. `tag TAG [--commit SHA]` and
`tag-preview TAG [--commit SHA]` perform the final metadata and dependency
checks themselves. Tag creation reports the commit and annotation.
The low-level CLI also retains untagged `release check` for metadata inspection.

## Cargo publication policy

The shared tooling supports **native Cargo publication**. It does not provide a
custom exact-archive registry uploader. The evidence core
supports retained-archive verification and reproduction without claiming that
native publication uploads those retained bytes.

The supported [Cargo publish interface](https://doc.rust-lang.org/cargo/commands/cargo-publish.html)
creates an archive from the checkout before uploading. Its `--no-verify` option
skips build verification; it does not accept an existing archive or suppress
packaging. A checked reproduction can establish equal bytes before approval or
token acquisition, but Cargo still packages again during actual publication.

The [registry web API](https://doc.rust-lang.org/cargo/reference/registry-web-api.html#publish)
accepts separate publication metadata and archive bytes, so an exact-byte
uploader is technically feasible. Maintaining it would add responsibility for
metadata/archive agreement, Cargo's publication restrictions, renamed and
target-specific dependencies, features and registry mappings, request framing,
short-lived credentials, limits, errors, uncertain upload outcomes and registry
checksum verification. Archive metadata inspection alone does not preserve all
of Cargo's publication checks.

The [packaged Cargo workflow](../src/research_repo_tools/templates/publish-crates.yml)
uses the validated `.crate` attached to the immutable GitHub Release as its
checksum reference. Attach the archive to the draft before publishing on GitHub.
The workflow checks that attachment before requesting a token and fails
verification if crates.io reports a different SHA-256 after upload. GitHub
publication and registry deployment approval remain separate steps.

The bounded alternative review included the upstream command and registry
interfaces, [cargo-release's publication configuration](https://github.com/crate-ci/cargo-release/blob/main/docs/reference.md),
and [release-plz's release operation](https://release-plz.dev/docs/usage/release).
Both orchestration tools document using Cargo publication. This review did not
establish a supported existing-archive path with equivalent metadata and
publication guarantees; it does not claim an exhaustive survey of all uploaders.

| Option | Benefit | Maintenance and guarantee boundary |
| --- | --- | --- |
| Native Cargo with signed source validation | Retains Cargo checks and established publishing workflow | Signed source validation does not attest archive bytes |
| Native Cargo with retained-archive checks and reproduction | Verifies original CI archive identity and equal local reproduction before credentials | Final native upload still repackages |
| Separate exact-archive uploader | Could keep consumer packaging outside the credential-bearing step and upload the retained bytes | Requires a separately reviewed, supported metadata/upload contract and checksum recovery |

Packaging, download and verification costs are **unmeasured**. There is no
performance claim or assumed savings. The potential provenance benefit does not
justify taking ownership of an additional publishing client. Consumer
native/scientific/feature checks, approval, registry absence,
tag/version/commit checks and post-upload visibility remain required.

A future proposal may reopen the uploader decision with a maintained supported
implementation or a bounded implementation design. It must preserve Cargo's
checks, bind exact archive and publication-metadata digests through validation
and approval, upload with short-lived credentials without executing consumer
code, and confirm the registry checksum. On an upload timeout, inspect registry
state and checksum before deciding recovery; never retry blindly. A speed claim
would additionally require representative packaging/download/verification
measurements. Consumers must retain the supported native publication path until
any replacement is implemented, published, and integration-tested.

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

- `ReleasePolicy(date_policy="today", final_changelog=False, required_files=(),
  exclude=(), rules=(), tag_policy="normalized-stable", published_dependencies=())`
  and `ReleaseRule(path, pattern, replacement, count=1, exclude=None)`
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

For v0.2.7 Python callers, replace `ReleaseRule(..., value=text)` with
`ReleaseRule(..., replacement=FixedValue(text))`, and `source="tag"` with
`replacement=ReleaseSource.TAG`. `FixedValue`, `ReleaseSource`, and `ReleaseRule`
are available from `research_repo_tools.releases`. Other source cases are
`VERSION`, `PREVIOUS_TAG`, and `RELEASE_DATE`. The rule carries exactly one
literal assertion or derived source; absent/contradictory payloads are no longer
constructor options. TOML retains its `value`/`source` spelling and parses that
choice once at the boundary. Consumers handle the resulting union exhaustively.

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

For the online recipe, offline preview and final changelog check, see
[first-release preparation](UPDATING_RELEASE_METADATA.md#first-release-preparation).

## Preparing a structured release

The release API replaces wrapper-owned discovery, staging, CLI-output parsing
and rollback:

```python
from pathlib import Path

from research_repo_tools.releases import apply_release, plan_release

plan = plan_release(
    Path.cwd(),
    "v1.2.4",
    previous_tag="v1.2.3",
    release_date="2026-09-20",
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
