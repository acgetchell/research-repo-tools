# Supported interfaces

The public contract comprises the installed `research-repo-tools` CLI, documented
TOML configuration, packaged templates and Python interfaces linked here.
Consumers pin an exact published version, or a verified full development commit,
and lock dependencies. [Release dependency policy](dependency-provenance-api.md)
requires selected upstream tooling to be published at release boundaries. Patch
releases normally preserve these contracts; minor releases may introduce
documented changes while the package is below 1.0. Release v0.2.7 made
an explicit exception for the documented Python API redesign: callers upgrading
from earlier releases must adopt the current signatures and typed variants.
Unlisted modules and private names are implementation details. Imports do not
install tools, access the network or modify consumer files.

Start with the [consumer quickstart](../README.md#install-with-uv) or
[task index](../README.md#capability-index). This reference describes current
source; [release availability](../README.md#release-availability) determines when
a consumer can adopt it. Compatibility changes belong in the [migration map](migration.md),
and release history in [CHANGELOG.md](../CHANGELOG.md).

## CLI and configuration

Global `--root` and `--config` precede the command group. By default, settings live
under `[tool.research-repo-tools]` in the consumer's `pyproject.toml`; a standalone
configuration uses unprefixed tables and `schema = 1`. Unknown settings fail.
Relative paths resolve against the consumer root, and bare executables use PATH.
The [configuration reference](configuration-api.md) owns detailed command behavior,
exit statuses, path rules, optional dependencies and the `cli.main` entry point.

Use `just help` for the consumer's actual recipes and the
[command reference](RUNNING_COMMANDS.md) for the current template catalog.

## Capability references

Public function and dataclass signatures can be inspected with
`inspect.signature` without loading optional dependencies. For evaluated types,
`typing.get_type_hints` resolves package types such as the measurement plan
returned by `measurement.load_measurement`. Evaluating an optional type also
loads its dependency. For example, install the `notebooks` extra before
resolving the `Notebook.node` annotation to `nbformat.NotebookNode`.

| Capability | Authoritative reference |
| --- | --- |
| Cargo documentation | [Stable packaged library docs](RUNNING_CHECKS.md#cargo-documentation) |
| Cargo examples | [Discovery, configuration and live streams](cargo-examples-api.md) |
| Configuration and CLI | [Configuration objects, entry point and platform contract](configuration-api.md) |
| Coverage cleanup | [Plans, typed removals and explicit application](#coverage-cleanup) |
| Files and text | [Selection, line checks and transactional file/directory publication](files-api.md) |
| Git inspection | [Index-preserving status and content-based changed paths](process-api.md#python-git-inspection-api) |
| Notebooks | [Workflow and isolated test APIs](notebooks-api.md), [inspection JSON](notebooks-api.md#notebook-inspection-schema) |
| Papers | [Explicit dates, PDFs and native dependency discovery](papers-api.md) |
| Path identity | [Typed native observations and versioned fingerprints](files-api.md#native-path-observations) |
| Processes and Just | [Byte/text execution, stderr assertions and recipe inspection](process-api.md) |
| Publication | [Document markers, tables, plots and evidence selection](publication-api.md) |
| Releases | [Metadata planning and registry publication](publishing-api.md), [evidence/assets](release-evidence-api.md) ([workflow](VERIFYING_RELEASE_EVIDENCE.md)) |
| Review | [CodeRabbit outcomes, content identity and one-use consent](review-api.md) |
| Runtime policies | [Dedicated locked graphs, retained wheels and isolated verification](runtime-policy-api.md) |
| Security and SARIF | [Scanners, Rust documentation and SARIF generations](security-api.md) |
| Toolchains | [Python adoption and managed-install cleanup](toolchain-api.md) |
| Upstream tooling | [Development identity and published release dependencies](dependency-provenance-api.md) |
| Workflows | [Configured measurement/assets, validation and CI export](workflow-api.md) |

## Coverage cleanup

`research_repo_tools.coverage_clean.plan_clean(root: Path) -> CleanPlan` inspects
the shared [coverage layout](INSTALLING.md#cleaning-retained-coverage) without
reading databases or changing files. The frozen plan carries the resolved `root`,
ordered `removals`, and `preserved` paths with unfamiliar contents. `plan.size`
reports selected file bytes, not allocated disk blocks.

Each removal is a `FileRemoval(path, size, identity)` or
`RunRemoval(path, identity, files)`. A run's `files` is an immutable tuple of
`FileRemoval` records and its `size` is their sum. Identities record device, inode,
modification time and change time; these are snapshots, not permanent ownership
proof. Obtain plans from `plan_clean` rather than constructing removal records.

`apply_clean(plan: CleanPlan) -> None` recomputes the plan before deleting files
and empty run directories. A changed inventory or refused link/junction raises
`ValueError` before any removal. I/O failures and link/junction refusals after
removal starts raise `RuntimeError` with the original cause and partial-progress
warning. Callers must stop coverage writers throughout planning/application.
Neither function installs tools, executes configuration, or needs coverage.py.

## Performance

Choose the contract that matches the evidence you have:

| Input or task | Reference |
| --- | --- |
| Complete native acquisitions | [Declaration and source/harness modes](DECLARING_PERFORMANCE.md), [complete-run API](complete-run-api.md) |
| Complete acquisition reuse | [Derived release archives, independent assertions and trust boundaries](benchmark-reuse-api.md) |
| Candidate publication | [Committed generated evidence and final-release binding](candidate-reuse-api.md#committed-publisher-evidence) |
| Criterion discovery and saved samples | [Inventories, exact raw files and parallel suite assembly](criterion-api.md) |
| Existing Criterion comparisons | [Samples, provenance, archive retrieval and extraction](performance-api.md) |
| Exploratory external-command timing | [Plans, retained timing reports and claim limits](command-timing-api.md) |
| Prepared release workflow | [Independent assertions, acquisition/retention and README publication](performance-release-api.md) |

Consumers own scientific cases, independent correctness gates, compatibility
requirements and release selection. Validation and retention preserve complete
raw evidence; presentation can select fewer rows without reducing acquisition.
Different hosts, harnesses or workloads do not implicitly qualify ratios.
Offline rendering retains the measurement environment rather than observing the
publisher's host. See [migration](performance-migration.md) before replacing an
adapter or changing a historical evidence format.

## Dependency sources

Use [dependency selectors and source plans](dependency-provenance-api.md#dependency-sources)
to verify a PyPI release, crates.io release or exact GitHub commit and apply a
reviewed change to a consumer. For the manifest, lock and installed-byte checks
required before release, see [configuration and proof](dependency-provenance-api.md#configuration-and-proof).
