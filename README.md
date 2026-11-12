# research-repo-tools

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23744167.svg)](https://doi.org/10.5281/zenodo.23744167)
[![PyPI version][pypi-badge]][pypi]
[![License][license-badge]][license]
[![CI][ci-badge]][ci-workflow]
[![CodeQL][codeql-badge]][codeql-workflow]
[![zizmor][zizmor-badge]][zizmor-workflow]
[![Codecov][codecov-badge]][codecov-dashboard]
[![Audit dependencies][audit-badge]][audit-workflow]

Shared setup, checks, notebooks, performance evidence and release tooling for
research software repositories. Install one pinned Python package and invoke it
through your repository's Just recipes. Keep scientific algorithms, benchmark
case selections, native builds and deployment policy in your own project.

Start with [installation](#install-with-uv), try the [first workflow](#first-workflow),
then choose a [capability](#capability-index). Package development belongs in
[CONTRIBUTING.md][contributing].

## Release availability

This README describes the current source tree. The latest published package and
quickstart pin are [v0.2.8][published-pin]. Features introduced in v0.2.7 are
included in that release.

The v0.2.7 additions include verified Python/Cargo dependency sources,
published-dependency release gates, retained-evidence draft preparation and
authenticated retrieval of original tag-push receipts,
committed benchmark publication receipts, resumable scheduled suites,
reusable compilation for managed Cargo tool installs, and shared coverage cleanup.

The acquisition-owner workflow (`performance-release --owner`, `performance capture`,
and `performance first-release`), candidate archive attachment, and the latest
publisher provenance fixes are **unreleased**; v0.2.8 does not supply them. Their release
validation is tracked in [#236][next-release]. Use an exact supplying
[development pin](#development-pins-and-release-dependencies) for rehearsal,
then adopt the published supplying release before release-intent use.

**v0.2.7 includes breaking Python API changes.** Update callers to the documented
typed variants, including [trial orders][complete-api] and
[release-rule replacements][publishing-api]. Review the current
[API contracts][api] when upgrading custom integrations.

An upstream merge, local wheel or passing source test does not publish a package.
Before adopting a new capability, verify its exact PyPI release, update your
consumer's pin and lockfile, and run its integration checks. Keep working adapters
until that adoption succeeds. Package publication and consumer adoption are
separate steps. [Migration guidance][migration] covers compatibility changes;
the generated [CHANGELOG.md][changelog-history] records release history.

## Install with uv

Use an existing consumer Git repository; you do not need to clone this package.
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git first.
Recipes require a POSIX `sh` on PATH, including on Windows: add Git for Windows'
`bin` directory, typically `C:\Program Files\Git\bin`. uv supplies managed Python;
the published package requires Python 3.15 or newer.

From the consumer root, add the exact published package:

```sh
uv add --group tooling --no-sync "research-repo-tools==0.2.8"
```

Include `tooling` from `dev`, retaining your other dependencies. Set
`.python-version` to a compatible selection such as `3.15`. Keep any stricter
application Python constraints; use the [adoption workflow][python-adoption]
if the project currently requires an older interpreter.

```toml
[dependency-groups]
tooling = ["research-repo-tools==0.2.8"]
dev = [{include-group = "tooling"}]
```

Declare the uv version you installed as `tool.uv.required-version = "==X.Y.Z"`.
The [toolchain guide][toolchain] explains Rust and optional tool declarations;
a Python-only consumer can omit them. Refresh the lock, then export the matching
installed Just template:

```sh
uv lock
uv run --locked --only-group tooling research-repo-tools templates justfile --output justfile
```

For an existing justfile, omit `--output justfile` and merge the printed recipes;
the exporter refuses to overwrite files. Review and retain the manifest, lockfile,
Python selector and recipes together.

Run the one-time setup:

```sh
uv run --locked --managed-python --only-group tooling research-repo-tools setup
```

Setup verifies the declarations, installs a pinned user-level Just and declared
tools, configures PATH and synchronizes the locked development environment.
Open a new terminal if PATH changed. A failed step stops setup with a diagnostic;
fix that prerequisite and rerun. [Installation and recovery][toolchain] explains
ownership, native build prerequisites and platform behavior.

With v0.2.7, managed Cargo tool installs reuse compilation output across setup
and update runs while retaining exact-version installation and executable checks.
See [local and CI cache configuration][cargo-cache] before adopting it downstream.

Semgrep consumers request the `semgrep` extra on their shared package pin,
retain the shared Python 3.15 selector, and adopt the package's
[shared dependency overrides][rust-scans]
before locking. The consumer lock selects the exact compatible scanner used for
fixture and production scans. A package
upgrade does not rewrite copied Just recipes; merge the relevant exported
recipes as part of each consumer adoption and verify its integration before
removing temporary adapters.

With the v0.2.7 cleanup recipes, preview obsolete tool installations and retained
coverage artifacts with `just clean`, then use `just clean --apply` to remove
the listed entries. `just clean-coverage` previews only coverage cleanup;
`just clean-coverage --apply` performs it. Stop coverage runs first. Both preserve
the latest `.coverage` database and current reports. See the
[coverage cleanup contract][coverage-cleanup] for
the selected paths and adoption instructions.

## Development pins and release dependencies

With the v0.2.7 capability, downstream development and CI may use a reviewed
full commit from the tooling repository's main branch. Keep the requirement's
exact version and extras, and add an exact Git source in the consumer manifest:

```toml
[dependency-groups]
tooling = ["research-repo-tools[python-tools]==0.2.6"]
dev = [{include-group = "tooling"}]

[tool.uv.sources]
# Supplying main commit; its package metadata still declares 0.2.6.
research-repo-tools = {git = "https://github.com/acgetchell/research-repo-tools.git", rev = "5ad89e0f8ea861deb140109c25a30a254b27189d"}

[tool.research-repo-tools.release]
published-dependencies = ["research-repo-tools"]
```

The commit above supplies the dependency-source and release-dependency capabilities.
Choose a reviewed commit that supplies every interface your consumer uses.
Use the version declared by that commit, resolve
and sync it deliberately, and retain the manifest and lock together. Match any
isolated runtime selection to the same repository and full commit.
Normal setup, checks and development evidence remain usable. Git provenance
must agree with the declaration and lock; a matching version label alone fails.

Before a release campaign, publish the supplying upstream version, remove its
development source override, update both normal and isolated requirements to
that exact PyPI release, then resolve and sync the lock. Run:

```sh
just release-dependencies
just performance-prepare tooling/performance-release.toml --release-intent
just performance-release tooling/performance-release.toml --release-intent
```

Release evidence, asset and approval paths enforce the configured policy again.
The pin transition changes evidence inputs: revalidate the final configuration
and acquire fresh results when measured inputs changed. Retain earlier results
as rehearsal evidence. See the [proof, supported artifacts and public API][dependency-api]
for fail-closed limitations and recovery. No consumer guard implementation or
automatic pin/lock rewrite is needed.

Use the shared wrapper for isolated commands. Export the matching
`python-bootstrap.py` template beside the consumer Justfile; it contains the
same implementation as `research_repo_tools.tooling_runtime`. For example,
with the published v0.2.8 release:

```sh
just templates python-bootstrap.py --output python-bootstrap.py
just tooling-run --version 0.2.8 --release-intent --arguments-json '["release","dependencies"]'
```

For development, replace `--version` with `--github acgetchell/research-repo-tools
--commit FULL_40_CHARACTER_SHA` and omit `--release-intent`. Add
`--extras python-tools` when running checks that need the shared Ruff/ty/pytest
profile. Selectors are mutually exclusive. Missing releases, yanked-only releases,
missing commits and failed lookups stop before tooling starts; there is no fallback.

GitHub Actions can use the same implementation, with uv already installed:

```yaml
- uses: acgetchell/research-repo-tools/.github/actions/run-tooling@v0.2.8
  with:
    version: '0.2.8'
    release-intent: 'true'
    arguments: '["release", "dependencies"]'
```

This example requires the supplying release; pin the action itself to its
reviewed supplying commit under your Actions policy. For development, substitute
the `github` and `commit` inputs for `version`. `root` selects the observed
consumer/configuration root and `extras` selects optional package extras.
The Action supplies `RRT_GITHUB_LOOKUP_TOKEN` for commit lookups; the wrapper
removes it before launching tooling. Set the same variable outside Actions for
authenticated lookups. Caller-supplied `GITHUB_TOKEN` or `GH_TOKEN` also
authenticate lookups and remain available to tooling commands that require them.
Lookup requests never send tokens to PyPI, raw content, or redirect destinations.
The wrapper centralizes availability checks and isolated uv invocation;
normal dependency pins remain in the manifest/lock, and release gates verify
both installations. It never rewrites a consumer's pins or upgrades tooling.

## First workflow

After setup, list your recipes and check Markdown source lines in the consumer:

```sh
just help
just files check-lines --limit 160 --include '*.md' --exclude CHANGELOG.md
```

This read-only check needs no optional scanner or notebook dependencies. It selects
tracked and nonignored Markdown, counts every raw line (including tables and code),
and returns zero for a clean inventory. Long lines produce
`file:line: line length N exceeds 160` and status 1. Fix the selected source or
[declare your intended selection and limit][line-checks], then rerun.

Use `just ...` for routine work; no environment activation is needed. Recipes
select the locked package internally. The underlying CLI and [Python APIs][api]
remain available for integrations.

## Verified Python and Cargo dependency sources

With v0.2.7, use `dependency-sources.toml` at the checkout root to select upstream
Python packages or Cargo crates. Releases accept **PyPI and crates.io only**;
exact GitHub commits are available for development checks and rehearsal.

```toml
schema = 1

[projects.rust]
ecosystem = "cargo"
directory = "."
release-owned = ["."]

[sources.upstream]
project = "rust"
package = "upstream-crate"
github = "owner/repository"
commit = "0123456789abcdef0123456789abcdef01234567"
```

For Python, use `ecosystem = "python"` and a Python package name. Project
`directory` selects a project within the checkout; an optional source `directory`
selects the package within the upstream repository. Export the annotated template
with `just templates dependency-sources.toml --output dependency-sources.toml`.

```sh
just dependency-source upstream
just dependency-source upstream --apply
# Synchronize the native environment and run your downstream checks/rehearsal.
```

After upstream publication, replace `github`, `commit` and the source `directory`
with `version = "1.2.3"`. Preview/apply again, synchronize and revalidate against
the published dependency, then run `just release-dependencies`. Existing rehearsal
files remain available, but changed source/lock identities require fresh evidence.
The preview verifies availability before asking the native resolver to produce
a candidate; apply publishes the manifest and lock together.

The reusable `run-tooling` Action accepts the same command arguments: use
`["deps", "source", "upstream", "--apply"]` for an explicit development transition
and `["release", "dependencies"]` with `release-intent: 'true'` before release work.
Use the pinned Action and exact published tooling version shown above; consumers
do not assemble Git URLs or implement their own dependency guard. Workspace
ownership, registry proof limits and recovery are described in the
[dependency source guide][dependency-sources].

### Source-built Python dependencies

When a locked PyPI dependency needs a native source build, explicitly select it
in the canonical policy and pin its build requirements in a locked dependency
group. One root Python project is supported for source-build preparation:

```toml
[source-builds]
packages = ["package-needing-a-build"]
group = "build"
```

Run `just release-dependency-builds` before `just release-dependencies`. The
builder checks the published sdist against the lock, installs hash-locked build
tools from wheels into a fresh environment, builds with automatic build dependency
resolution disabled, and installs the resulting wheel. It never certifies an
existing local installation without rebuilding it. The release gate compares
installed files with that authenticated wheel, including supported `purelib`,
`platlib`, environment-relative `data`, and unchanged native `scripts` relocation. Other wheel relocation
schemes or rewritten script shebangs remain unsupported.

Backends that package downloaded executables must declare each platform's input
under `[[source-builds.external]]`: `package`, `platform`, exact GitHub release
asset `url`, `sha256`, archive `member`, and destination `wheel-member`. The builder
independently checks the archive hash and compares the packaged executable byte
for byte. This repository's [policy][dependency-policy] provides a complete
actionlint example. Keep these pins synchronized when updating that dependency.

Receipts and retained wheels live in `~/.research-repo-tools/source-builds`,
outside the checkout. `RESEARCH_REPO_TOOLS_BUILD_STORE` can select another absolute,
private directory outside it. Receipts authenticate the source, lock, policy,
manifest, build-tool inventory, builder, interpreter, native platform and output
wheel using a machine-local key. Changes require rebuilding. Each CI runner
creates its own evidence; do not copy signing keys between machines.

This trusts the local runtime, filesystem, native toolchain and verified upstream
build hooks. A fresh virtual environment is not a security sandbox; build hooks
can execute native tools or access the network. The receipt records observed
inputs and output, not a complete system dependency closure, reproducible build,
portable attestation, or publisher signature. Keep the store private using user
permissions on POSIX and the user profile's ACLs on Windows. If installation
fails partway, rerun preparation; release verification continues to reject any
unverified installed payload.

## Common workflows

These are independent commands; run only the workflow you intend. Some require
consumer configuration or optional dependencies described in their guides.

| Command | Use |
| --- | --- |
| `just changelog-preview --tag v1.2.3 --date YYYY-MM-DD` | Inspect prospective notes without replacing files |
| `just check` | Run your configured local checks and native/domain gates |
| `just files check-lines --limit 160 --include '*.md'` | Check selected raw text without formatting |
| `just help` | Discover recipes supplied by your installed template |
| `just notebook-inspect notebooks/analysis.ipynb --no-preview` | Inspect notebook structure without executing cells |
| `just setup` | Recreate the environment from existing declarations |
| `just tools-check` | Verify installed tools and versions without installing |
| `just update` | Upgrade declared tools and dependencies, then audit final locks |

The [complete command and capability reference][commands] is organized lexicographically.
`just help` remains the authority for the recipes actually merged into your project.
The [packaged templates][just-template] provide thin wrappers; consumers extend
checks with their own Rust, scientific and deployment requirements.

## Capability index

Choose a task guide for execution order and outcomes; use its linked reference
for exhaustive arguments, configuration and Python contracts.

| Task | Primary guide | Reference |
| --- | --- | --- |
| Automate Dependabot review and merge | [Configure the shared workflow][dependabot] | [GitHub settings][github-settings] |
| Build reproducible papers | [Dates, PDFs and native dependencies][papers-guide] | [Paper APIs][papers-api] |
| Check code, dependencies and secrets | [Python, Cargo, Semgrep, SARIF and review][checks-guide] | [Security APIs][security-api] |
| Generate changelogs and prepare releases | [Changelog generation][changelog], [metadata preparation][release] | [Release APIs][publishing-api] |
| Inspect and execute notebooks | [Notebook workflows][notebooks-guide] | [Notebook APIs][notebooks-api] |
| Prepare a retained-evidence release draft | [Isolated writer and recovery][draft-guide] | [Draft preparation API][draft-api] |
| Publish reports and figures | [Preview and publish retained evidence][reports-guide] | [Publication APIs][publication-api] |
| Publish to PyPI or crates.io | [Reviewed release procedure][publishing] | [Exact-commit evidence][evidence-guide], [optional reuse][reuse-guide] |
| Record complete performance results | [Prepare, measure/retain, publish README][performance-guide] | [Measurement policy][performance-policy], [complete-run APIs][complete-api] |
| Select files, run commands and export CI values | [Consumer checks and integration][checks-guide] | [File APIs][files-api], [process/Just APIs][process-api], [workflow APIs][workflow-api] |
| Set up or repair tools | [Installation and ownership][toolchain] | [Toolchain APIs][toolchain-api] |
| Time external commands | [Exploratory command timing][timing-guide] | [Timing evidence contract][timing-api] |
| Update dependencies and Actions pins | [Update and adoption workflows][updates-guide] | [CLI/configuration][configuration-api] |

### Git inspection

Use the index-preserving inspection API to replace local inspection wrappers:

```python
from pathlib import Path

from research_repo_tools.git_inspect import git_diff_paths_bytes, git_status_bytes

root = Path.cwd()
status = git_status_bytes(root).stdout
staged = git_diff_paths_bytes(root, cached=True).stdout.split(b"\0")[:-1]
worktree = git_diff_paths_bytes(root, base="HEAD", paths=["src"]).stdout.split(b"\0")[:-1]
```

Queries preserve the selected index bytes and return NUL-delimited byte output
with literal path selection. See the [inspection contract][git-inspection-api]
for configuration, deadlines, error handling and guarantee limits.

### Notebooks

Add the `notebooks` extra at your exact tooling version to a separate notebook
dependency group. Declare analysis packages and the required Ruff/ty checkers,
refresh the lock, then follow [notebook setup][notebooks-guide]:

```sh
just notebook-sync
just notebook-lint notebooks/analysis.ipynb
just notebook-execute notebooks/analysis.ipynb
```

Execution preserves source notebooks and writes executed copies plus JSON reports
under `target/notebooks`. A failed cell returns nonzero and identifies the cell.
Consumers retain scientific assertions and fast/slow selection. Interactive
JupyterLab, explicit source reset and source-policy checks are in the same guide.

### Performance

For a release with a published predecessor, configure the installed
`performance-release.toml` template with your
measurement, retained-report and document-publication policies. Declare and ignore
its scratch directory; keep the report archive outside scratch. Then run:

```sh
just performance-prepare tooling/performance-release.toml
just performance-release tooling/performance-release.toml
just performance-readme tooling/performance-release.toml --preview
just performance-readme tooling/performance-release.toml
```

Owner capture runs the release dependency gate before acquisition, even without
`--release-intent`. Packages selected in `published-dependencies` must use a
published registry release; development source overrides are rejected. Rehearse
an unreleased pin without `--owner` or `--release-intent`. After adopting the
published supplying release, pass `--owner "$owner"` to `just performance-release`
to retain the original assertions outside the checkout before report promotion.

For a repository's first stable release, `just performance first-release` acquires
a single current-only run and publishes its absolute timing report. Use this
procedure instead of the paired preparation, release and README commands above;
paired schema-2 README publication rejects first-release v6 evidence. See the
[candidate workflow][candidate-publication]
for capture, recovery and draft attachment.

For schema-5 measurement declarations, use a new ignored checkpoint directory
whose parent already exists. v0.2.7 also supports interruption recovery within
schema-6 scheduled trials. Resume explicitly after an interruption:

```sh
just performance-release tooling/performance-release.toml --checkpoint target/performance-checkpoint
just performance-release tooling/performance-release.toml --checkpoint target/performance-checkpoint --resume
```

Completed paired suites retain their original raw bytes and sessions. An
incomplete pair is recollected in full. Scheduled recovery retains completed
trials and completed suites within the interrupted trial; follow the
[schedule and recovery contract][candidate-guide]. `--progress json` emits versioned status
events on stderr; `--progress none` disables status. Heartbeats default to 300
seconds (five minutes); `--progress-cadence 60` or `--progress-cadence 600` selects
a shorter or longer interval. Stage changes and failures are reported immediately.
Report and README retries
continue to use retained evidence without measuring again.

Preparation saves independently selected release tags and full commits. Measurement
runs the gates and benchmarks, then retains/promotes the complete evidence.
README publication only consumes retained evidence: repair a presentation failure
and retry that last stage without measuring again. The [performance guide][performance-guide]
explains source exports versus worktrees, common versus independent harnesses,
parallel suite assembly, ratio eligibility and first-use report state.

The [benchmark reuse API][benchmark-reuse] derives release archives from
one accepted complete-run v4 acquisition. Supply a trusted, independently prepared
reuse policy, then plan and publish the archive without invoking benchmarks:

```python
from pathlib import Path

from research_repo_tools.benchmark_reuse import plan_reuse_archive
from research_repo_tools.publication import publish_publication

candidate = plan_reuse_archive(
    Path.cwd(), "target/run.json", "target/evidence.json",
    "tooling/reuse-policy.json", "target/benchmark-release.tar.gz",
)
publish_publication(candidate)
```

This v1 API requires exact tagged source measurements. The v0.2.6
[candidate contract][candidate-api] supports a single pre-tag
acquisition, report/README edits, final-main evidence acceptance, and archive
reuse. Its schema-6 measurement declaration includes repeated alternating or
seeded randomized trials, retained separately with observed execution receipts.
Follow the [Step 1 → Step 2 → publication procedure][candidate-guide].
The derived archive preserves original evidence and does not authenticate CI origin;
attachment and approval retain the existing release safeguards. Verify the
supplying PyPI release before adoption.

For the pre-tag contract, the consumer's Step 2 recipe can compose native evidence
and independently retained benchmark assertions through the shared CLI:

```sh
just release-evidence candidate tooling/release-evidence.toml \
  --checkout . --payload target/run.json --manifest target/evidence.json \
  --policy target/candidate-policy.json --acquisition-sha256 "$STEP1_ASSERTION_SHA256" \
  --output target/accepted.json
```

At approval, pass `--acceptance target/accepted.json` and a new output path to
revalidate that selection. Obtain `STEP1_ASSERTION_SHA256` from the trusted
acquisition owner's separate handoff. Never derive it from the candidate being
checked. These operations perform no benchmark acquisition or preflight.

For committed schema-2 publisher histories, v0.2.7 adds a publication receipt.
Declare the original report configuration in
`CandidatePolicy(report="tooling/performance-report.toml", ...)`.
Before promotion, freeze the shared publisher's exact outputs under acquisition-owner
control:

```python
from research_repo_tools.candidate_publication import plan_candidate_publication

plan, receipt = plan_candidate_publication(
    root, "target/run.json", "target/evidence.json", step_one_policy,
    acquisition_sha256=trusted_acquisition_digest,
)
# Save receipt and its SHA-256 through the separate trusted handoff.
publish_publication(plan)
```

The report configuration must predate acquisition. Existing prepared-release
adapters can capture this receipt in `measure_release(before_promotion=...)`;
the callback saves the handoffs and leaves promotion to `measure_release`.
Commit the generated run trio, index/latest files and reports with the release PR.
Step 2 adds `--publication-receipt /trusted/publication.json`,
`--publication-sha256 "$STEP1_PUBLICATION_SHA256"`, and `--format summary` to the
candidate command above. Keep final acceptance and attestations outside committed
history. [The contract][candidate-publication] preserves exact old history and
rejects arbitrary JSON/output paths.

### Releases

With v0.2.7, add `--format summary` to shared evidence verification commands for
release/commit, native run/attempt, actual package guarantee, optional benchmark acquisition,
signature and artifact-integrity results. `--format json` prints the same verified
record saved by `--output`; default shared CLI status messages remain compatible.
Checks that are not configured are labeled explicitly. Failures print diagnostics
and a nonzero status, without a success summary or replacement acceptance.

Follow the [ordered release procedure][publishing] and matching installed
`RELEASING.md` template. Exact-commit checks are the default. [CI reuse][reuse-guide]
is an explicit reviewed option with its own eligibility and revalidation;
package and artifact checks still apply. Approval, registry publication,
attachment uploads and downstream adoption remain distinct actions.

For the v0.2.7 draft-preparation capability, a read-only job verifies the tagged
source and generates notes. CI writers with repository-write credentials use
the isolated, exactly pinned, no-checkout invocation in the
[workflow and recovery guide][draft-guide].

The following recipe is for a trusted local project environment. It executes
the project's Justfile and uses its uv environment:

```sh
just release-assets prepare /trusted/evidence.toml \
  --repository owner/repository --tag v1.2.3 --commit FULL_COMMIT \
  --expected /trusted/selection.json --expected-title v1.2.3 \
  --notes /trusted/notes.md --receipt /durable/draft-receipt.json \
  --output /trusted/target.json --approve
```

This creates an unpublished stable draft or retains an exact matching draft and
its assets. Preserve the original receipt for every retry: missing or replaced
captured IDs reject, and ambiguous creation requires explicit recovery. The
guide covers immutable handoff authentication, least privilege, and the
supplying-release adoption gate.

After the preparation workflow succeeds, Step 2 can retrieve its original
authenticated target with `just release-assets retrieve`. The command freezes
the original run/attempt/artifact ID/digest and revalidates that selection on
retry. Configure the exact workflow and required steps as shown in the
[retrieval guide][draft-retrieval].

For Python releases, set `package-kind = "python"` and
`guarantee = "python-distributions"` in the evidence policy. Explicit selection
reads the Python project even beside a virtual Cargo workspace; existing
automatic selection remains Cargo-first. Declare the exact wheel/sdist filename
templates and PyPI destination:

```toml
[distributions]
files = ["example_app-{version}-py3-none-any.whl", "example_app-{version}.tar.gz"]
index-url = "https://test.pypi.org"
max-age-seconds = 86400
```

The consumer builds once and runs its native installed-package, scientific and
security checks against those files. In a subsequent evidence job of the same
trusted push attempt, capture the manifest before any tag exists:

```sh
just release-evidence create tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --run-id RUN_ID --attempt ATTEMPT --distributions /path/to/dist \
  --output /trusted/validation.json
```

Retain the manifest and exact distributions in the attempt-specific artifact
declared by the policy. Add a signature bundle only when a signing job is
configured. After the complete run succeeds, select and approve that evidence,
then revalidate the original selection and publication files before credentials:

```sh
just release-evidence verify tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --distributions /path/to/dist --output /trusted/selection.json
# After approval, immediately before the consumer's publication step:
just release-evidence verify tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --distributions /path/to/dist --expected /trusted/selection.json \
  --output /trusted/revalidated.json
# After the consumer uploads those exact files to the configured destination:
just release-evidence published tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --expected /trusted/selection.json --output /trusted/published.json
```

`published` checks registry inventory, metadata, hashes and downloaded bytes;
version presence alone does not prove byte identity. It performs no upload.
See the [evidence guarantees][python-evidence]
and [complete-run recovery][evidence-recovery].
These Python interfaces require an exact published supplying version containing
this contract, verified through ordinary wheel/sdist and locked consumer installs
before adoption; a merged source change or editable checkout is insufficient.

## Contributing

See [CONTRIBUTING.md][contributing] for package development, tests and maintainer
commands. [Supported interfaces][api] defines the consumer compatibility boundary.

## Citation

If you use research-repo-tools in your work, cite the software using
[CITATION.cff](https://github.com/acgetchell/research-repo-tools/blob/main/CITATION.cff).
Include the version used so others can identify the same release. The
[Zenodo concept DOI][doi] covers all versions; use the version-specific DOI from
the corresponding Zenodo record when citing an exact release.

## License

BSD-3-Clause. See [LICENSE][license].

[api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/api.md
[audit-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/audit.yml/badge.svg?branch=main
[audit-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/audit.yml
[benchmark-reuse]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/benchmark-reuse-api.md
[candidate-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/candidate-reuse-api.md
[candidate-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_BENCHMARKS.md
[candidate-publication]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/candidate-reuse-api.md#committed-publisher-evidence
[cargo-cache]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md#first-adoption-and-setup
[changelog]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/GENERATING_CHANGELOGS.md
[changelog-history]: https://github.com/acgetchell/research-repo-tools/blob/main/CHANGELOG.md
[checks-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_CHECKS.md
[ci-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/ci.yml/badge.svg?branch=main
[ci-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/ci.yml
[codecov-badge]: https://codecov.io/gh/acgetchell/research-repo-tools/branch/main/graph/badge.svg
[codecov-dashboard]: https://app.codecov.io/gh/acgetchell/research-repo-tools
[codeql-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/codeql.yml/badge.svg?branch=main
[codeql-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/codeql.yml
[commands]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_COMMANDS.md
[complete-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/complete-run-api.md
[configuration-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/configuration-api.md
[contributing]: https://github.com/acgetchell/research-repo-tools/blob/main/CONTRIBUTING.md
[coverage-cleanup]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md#cleaning-retained-coverage
[dependabot]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/AUTOMATING_DEPENDABOT.md
[dependency-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/dependency-provenance-api.md
[dependency-policy]: https://github.com/acgetchell/research-repo-tools/blob/main/dependency-sources.toml
[dependency-sources]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md#verified-dependency-sources
[doi]: https://doi.org/10.5281/zenodo.23744167
[draft-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/release-evidence-api.md#draft-preparation
[draft-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PREPARING_DRAFTS.md
[draft-retrieval]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PREPARING_DRAFTS.md#retrieve-original-preparation-output
[evidence-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/VERIFYING_RELEASE_EVIDENCE.md
[evidence-recovery]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/VERIFYING_RELEASE_EVIDENCE.md#complete-run-recovery
[files-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/files-api.md
[git-inspection-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/process-api.md#python-git-inspection-api
[github-settings]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/CONFIGURING_GITHUB.md
[just-template]: https://github.com/acgetchell/research-repo-tools/blob/main/src/research_repo_tools/templates/justfile
[license]: https://github.com/acgetchell/research-repo-tools/blob/main/LICENSE
[license-badge]: https://badgen.net/github/license/acgetchell/research-repo-tools
[line-checks]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_CHECKS.md#raw-text-line-limits
[migration]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/migration.md
[next-release]: https://github.com/acgetchell/research-repo-tools/issues/236
[notebooks-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/notebooks-api.md
[notebooks-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_NOTEBOOKS.md
[papers-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/papers-api.md
[papers-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/BUILDING_PAPERS.md
[performance-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RECORDING_PERFORMANCE.md
[performance-policy]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/DECLARING_PERFORMANCE.md
[process-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/process-api.md
[publication-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/publication-api.md
[published-pin]: https://pypi.org/project/research-repo-tools/0.2.8/
[publishing]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RELEASING.md
[publishing-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/publishing-api.md
[pypi]: https://pypi.org/project/research-repo-tools/
[pypi-badge]: https://badgen.net/pypi/v/research-repo-tools
[python-adoption]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md#shared-python-adoption
[python-evidence]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/release-evidence-api.md#python-distribution-evidence
[release]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_RELEASE_METADATA.md
[reports-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/PUBLISHING_REPORTS.md
[reuse-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/REUSING_CI.md
[rust-scans]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/RUNNING_CHECKS.md#rust-documentation-scans
[security-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/security-api.md
[timing-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/command-timing-api.md
[timing-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/TIMING_COMMANDS.md
[toolchain]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/INSTALLING.md
[toolchain-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/toolchain-api.md
[updates-guide]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/UPDATING_DEPENDENCIES.md
[workflow-api]: https://github.com/acgetchell/research-repo-tools/blob/main/docs/workflow-api.md
[zizmor-badge]: https://github.com/acgetchell/research-repo-tools/actions/workflows/zizmor.yml/badge.svg?branch=main
[zizmor-workflow]: https://github.com/acgetchell/research-repo-tools/actions/workflows/zizmor.yml
