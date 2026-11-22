# Upstream tooling provenance

Introduced in v0.2.7, this capability separates reproducible development sources
from published upstream tooling required for a consumer release. Select only
tooling dependencies; scientific dependencies and native release gates remain
consumer policy. See the [usage and transition example](UPDATING_DEPENDENCIES.md#development-pins-and-release-dependencies).

## Configuration and proof

`release.published-dependencies` is an array of unique Python package names,
normalized using Python packaging name rules. Its default is empty. In the
consumer manifest it lives under `[tool.research-repo-tools.release]`.
An explicit CLI configuration may add selections; it cannot disable selections
in the observed checkout. Selecting the project being released is an error.
This package's own source validation therefore needs no self-publication.

Every selected package requires consistent, unmarked exact `==version`
requirements in project dependencies, extras or dependency groups. Competing
uv constraints/overrides, conditional or multiple resolutions, direct requirements,
and all `tool.uv.sources` overrides are rejected at release boundaries. The
root's `uv.lock` must contain exactly one selected package from
`https://pypi.org/simple`, with hashed wheel and sdist artifacts. Separate
`uv.toml` configuration is unsupported. An offline lock check verifies freshness
without resolution, sync, downloads, or lock writes.

Release proof then checks PyPI's exact-version JSON inventory and the locked
artifact URLs, SHA-256 hashes, sizes and non-yanked status. For each executing
or normal installation it downloads the unique locked wheel matching the
installed WHEEL tags, verifies its size/hash, and compares each payload member
with the installed bytes. Missing or ambiguous distribution metadata fails.
Any `direct_url.json` rejects a release installation, including a direct wheel
whose version or bytes match PyPI. Absence of that file alone is insufficient.

The current process is always checked. The normal `UV_PROJECT_ENVIRONMENT`
(relative to the root), or `.venv` by default, when present, is also
checked. When research-repo-tools is selected, the executing provenance checker
must belong to the observed installed distribution; a source import override
cannot borrow a matching published distribution's metadata. The normal environment is
observed by reading its native site-packages metadata and bytes. Its interpreter,
startup hooks and package code are never executed. Environments with inherited
system site packages or ambiguous site-packages layouts are unsupported. This
catches differing normal and `uvx` installations with identical version labels.
Run isolated publication jobs with their actual selected runtime. The validator
does not parse shell recipes to infer unexecuted `uvx` pins. Manifest, lock and
runtime observations are checked again after proof acquisition.

Lock freshness uses the trusted current interpreter and an external uv executable,
with the observed root selected explicitly. Consumer build hooks are disabled;
inherited project-directory selectors cannot redirect the check. Separate uv
configuration overrides fail closed.

The published artifact proof uses PyPI and `files.pythonhosted.org`, with
32 MiB compressed/uncompressed wheel bounds for individually selected tooling
and 512 MiB compressed/uncompressed bounds per package for the complete native
development environment, which may include large engines such as Semgrep.
Retained source-built wheels are bounded at 256 MiB. All checks retain a
10,000 member/file limit.
Custom indexes, missing wheel artifacts, or ambiguous installed wheel tags fail
closed. Package directories are inventoried for added code, including files
omitted from RECORD. In complete-environment checks, shared namespace files are
verified against their owning distributions; unrecorded files still fail.
Windows RECORD entries may use forward or backward slashes. Package directory
symlinks, Windows junctions, and unreadable subdirectories prevent verification.

`prepare_dependency_builds(root)` rebuilds explicitly selected sdists using
hash-locked build-tool wheels and issues authenticated local receipts. It then
installs and verifies the resulting wheels. `verify_build_receipt` binds retained
wheel bytes to the live manifest, lock, policy, builder, interpreter and platform.
It rejects unauthenticated receipt content and does not import installed dependencies.
Complete-environment checks support `purelib`, `platlib`, environment-relative
`data` and byte-preserving native script relocation for both published and
authenticated source-built wheels. Relocation applies only to the wheel's own
top-level `.data` directory. Resources such as `assets/model.data` are checked
against their installed bytes at their original paths. Other relocation schemes fail closed. See the
[source-build trust boundary](UPDATING_DEPENDENCIES.md#source-built-python-dependencies).
Installer-generated RECORD,
bytecode and entry-point wrappers are not publication evidence; package payloads,
METADATA and WHEEL bytes are compared. Matching normal and isolated installations
reuse the downloaded wheel within one check; later checks reacquire proof.
These checks establish dependency
provenance under the trusted runtime/filesystem boundary, not upstream scientific
correctness or a new signature guarantee. They do not replace protected jobs,
approval, signing or artifact validation.

## Development identity

Ordinary Python baseline/tool inheritance accepts one HTTPS Git source with
`git` and `rev` keys. The revision must be a full lowercase 40-character commit,
and the dependency-group requirement retains its exact version and extras.
The URL, requested revision and resolved commit in `uv.lock` and installed
PEP 610 `direct_url.json` must agree, including the normal `.venv` when present.
Branches, tags, abbreviated revisions, path/workspace sources and conditional
source lists remain unsupported for inherited tooling. These checks are offline
and do not label a Git build as published. Requires-Python and inherited
Ruff/ty/pytest constraints retain their existing checks. Registry-only development
retains its existing baseline/tool checks; full published-byte proof runs only
at release boundaries.

The origin format follows [PEP 610](https://packaging.python.org/en/latest/specifications/direct-url-data-structure/);
source declarations follow [uv's dependency sources](https://docs.astral.sh/uv/concepts/projects/dependencies/#dependency-sources).

## Python API and boundaries

`research_repo_tools.dependency_provenance` exports:

- `DependencyIdentity(name, version, source, revision=None)`, an immutable result.
- `check_development_dependency(root, name="research-repo-tools", *, document=None)`:
  returns the verified Git identity, or `None` for registry-only development.
- `check_release_dependencies(root, *, packages=None)`: returns a tuple of verified
  published identities. Explicit package selections add to the manifest policy.
  An empty selected-package policy still enforces the canonical
  [dependency-source policy](#dependency-sources); without that policy,
  only dependency-free checkouts pass. Failures raise `ValueError` with recovery
  guidance; no dependency mutation is performed.

The standalone `research_repo_tools.tooling_runtime` wrapper exports immutable
`ReleasePin(version)` and `GitHubPin(repository, commit)` selector types,
`parse_source(*, release=None, github=None, commit=None)`, and
`run_tooling(source, arguments, *, root, release_intent=False, extras=())`.
Parsing rejects competing/incomplete forms, noncanonical versions, repository URLs,
floating refs and abbreviated SHAs. A selector is not availability evidence:
`run_tooling` checks the exact PyPI release and a non-yanked artifact, or the exact
GitHub commit plus its root package metadata, before launching isolated uv.
Missing, malformed or unavailable metadata fails without launching tooling.
Arguments and extras are tuples; the subprocess receives literal argument strings.
The return value is the child exit status. Failures raise `ValueError`, invalid
source types raise `TypeError`, and process launch/timeouts propagate.

GitHub selection supports public repositories and requires Python 3.11+ for TOML.
Lookup authentication selects `RRT_GITHUB_LOOKUP_TOKEN`, then `GITHUB_TOKEN`,
then `GH_TOKEN`, taking the first nonempty value. Lookup requests authenticate
only `api.github.com`; credentials are never sent to PyPI or raw content, and
redirects remain disabled. The composite Action supplies its token through
`RRT_GITHUB_LOOKUP_TOKEN`, which is removed before every tooling launch,
including legacy offline adoption. Caller-supplied `GITHUB_TOKEN` and `GH_TOKEN`
remain available to tooling commands requiring authentication; the Action does
not grant those credentials automatically.
Malformed lookup credentials fail before HTTP transport without printing their
values; accepted credentials contain visible ASCII without whitespace.
The Just and Actions wrappers select managed Python 3.15 for bootstrap. The target
package's metadata selects its own runtime Python. Release intent rejects Git
before lookup. A verified release cannot be redirected by ambient uv source/index
configuration. This existence check complements the subsequent checkout, lock and
installed-byte release gate; it does not replace it or assert scientific validity.
The action `.github/actions/run-tooling` and exported `python-bootstrap.py` use
this single module. See [wrapper usage](UPDATING_DEPENDENCIES.md#development-pins-and-release-dependencies).

The CLI `release dependencies` exposes the release check independently.
Configured checks run at pre-tag `release evidence` creation/verification,
candidate acceptance and approval revalidation, asset preflight/revalidation/upload,
and reviewed release gate/publication/verification. Checkout-taking APIs inspect
the observed checkout, even when the controller runtime lives elsewhere.
Asset and `verify_candidate_release` APIs accept `dependencies=()` to carry
additional explicit selections through every revalidation, including the final
upload/publication checks.

`check_release_dependencies(..., progress=DependencyProgress(mode="json"))`
accepts the reporter from `research_repo_tools.dependency_progress`; an observer
callback can receive the same versioned event dictionaries. CLI dependency
checks and asset batches default to human progress on stderr; candidate checks
default to `none`. All three accept `--progress human|json|none`. JSON events use
`research-repo-tools/dependency-progress/v1` and include phase, graph, checked,
total (null when unknown), elapsed seconds, request and retry counts, and a
bounded diagnostic. They contain no provider URLs, exception payloads or ETA.
Progress is advisory; only a successful verification establishes provenance.
Observers receive independent dictionaries; changing one cannot rewrite the
serialized event. Observer or stream errors abort the operation and restore the
prior reporter. If verification is already failing, its original error takes
precedence over a failure to report it.

One dependency-policy pass shares verified metadata for an exact source identity
across configured graphs and their installed-wheel checks. Every graph compares
its own lock checksums and artifacts. Installed wheels are downloaded and compared
with local files. A later pass fetches fresh metadata, including current yank status.
Local policy, manifest, lock and environment layout checks precede remote graph
traversal where their inputs are available. Cargo checks reuse a compressed
archive from the same registry cache when its SHA-256 and size match the lock
and fresh registry metadata. They download the archive if the local copy is
unavailable or fails verification, and compare every extracted input with the
authenticated bytes. Cargo cache errors identify the affected crate and path.
Unexpected installed files
require repair of the named environment from its declared lock. Preserve the
original benchmark evidence and release handoffs while repairing dependencies.

Public PyPI/crates.io metadata and dependency-artifact GETs allow at most three
attempts for connection/read timeouts, connection failures and HTTP 408, 429,
500, 502, 503 or 504. Each attempt preserves the original HTTPS URL, redirect
rejection and byte bounds. `RRT_REGISTRY_TIMEOUT_SECONDS` sets the per-GET
retry window in whole seconds from 1 to 900 (default 60). Connection and
stalled-read timeout limits are 30 seconds. The caller stops waiting for DNS
when the budget expires and discards any result that arrives later.
Waiting for the shared crates.io pacing lock consumes the same retry budget.
Backoff is one then two seconds. A valid `Retry-After` can extend a delay to at
most ten seconds; larger or malformed values stop verification for a later
operator retry. Every crates.io API attempt retains process-wide one-second
pacing. Parsing, authentication, source/identity/hash errors and mutations are
never retried. Exhaustion is unavailable evidence, never absence or success.

The composed candidate retention API runs fresh checks before native verification
and immediately before retaining outputs. Standalone candidate verification keeps
entry and return checks. Asset staging owns a separate synchronous dependency
observation: fresh at entry, refreshed at the next observation after five minutes
since the last pass completed, and always fresh before final transfer retention.
Between passes it rechecks original local inputs, environment and installed bytes
while live target/asset observations continue. Neither API exposes a caller
verification override or shares observations across operations. Approval and
publication still obtain their own fresh checks.

One `release evidence` invocation reuses its entry dependency observation while
checking the signed evidence and writing the handoff. It rechecks local inputs
and refreshes registry proof after five minutes. A separate invocation after
approval starts with fresh registry proof, including current yank status.

Low-level rootless evidence functions still verify their explicit provider and
artifact inputs; callers composing those primitives must call the dependency gate
with the observed root before effects.

`performance prepare`, `performance measure`, and `performance release` accept
`--release-intent` to expose pin transitions before source fetches, campaign reads,
measurement or output writes. The `prepare_performance` and `measure_release`
Python APIs accept `release_intent=True` and `dependencies=()` for selections
loaded from standalone controller configuration. Ordinary acquisition defaults
remain available with exact development pins. The flag does not turn rehearsal evidence
into release evidence or waive later source/freshness checks.

Changing a Git pin to a published pin changes manifest/lock and often harness
inputs. Preserve old rehearsal results and revalidate the final configuration;
changed measured inputs require a fresh acquisition and checkpoint. Never relabel
an old candidate or modify its independent assertions to fit the final pin.

## Dependency sources

`research_repo_tools.dependency_sources` owns `RegistryRelease`, `GitCommit`,
`parse_source` and `verify_source`. Each immutable selector validates its own
syntax; `verify_source` alone creates `VerifiedSource` availability evidence.
Python versions follow public PEP 440 rules; Cargo versions follow SemVer.
Supported release registries are exclusively PyPI and crates.io. Development
repositories are GitHub repositories pinned to full commit SHAs. A provider's
`read(url) -> bytes` supplies the response bytes for each lookup. Passing a
provider selects that transport even when it represents an empty cache. Omitting
it or passing `None` selects HTTP. Registry and artifact verification also
apply to injected responses.

```python
from research_repo_tools.dependency_sources import GitCommit, verify_source

source = GitCommit("cargo", "upstream-crate", "owner/repository", "a" * 40)
verified = verify_source(source)
# verify_source(source, release=True) rejects before any lookup.
```

`research_repo_tools.dependency_workflows` provides the consumer workflow:

```python
from pathlib import Path

from research_repo_tools.dependency_workflows import (
    apply_source,
    check_dependency_policy,
    plan_source,
)

root = Path("/path/to/consumer")
plan = plan_source(root, "upstream")  # verifies and resolves in a temporary copy
print(plan.diff())
apply_source(plan)  # reverifies; recoverable multi-file publication
# After transitioning to a published source and rerunning downstream checks:
check_dependency_policy(root)  # read-only, all configured projects
```

`plan_source` may populate native resolution caches but leaves consumer inputs,
environments and retained evidence unchanged. `SourcePlan` cannot be constructed
or deserialized directly. `apply_source` rejects stale inputs/provider metadata;
file-publication failures use the existing recoverable rollback contract.
`check_dependency_policy` returns verified external sources. Without the canonical
policy, only dependency-free checkouts pass. Existing `check_release_dependencies`
entry points also enforce this policy, preserving the earlier selected-tooling
checks. Invalid input/proof raises `ValueError`; native process and filesystem
failures retain their existing exception categories. See the
[proof boundaries and recovery guide](UPDATING_DEPENDENCIES.md#verified-dependency-sources).

## Registry and lock verification

Release inspection reads all declared requirements and universal lock entries,
including development/build dependencies, optional features and alternate
targets. Cargo additionally checks an all-features, unfiltered-target native
metadata graph with `--locked --offline`. uv checks lock freshness offline with
builds and interpreter downloads disabled. Neither inspection runs consumer
startup or build hooks. The native inspector executable must be outside the
observed checkout. Source replacements, alternate registries/indexes, external
paths, floating selectors, ambiguous metadata and unsupported provenance fail
closed. Python build requirements must also have a matching locked identity;
include them in a dependency group when uv would otherwise resolve them only
in an isolated build environment.

Availability proof binds exact PyPI upload URLs, sizes and SHA-256 digests, or
the crates.io archive checksum and size, and rejects yanked uploads. Cargo's
resolved input files are compared with an archive authenticated against the
lock and fresh crates.io metadata. The verifier uses matching compressed bytes
from the same Cargo cache when available and fetches the published archive on
a cache miss or mismatch.
Each Cargo archive is limited to 32 MiB compressed, 32 MiB per member, 64 MiB
expanded in total, and 10,000 members. Size diagnostics identify the crate and
the exceeded limit.
Existing Python environments are inspected through static distribution metadata
and compared with matching published wheels; packages are never imported into
the controller. An absent Python environment is an acquisition input: sync the
checked lock and run the required downstream validation before promotion.
Explicitly configured source builds use authenticated machine-local receipts
and retained wheels, including supported wheel relocation; see
[source-built dependencies](UPDATING_DEPENDENCIES.md#source-built-python-dependencies).
Unrecorded local builds cannot substitute for published-wheel proof. Universal
lock checks cover variants not installed on the current host; they do not claim
that those variants were tested there.
The exact legacy uv 0.8.0 `_virtualenv.py`/`_virtualenv.pth` pair is recognized
by content without executing or changing it. Modified or unknown startup hooks
are rejected; recreate such environments with the supported uv.

GitHub proof requires an exact full lowercase commit SHA, matching repository
identity, and static package metadata at the selected directory. Cargo additionally
requires a complete repository tree with at most 100 Cargo manifests and exactly
one package with the selected name. GitHub redirects, dynamic Python versions,
missing commits/packages and unavailable providers are errors. Commit existence
is development availability evidence, not a signature or publication assertion.
