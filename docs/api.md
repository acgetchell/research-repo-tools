# Supported interfaces

The public contract comprises the installed `research-repo-tools` CLI, documented
TOML configuration, packaged templates and Python interfaces linked here.
Consumers pin an exact published version, or a verified full development commit,
and lock dependencies. [Release dependency policy](dependency-provenance-api.md)
requires selected upstream tooling to be published at release boundaries. Patch releases
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

Public function and dataclass signatures can be inspected with
`inspect.signature` without loading optional dependencies. Annotations involving
optional dependencies or cyclic module references remain forward-reference
strings. Tools that evaluate those annotations must supply the corresponding
type namespace; signature display does not require resolving them.

| Capability | Authoritative reference |
| --- | --- |
| Cargo documentation | [Stable packaged library docs](cargo-docs-api.md) |
| Cargo examples | [Discovery, configuration and live streams](cargo-examples-api.md) |
| Configuration and CLI | [Configuration objects, entry point and platform contract](configuration-api.md) |
| Files and text | [Selection, line checks and transactional file/directory publication](files-api.md) |
| Git inspection | [Index-preserving status and content-based changed paths](process-api.md#python-git-inspection-api) |
| Notebooks | [Workflow and isolated test APIs](notebooks-api.md), [inspection JSON](notebook-inspection.md) |
| Papers | [Explicit dates, PDFs and native dependency discovery](papers-api.md) |
| Processes and Just | [Byte/text execution, stderr assertions and recipe inspection](process-api.md) |
| Publication | [Document markers, tables, plots and evidence selection](publication-api.md) |
| Releases | [Metadata planning and registry publication](publishing-api.md), [evidence/assets](release-evidence-api.md) ([workflow](VERIFYING_RELEASE_EVIDENCE.md)) |
| Security and SARIF | [Scanners, Rust documentation and SARIF generations](security-api.md) |
| Toolchains | [Python adoption and managed-install cleanup](toolchain-api.md) |
| Upstream tooling | [Development identity and published release dependencies](dependency-provenance-api.md) |
| Workflows | [Configured measurement/assets, validation and CI export](workflow-api.md) |

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

`research_repo_tools.dependency_sources` owns `RegistryRelease`, `GitCommit`,
`parse_source` and `verify_source`. Each immutable selector validates its own
syntax; `verify_source` alone creates `VerifiedSource` availability evidence.
Python versions follow public PEP 440 rules; Cargo versions follow SemVer.
Supported release registries are exclusively PyPI and crates.io. Development
repositories are GitHub repositories pinned to full commit SHAs. A provider's
`read(url) -> bytes` is a transport seam for deterministic tests, not a mechanism
for importing saved proof or adding unsupported registries.

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
apply_source(plan)                   # reverifies; atomic manifest/lock update
# After transitioning to a published source and rerunning downstream checks:
check_dependency_policy(root)        # read-only, all configured projects
```

`plan_source` may populate native resolution caches but leaves consumer inputs,
environments and retained evidence unchanged. `SourcePlan` cannot be constructed
or deserialized directly. `apply_source` rejects stale inputs/provider metadata;
file-publication failures use the existing recoverable rollback contract.
`check_dependency_policy` returns verified external sources and is a no-op when
the canonical policy file is absent. Existing `check_release_dependencies`
entry points also enforce this policy, preserving the earlier selected-tooling
checks. Invalid input/proof raises `ValueError`; native process and filesystem
failures retain their existing exception categories. See the
[proof boundaries and recovery guide](UPDATING_DEPENDENCIES.md#verified-dependency-sources).
