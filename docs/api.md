# Supported interfaces

The public contract comprises the installed `research-repo-tools` CLI, documented
TOML configuration, packaged templates and Python interfaces linked here.
Consumers pin an exact published version and lock dependencies. Patch releases
preserve these contracts; minor releases may introduce documented changes while
the package is below 1.0. Unlisted modules and private names are implementation
details. Imports do not install tools, access the network or modify consumer files.

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

| Capability | Authoritative reference |
| --- | --- |
| Cargo examples | [Discovery, configuration and live streams](cargo-examples-api.md) |
| Configuration and CLI | [Configuration objects, entry point and platform contract](configuration-api.md) |
| Files and text | [Selection, line checks and transactional file/directory publication](files-api.md) |
| Notebooks | [Workflow and isolated test APIs](notebooks-api.md), [inspection JSON](notebook-inspection.md) |
| Papers | [Explicit dates, PDFs and native dependency discovery](papers-api.md) |
| Processes and Just | [Byte/text execution, stderr assertions and recipe inspection](process-api.md) |
| Publication | [Document markers, tables, plots and evidence selection](publication-api.md) |
| Releases | [Metadata planning and registry publication](publishing-api.md), [evidence/assets](release-evidence-api.md) ([workflow](VERIFYING_RELEASE_EVIDENCE.md)) |
| Security and SARIF | [Scanners, Rust documentation and SARIF generations](security-api.md) |
| Toolchains | [Python adoption and managed-install cleanup](toolchain-api.md) |
| Workflows | [Configured measurement/assets, validation and CI export](workflow-api.md) |

## Performance

Choose the contract that matches the evidence you have:

| Input or task | Reference |
| --- | --- |
| Complete native acquisitions | [Declaration and source/harness modes](DECLARING_PERFORMANCE.md), [complete-run API](complete-run-api.md) |
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
