# Upstream tooling provenance

This capability targets v0.2.7. It separates reproducible development sources
from published upstream tooling required for a consumer release. Select only
tooling dependencies; scientific dependencies and native release gates remain
consumer policy. See the [usage and transition example](../README.md#development-pins-and-release-dependencies).

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

The supported artifact proof uses PyPI and `files.pythonhosted.org`, with
32 MiB compressed/uncompressed wheel bounds and at most 10,000 members.
Wheels requiring `.data` relocation, custom indexes, missing wheel artifacts,
or ambiguous installed wheel tags fail closed. Package directories are also
inventoried for added code, including files omitted from RECORD. Shared namespace
directories containing other packages and symlinked package directories are
unsupported. Installed observation is bounded to 32 MiB and 10,000 files.
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
  An empty effective policy is a no-op. Failures raise `ValueError` with recovery
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

GitHub selection supports public repositories and requires Python 3.11+ for TOML;
the Just and Actions wrappers select managed Python 3.15 for bootstrap. The target
package's metadata selects its own runtime Python. Release intent rejects Git
before lookup. A verified release cannot be redirected by ambient uv source/index
configuration. This existence check complements the subsequent checkout, lock and
installed-byte release gate; it does not replace it or assert scientific validity.
The action `.github/actions/run-tooling` and exported `python-bootstrap.py` use
this single module. See [wrapper usage](../README.md#development-pins-and-release-dependencies).

The CLI `release dependencies` exposes the release check independently.
Configured checks run at pre-tag `release evidence` creation/verification,
candidate acceptance and approval revalidation, asset preflight/revalidation/upload,
and reviewed release gate/publication/verification. Checkout-taking APIs inspect
the observed checkout, even when the controller runtime lives elsewhere.
Asset APIs accept `dependencies=()` to carry additional explicit selections
through every revalidation, including the final upload/publication checks.
Low-level rootless evidence functions still verify their explicit provider and
artifact inputs; callers composing those primitives must call the dependency gate
with the observed root before effects.

`performance prepare`, `performance measure`, and `performance release` accept
`--release-intent` to expose pin transitions before source fetches, campaign reads,
measurement or output writes. The `prepare_performance` and `measure_release`
Python APIs accept `release_intent=True`. Ordinary acquisition defaults remain
available with exact development pins. The flag does not turn rehearsal evidence
into release evidence or waive later source/freshness checks.

Changing a Git pin to a published pin changes manifest/lock and often harness
inputs. Preserve old rehearsal results and revalidate the final configuration;
changed measured inputs require a fresh acquisition and checkpoint. Never relabel
an old candidate or modify its independent assertions to fit the final pin.
