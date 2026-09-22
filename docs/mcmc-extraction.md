# MCMC extraction and deletion map

This inventory is based on MCMC's landed v0.1.3 adoption at
`b717f48` (issue #164, pull request #167), rather than its earlier working
tree. It defines the coordinated v0.1.4 scope in
[issue #36](https://github.com/acgetchell/research-repo-tools/issues/36).
The consumer adoption follows in MCMC #166. Later repositories are not release
gates. This is an ownership and migration reference; release evidence belongs
in review notes and CI, and release history in [CHANGELOG.md](../CHANGELOG.md).

## Ownership and deletion map

The replacement column describes the complete required contract. A named
capability is not a claim that its release gate has already passed.

| Consumer family | Shared or native replacement | Consumer change and validation boundary |
| --- | --- | --- |
| `archive_performance`: release normalization, package/current/latest pair selection | `release_pairs.resolve_pair`; explicit publication/version order | Declare order and package manifest; retain one configuration test for intended release choices |
| Worktrees, working patch, new inputs, cleanup | `worktrees` with explicit measurement opt-in, byte runner, file selection | Delete orchestration; upstream covers binary/CRLF inputs, links, stale capture and dual failures |
| Benchmark execution and sample copying | Live command runner and comparisons of separate Criterion roots | Declare command, sample, timeout and workload; delete sample-copy implementation |
| CPU/OS/toolchain/dependency/source/harness capture | Shared measurement provenance with configured source inventories and probes | Declare source/harness paths and tool probes; retain benchmark-set scientific contracts |
| Release download, metadata, extraction | Authenticated asset workflow and bounded archive extraction | Declare repository, asset template and historical metadata layout |
| CSV/provenance readers and writers | Shared comparison/evidence format plus bounded legacy conversion | Preserve historical bytes; write conversions under a new path with original hashes; delete old writers |
| Current report selection, links, archive/index and promotion | Shared retained-report workflow and recoverable publication | Declare report/archive/evidence paths; upstream owns stale-input and immutable-pair regressions |
| `bench_compare`: model adapter, defaults and renderer | Shared comparison model, configured report prose and layout | Delete module; keep timing interpretation and workload meaning as scientific documentation |
| `publish_performance_readme`: identity/eligibility, table/SVG, links and markers | Declarative document publication including explicit future-release policy | Delete module; configure package/report references and selected rows; existing tags still require exact blobs |
| `update_release_version`: stable-tag wrapper and example callback | Shared release CLI with canonical stable policy; version-independent example commands | Delete module and callback; retain DOI assertions and historical exclusions in release config; ordinary checks remain offline |
| Release workflow draft lookup, packaging, upload/retry and publication | Shared release-asset commands | Preserve read-only benchmark job and separate write-token jobs without checked-out benchmark code |
| Setup composite action and inline environment program | Existing managed toolchain plus `toolchain export` | Keep thin uv/cache/setup steps and cache key declarations; delete inline Python |
| Just tracked/nonignored discovery and batching | `files list` / `files run` | Replace Markdown/TOML/YAML/JSON/workflow/Semgrep/notebook loops; configure exclusions and command arguments |
| Example execution and marker validation | Shared command validation; native executable discovery | Configure example names and expected scientific output markers; keep trace preparation and notebook dependency ordering |
| Prerequisite and package-preflight scaffolding | Shared executable checks and native package validation | Remove duplicate executable lookup and shell parsing; keep publishing decisions in consumer recipes |
| Notebook selection/lint/execution | File selection plus existing notebook commands | Keep fast/slow notebook choice, Ising trace checks, analysis and figure destinations |
| Clippy SARIF pipeline | Native `clippy-sarif` / `sarif-fmt`, existing shared tool catalog | Keep short pipefail pipeline, flags, artifact upload and permissions; no new Python converter |
| Semgrep SARIF and fixture policies | Native Semgrep output plus existing shared fixture validator | Keep rules, expected counts, scan flags and upload policy |
| Coverage | Native cargo-llvm-cov and existing shared Cobertura reporting | Keep scope/exclusions and Codecov workflow; no duplicated coverage parser exists |
| Audit, CodeQL, Dependabot, merge policy and workflow gate composition | Existing native commands/actions | Keep repository policy and deployment/permission choices; no common Python implementation to extract |
| Package metadata, console scripts, setuptools and wheel tests | Non-package uv project with exact shared tooling pin | Delete local build/entry points and self-referencing extra; retain notebook/tool dependency groups |

## Consumer coverage after deletion

The landed baseline contains 1,991 production Python lines across four modules,
2,987 Python test lines (including the test package initializer), and 217
collected tests. Counts exclude Python text embedded in workflows and recipes,
notebook cells, and intentionally invalid Semgrep fixtures. The complete
inventory above includes those non-module surfaces as well.

The intended retained consumer groups protect the `stepping` benchmark names,
fixture placement outside timed regions, fresh-batch behavior, Ising trace and
figure behavior, scientific interpretation, release DOI/reference configuration,
and a small number of installed-command wiring cases. Generic number parsing,
rendering, Git byte transport, cleanup, rollback, asset retry and command-file
injection regressions belong upstream. Wheel/console-script tests become obsolete
when the consumer is no longer a Python distribution.

Before publication, record actual candidate after-counts and classify every
removed group as shared upstream, replaced by configuration/native commands, or
obsolete. Do not use the desired count to discard scientific assertions.
Historical conversion tools retire after retained legacy artifacts have verified
shared-format companions and consumers no longer invoke the conversion path;
immutable historical originals do not need a continuing legacy writer.

## Coordinated release gate

Validate the installed candidate wheel outside this source tree against an
isolated MCMC candidate and representative fixtures. The candidate must delete
all four support modules and its installable tooling package while exercising
measurement, saved comparisons, retained rerender, release assets, promotion,
README publication, release metadata and notebook/setup/CI paths. The candidate
can remain uncommitted and need not merge before upstream publication.

Ordinary commands may not depend on sibling checkouts or private imports. Native
Linux, macOS and Windows package evidence is required, with fixture models
identified separately. Do not publish an intermediate capability release or
claim this gate is complete from local checks alone. Final MCMC adoption pins
the published registry artifact and refreshes its lock, then other repositories
can adopt on their own schedules.
