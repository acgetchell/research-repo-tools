# Supported interfaces

The `0.1` series supports the `research-repo-tools` command, its documented TOML
configuration, packaged templates, and the Python APIs listed below. Consumers
should pin an exact released version in their uv development dependencies.
Patch releases preserve these contracts; a minor release may introduce a
documented breaking change while the package remains below `1.0`.
The v0.1.8 [Semgrep scan migration](shared-capability-migration.md#sarif-and-batched-scan-adoption)
is an explicit exception: it changes the default report layout and gives scans
exclusive ownership of their output directory. Review that migration before
updating a consumer's exact pin.

The [complete-run and host APIs](complete-run-api.md) extend the performance
contracts with common-harness plans, raw sample retention, named phase series,
immutable runs, host observations and profiling declarations. Consumer usage
and release availability are described in the [README](../README.md#common-harness-and-complete-runs).

## Consumer just recipes

The [packaged justfile template](../src/research_repo_tools/templates/justfile)
supplies thin wrappers that consumers retain in their own justfiles. Each recipe
selects the consumer's locked package version. The shared implementation stays
in the installed package; the consumer owns its configuration and invocation.

The [README command reference](../README.md#just-recipes) covers routine use.
The [toolchain guide](INSTALLING.md) defines managed execution, and the
[changelog guide](GENERATING_CHANGELOGS.md) describes generation and archiving.
Package development and publication preflight recipes belong to
[Contributing](../CONTRIBUTING.md#maintainer-commands).
Root and consumer release recipes share names and argument contracts; see
[reviewed publication](#reviewed-registry-publication-api), the
[release checklist](RELEASING.md), and [migration](release-policy-migration.md).
These interfaces cover PyPI and crates.io; consumers retain native packaging
and deployment policy.

## Command line and configuration

The [README](../README.md#workflow-examples) contains runnable examples and
help commands for inspecting CLI arguments.

Place global `--root` and `--config` options before the command group. Configuration
defaults to `[tool.research-repo-tools]` in the consumer's `pyproject.toml`.
A standalone configuration uses unprefixed tables and `schema = 1`, as shown in
the [packaged template](../src/research_repo_tools/templates/research-repo-tools.toml).
Unknown settings fail. Relative paths and explicit executable paths resolve
against the consumer root; bare executable names use `PATH`.
Explicit `ci export --file` and `toolchain export --file` paths follow this rule.
The `GITHUB_ENV` fallback keeps the path supplied by the invoking environment.

`actions allowlist --policy PATH INPUT...` checks consumer-owned selected-actions
JSON against structured workflow job/step references. Only the strict false/false
owner-mode payload with exact `owner/repo[/path]@*` patterns is supported. Local
actions and containers are outside this policy. YAML aliases are supported with
anchor locations; duplicate/merge keys and recursive aliases fail. Explicit
directories expand to regular `.yml`/`.yaml` files; repeated paths are deduplicated.
Leaf symlinks are rejected and parent paths are resolved. Inputs and
repository settings remain unchanged. See [consumer wiring](../README.md#actions-allowlists-and-opt-in-pin-updates).

`actions update --policy PATH INPUT... [--check | --dry-run]` resolves explicit
Actions targets from consumer TOML through GitHub CLI and preserves workflow
source bytes outside scalar SHAs/version comments. `--check` returns 1 for drift;
`--dry-run` previews without drift failure. All candidates and compatibility
inventories are checked before any workflow replacement. Caught publication
failures use the shared rollback/recovery contract. The YAML/update modules are
implementation details; use the CLI or `cli.main`.

`deps update-uv --dry-run` inspects standalone, Homebrew, or `uv tool` ownership
and reports the native operation and intended project-pin reconciliation. Applying
verifies the resulting stable version before preserving/replacing the project
pin. The existing project pin may differ from installed uv; launch with the
packaged `update-uv` recipe. Installation upgrades persist if verification or
publication fails; reported recovery is inspection/repair and retry.
`deps.tool-owners` explicitly selects `cargo`/`homebrew` for mapped user tools;
prebuilt tools migrate to the authoritative `toolchain.binaries` contract.

`toolchain sync-binaries` verifies and repairs only the consumer's exact managed
release-binary pins. It uses the existing host/version cache, release URL/tag
checks, SHA-256 verification and staged executable probes. Warm caches are
probed without release requests; damaged caches use the verified installer.
It performs no package/environment synchronization, Python/Rust/Cargo/Just
installation, or unrelated tool probes. The command supports the usual `--root`
and `--config` options and can be called through `cli.main` without importing
toolchain or prebuilt implementation APIs. Failures return nonzero and retain
previous files and earlier successful installations.

Only release-metadata requests consume the first nonempty `GITHUB_TOKEN`, then
`GH_TOKEN`. All toolchain probes and setup subprocesses remove both variables
case-insensitively while preserving other build settings. Release-installation
diagnostics redact both inherited values. Explicit `toolchain run` commands
inherit the caller's credential variables. The
[GitHub Actions example](../README.md#authenticated-release-installation-in-github-actions)
separates package installation and ordinary setup from authenticated binary sync.

`zizmor check` verifies one repository-declared scanner pin and requires an explicit
`[tool.research-repo-tools.zizmor] persona`. It supports plain and SARIF reports,
automatic authentication discovery, `--offline`, and `--require-online`.
The [packaged audit and validation contract](../src/research_repo_tools/templates/VALIDATING_WORKFLOWS.md)
defines token precedence, redaction, timeouts, native exit status (including
SARIF's finding behavior), complete Python inventory, and migration requirements.
It is available from installed distributions as `templates VALIDATING_WORKFLOWS.md`.
The `python-validation.toml` and `zizmor.yml` templates accompany it. The zizmor
module itself is an implementation detail; use the supported CLI entry point.

`python check`, `python fix`, and `python typecheck` use the same complete Git
inventory and native consumer policy. Checks require existing Ruff/ty executables;
only `fix` enables source edits. See the [Python gate contract](../src/research_repo_tools/templates/VALIDATING_WORKFLOWS.md#python-inventory-and-policy)
for empty selections, batching, timeouts, and failure behavior. The `python_checks`
module is private; use the CLI or `cli.main`.

Commands return zero on success. Validation failures and handled operational
errors return nonzero; argument errors return `2`. Help and version output are
successful exits. Diagnostics use stderr, while reports and generated content
use stdout. Human-readable diagnostics and progress messages are not a structured
machine API. Template output and extracted release notes are intended for reuse.
Notebook inspection additionally supports a [versioned JSON inventory](notebook-inspection.md).
Completed inventories may report repair problems with status zero; advisory
warnings fail only when strict mode is enabled. These review commands do not
replace notebook validation.

Review commands require Git and an externally installed, authenticated CodeRabbit
CLI. `review branch --base origin/main` verifies the cached base against the remote;
`review uncommitted` does not query a remote. Both use the configured consumer root
and require its `AGENTS.md` and exactly one CodeRabbit YAML configuration. See the
[review contract and recipes](../README.md#coderabbit-review). CodeRabbit output is
streamed without a wrapper timeout. Its exit status propagates; signal termination
maps to 128 plus the signal number, and keyboard interruption returns 130.

File-changing commands operate only when invoked: dependency and release updates,
changelog generation/normalization/archiving, template output, local tagging,
explicit setup/toolchain synchronization and upgrades, and notebook synchronization
or output cleanup. Notebook launch synchronizes its locked environment and uses
private session caches; notebook reset previews by default and requires `--apply`
for restoration and declared deletion. Notebook execution publishes separate artifacts. Performance
output, asset retrieval, and extraction publish only when explicitly invoked.
Dry runs are available only where command help lists them. Importing the package
does not install tools, access the network, or modify consumer files.

## Python entry point

Thin Python scripts can reuse the same command contract without a subprocess;
see the [README example](../README.md#calling-from-python).

- `research_repo_tools.__version__` is a string read from installed distribution
  metadata. `project.version` in this package's `pyproject.toml` is its authority.
- `research_repo_tools.cli.main(argv: list[str] | None = None) -> int` accepts
  arguments without the executable name. `None` uses the process arguments.
  It writes to the process stdout/stderr and returns the command status.
  Argument parsing raises `SystemExit(0)` for help/version and `SystemExit(2)`
  for usage errors. Unexpected programming errors may propagate.

## Python configuration API

Use `research_repo_tools.config.load(path=None, root=None)` to construct settings
for the APIs below. By default it reads `[tool.research-repo-tools]` from
`pyproject.toml` under `root`, or the current directory when `root` is omitted.
An explicit `path` selects a configuration file; relative file paths use the
caller's directory. Files named `pyproject.toml` use the prefixed table; other
filenames use unprefixed tables. Without an explicit root, the file's parent is
the consumer root. A missing default manifest supplies default settings, while
a missing explicitly selected file raises `ValueError`.

`config.parse(value, *, root)` validates an in-memory unprefixed configuration
table against the same schema. Both factories resolve the consumer root,
reject unknown or malformed settings with `ValueError`, and perform no tool
installation or execution. File reads can raise `OSError`; invalid TOML raises
`tomllib.TOMLDecodeError`. Treat the returned settings as opaque: pass them to
documented APIs and use these factories to change configuration. Dataclass
constructors and internal attributes are not supported extension points.

```python
from pathlib import Path

from research_repo_tools.config import load
from research_repo_tools.toolchain_clean import plan_clean

settings = load(root=Path.cwd())
preview = plan_clean(settings, keep_roots=(Path("../another-consumer"),))
```

## Python notebook policy and workflow API

Targeting v0.1.8, `research_repo_tools.config.load` and `parse` accept
`notebooks.id-pattern`, `notebooks.lab`, and `notebooks.reset` as described in
[the notebook contract](RUNNING_NOTEBOOKS.md). Use configuration parsing to
construct settings for these public interfaces:

- `research_repo_tools.notebook_lint.lint(settings, paths, *, timeout=30,
  id_pattern=None) -> int`: optional full-match pattern override; `None` uses
  configuration. Paths are explicit `Path` objects; the notebook extra and
  consumer's locked Ruff/ty environment are required. It returns zero for a
  clean selection and one for findings. Configuration/structure failures raise
  `ValueError`; missing notebook dependencies raise `RuntimeError`.
- `research_repo_tools.notebook_workflows.launch(settings, *, browser=None,
  scratch_dir=None) -> int`: `None` selects configuration. It requires uv and
  the consumer's declared, locked notebook environment with JupyterLab and
  project kernel. Exit status propagates; interruption returns 130. Caches
  are private beneath scratch and the caller's environment remains unchanged.
  A `UV_PROJECT_ENVIRONMENT` override must remain strictly beneath the consumer
  root without symlink or junction components.
- `research_repo_tools.notebook_workflows.reset(settings, paths=(), *,
  revision=None, apply=False) -> NotebookResetPlan`: `paths` is a sequence of
  literal `Path` files/directories, relative to the consumer root or absolute
  within it. Empty paths use configured sources. `revision=None` selects the
  index; a string explicitly selects a verified Git tree. Preview performs
  only reads. `apply=True` explicitly opts into working-file restoration and
  declared cleanup. The base package suffices; notebook dependencies are not
  loaded. Git/OS discovery errors propagate during preflight; unsafe maps raise
  `ValueError`. Restore/cleanup failures raise `RuntimeError` with phase and
  partial-progress diagnostics. Interrupts propagate.

`NotebookResetPlan` is frozen and exposes `sources` and `cleanup` as tuples of
absolute `Path` objects, and `revision` as a pinned tree ID or `None` for the
index. It describes the requested operation, not a mutable execution handle or
proof of successful application. The CLI and these functions share validation,
preview output, and failure behavior. See the [README](../README.md#notebooks)
for consumer commands and the exact deletion map.

## Python notebook integration-test API

Targeting v0.1.8, `research_repo_tools.notebook_testing` exports
`isolated_project`, `NotebookProject`, and `NotebookExecution`. Importing the
module needs only the base package; execution needs the notebook extra. There
is no pytest dependency. Use the factory/context manager; result constructors
and private attributes are not extension points.

`isolated_project(source_root, paths, *, parent, environment)` yields a
`NotebookProject` in a new temporary directory under the existing `parent`.
Arguments are `Path` objects; `paths` is an explicit sequence of files relative
to `source_root`, or absolute files inside it. Directories, traversal, links
(including Windows junctions), `.git`, `.venv`, and `rust-toolchain.toml` are
rejected. The manifest, `.python-version`, and `uv.lock` are required and copied
automatically, byte-for-byte. Other files are copied only when listed. `parent`
must be outside the source project and borrowed environment. Copy/parse failures
clean up the new workspace and preserve the originals.

`environment` explicitly selects the already synchronized Python environment
running the tests: normally `Path(sys.prefix)`. A different environment is an
error; launch the tests with its interpreter instead. Execution verifies the
declared uv and Python versions and notebook group, and hashes the copied lock.
It does not re-resolve the lock or verify every installed package against it.
No environment is created, synchronized, upgraded or deleted, and no kernel is
registered. Notebook options and Python inheritance are retained; managed Cargo
and binary declarations are disabled in memory, so copying the consumer manifest
does not require a Rust toolchain or TOML text surgery.

`NotebookProject` exposes `root` for consumer input preparation, `artifacts` for
execution outputs, and `environment`. Its method
`execute(notebook, *, cwd=None, timeout=None, env=None) -> NotebookExecution`
uses the existing notebook engine and a fresh kernel for each call:

- `notebook` and `cwd` are `Path` objects relative to `root`, or absolute paths
  inside it. Links and escapes are rejected. Omitted `cwd` and `timeout` use
  notebook configuration; timeout must be a positive integer in seconds.
- `env` overlays kernel variables; `None` values remove them. The calling process
  environment is never changed. The selected interpreter/environment and private
  Jupyter/IPython/Matplotlib directories remain authoritative; `PYTHONHOME` and
  `PYTHONPATH` are removed and bytecode writes disabled for the kernel.
- Executed notebooks, schema-1 reports, and temporary kernel state stay in the
  fixture workspace. Configured `output-dir` is replaced by `artifacts`.
  Repeated execution of the same notebook replaces its previous artifacts.
- `NotebookExecution` exposes `source` (the copied input), `notebook_path`,
  `report_path`, `report` (the existing JSON report as a dictionary), and
  `returncode` (`0` passed, `1` failed). Cell errors and timeouts return reports;
  malformed inputs, environment failures, and publication errors raise, using
  the existing notebook/file exception contracts. Interrupts propagate.
- Context exit removes only the newly created workspace, including on an
  exception. Artifact paths then expire and further execution raises `ValueError`.

Notebook code is trusted executable code, not sandboxed. Consumer tests own any
files that code writes, its domain environment variables, scientific assertions,
and external side effects. Place intended test outputs beneath `project.root`.

The [README example](../README.md#consumer-integration-tests) replaces generic
notebook/project copying, `UV_PROJECT_ENVIRONMENT` mutation, Cargo-table string
slicing, and sidecar-path reconstruction. MCMC adoption should retain its
trace-root, output-destination, ACF, ESS, timing, and split-R-hat assertions;
modify only fixture copies when injecting scientific assertion cells. Consumer
adoption follows publication and does not follow automatically from upstream tests.

## Python Just inspection API

Targeting v0.1.8, `research_repo_tools.just_inspect` exports `Justfile`,
`dry_run`, and `inspect_justfile`. Both functions require an explicit `root: Path`
and accept keyword arguments `justfile=Path("justfile")`, `executable="just"`,
`env=None`, and `timeout=30`. Relative Justfile and explicit executable paths
resolve against `root`; bare executable names use `PATH`. `env` replaces the
child environment, with omission inheriting it; it never mutates the caller.
The timeout bounds each subprocess. The default selects the root's literal
`justfile`, with no implicit search into parent projects.

The executable must report the version of the installed `rust-just` dependency,
the existing shared Just authority. Inspection never installs or upgrades it.
Use the normal consumer environment or explicitly select its matching executable.

| Interface | Result |
| --- | --- |
| `dry_run(root, recipe, arguments=(), **options)` | `subprocess.CompletedProcess[str]` with native stdout/stderr and return code; arguments remain separate argv entries |
| `inspect_justfile(root, **options)` | `Justfile` snapshot with `recipes` and `aliases` dictionaries |

`recipes` maps root-level recipe names to native JSON metadata, including
parameter/default expressions, dependency records and body fragments. `aliases`
maps alias names to target names. Native expressions remain JSON values: these
helpers do not parse shell commands, infer argv from rendered text, or prescribe
consumer CI policy. Imported root recipes follow Just's dump; nested module
metadata is outside this small interface. Qualified module names can be passed
to `dry_run`. Treat returned records as snapshots, not extension constructors.

JSON object/recipe/alias shapes and parameter/dependency identity fields are
validated; ambiguous duplicate keys and non-finite numbers are rejected. Missing
files/directories, wrong versions and malformed metadata raise `ValueError`.
Executable lookup, OS launch failures, nonzero exits, timeouts and decoding
errors retain the [process API](#python-process-api) exceptions and
captured diagnostics. A failed Just evaluation never becomes an empty recipe map
or a successful preview.

Native Just remains the parser and evaluation authority. Dry-run skips recipe
bodies, but configuration/imports and expressions still matter: for example,
an absent variable referenced through `env()` fails during dry-run. This is not
a security sandbox for untrusted Justfiles. Commands are returned as native
text, normally on stderr, with explicit UTF-8 decoding and preserved newlines.

MCMC can replace its `_run_just` and `_recipes` boilerplate with these functions.
Keep assertions about CI dependencies, Rust features, scanner inventory,
credentials and review boundaries in the consumer. See the
[README example](../README.md#consumer-integration-tests).

## Python performance APIs

`research_repo_tools.archives`, `research_repo_tools.criterion`, and
`research_repo_tools.evidence` expose the names listed in the
[performance API contract](performance-api.md), starting with the release
containing them; published `0.1.2` does not contain them. These modules require
no plotting, numeric, or notebook dependencies. See the
[consumer examples](../README.md#performance-evidence) and
[retained-evidence migration](performance-migration.md).

`research_repo_tools.publication` and `research_repo_tools.publication_config`
provide byte-preserving document sections, deterministic timing tables/SVGs,
exact tagged-blob checks, and transactional publication plans. See the
[publication API contract](publication-api.md),
[consumer configuration](../README.md#document-publication), and
[publication migration](publication-migration.md). These also require the release
containing them after `0.1.2` and add no plotting dependencies.

## Python process API

The coordinated workflow additions, including `measurement`, `worktrees`,
`release_pairs`, `release_assets`, `legacy_evidence`, `performance_reports`,
`selection`, `validation`, and `ci`, are documented in
[configured workflow contracts](workflow-api.md).

The following imports from `research_repo_tools.process` are supported starting
with the release containing these APIs (they are absent from published `0.1.2`).
Pin that subsequent published package before migrating consumers. These functions
and the publication API below follow the same patch/minor compatibility policy
as the CLI. Type annotations are shipped through `py.typed`.

| Import | Contract |
| --- | --- |
| `ExecutableNotFoundError` | Executable discovery failed, before a child was launched |
| `cpu_description() -> str` | Available host CPU description, or `unavailable` without invented provenance |
| `format_exception_diagnostics(error, *, single_line=False) -> str` | Render command, timeout, or grouped publication failures; byte diagnostics use UTF-8 with replacement; text is human-readable, not a parsing contract |
| `resolve_executable(command, *, cwd=None, env=None) -> Path` | Resolve a name or explicit `str`/`Path` to an absolute executable path without executing it |
| `run_command(command, args=(), *, cwd=None, env=None, input=None, encoding="utf-8", errors="strict", timeout=300.0, check=True) -> CompletedProcess[str]` | Encode text stdin and decode captured stdout/stderr with the specified codec; preserve newlines on every platform |
| `run_command_bytes(command, args=(), *, cwd=None, env=None, input=None, timeout=300.0, check=True) -> CompletedProcess[bytes]` | Capture stdout/stderr and transport stdin without decoding or newline translation |
| `run_command_live(command, args=(), *, cwd=None, env=None, timeout=300.0, check=True) -> CompletedProcess` | Inherit stdin/stdout/stderr for trusted long-running commands; streams are not captured |
| `run_git_bytes(args, cwd=None, *, env=None, input=None, timeout=300.0, check=True) -> CompletedProcess[bytes]` | Byte execution with `git` resolved from the selected environment; Git retains responsibility for attributes, clean filters, and all configuration |

`args` is a sequence of strings, without the executable name. `cwd` is a `Path`
and defaults to the caller's current directory. Explicit relative executable
paths and relative `PATH` entries resolve against that directory. `env` is a
replacement mapping, not an overlay; omit it to inherit the environment. A missing
`PATH` uses `os.defpath`; an empty `PATH` searches the selected directory. Windows
extension discovery follows `shutil.which`, including the host process's
`PATHEXT`. Symlinks to executables are preserved so virtual-environment Python
and executable dispatchers keep their invocation identity. Discovery is not a
security boundary against executable replacement. Native Windows batch-file
execution retains the operating system's shell behavior; prefer native executables
for literal argument transport.

The runners request no shell. Capturing runners keep both output streams in memory;
the live runner inherits them and returns `None` for stdout/stderr. They do not
provide pipelines, redirection, or detached processes.
`input=None` inherits stdin; an empty string/byte string supplies an empty pipe.
Text input is encoded once, without platform newline conversion. Binary input
must be `bytes`; use `run_git_bytes` for Git blobs and filter-sensitive data.
Git options, mutating operations, and policy decisions remain the caller's choice.

`CompletedProcess` is the standard-library type; `args`, `returncode`, `stdout`,
and `stderr` are available. Capturing runners return both streams, possibly empty;
`run_command_live` returns `None` for both. With
`check=True`, a nonzero status raises `subprocess.CalledProcessError`; with
`check=False`, it is returned. Timeouts raise `subprocess.TimeoutExpired` and
terminate/reap the direct child, without promising descendant cleanup. Timeouts
must be positive and finite, or `None` for no deadline. OS launch errors propagate
as `OSError`. Invalid arguments raise `TypeError` or `ValueError`; invalid codecs
raise `LookupError`. Strict encoding/decoding errors raise `UnicodeError`.

Capturing runners retain raw captured bytes on checked failures and timeouts,
including the original argument vector and exit status/deadline. The live runner
does not capture output on failures or timeouts either; its exception stream
attributes are `None`. The text runner checks
failure before decoding, so invalid output cannot hide a command error. Successful
or unchecked text output is decoded using `encoding` and `errors`. Diagnostic
formatting does not redact arguments or output; consumers own sensitive-data
policy. See the [Python examples](../README.md#calling-from-python).

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
Release events, GitHub evidence and registry responses use strict UTF-8 JSON
parsing that rejects duplicate fields, non-finite numbers and excessive nesting.

`publish_reviewed_release(config, tag)` validates a stable draft twice and publishes
that GitHub Release as the invoking maintainer. Calling it constitutes approval;
the CLI additionally requires `--approve`. It never changes Git state or uploads
registry packages. Consumers retain native Cargo packaging/upload commands.
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

`research_repo_tools.releases` is supported starting with the release containing
these APIs; published `0.1.2` does not contain them. All paths passed as roots are
`Path` objects. Policies default to the consumer configuration when omitted.
These functions do not print or parse CLI output.

| Import | Contract |
| --- | --- |
| `apply_release(plan) -> ReleaseResult` | Publish a validated, unchanged plan using the file-publication API |
| `check_release(root, *, policy=None, previous_tag=None, adapter=None) -> ReleaseCheckResult` | Check shared metadata and consumer rules without writing source files |
| `discover_release(root, *, policy=None, adapter=None) -> ReleaseDiscovery` | Read package identity, selected files, and standard version references; validate explicit selector cardinality without GitHub access |
| `plan_release(root, tag, *, previous_tag=None, release_date=None, policy=None, adapter=None) -> ReleasePlan` | Prepare all shared, declarative, and adapter edits in an isolated tree and validate them before returning |
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
`OSError`/`ExceptionGroup`/`RecoveryError` contracts below. Diagnostic strings are
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

## Python file-publication API

`research_repo_tools.files.publish_directory(destination: Path)` is a context
manager yielding an empty private staging `Path` beside the destination.
Generate or copy the complete set and validate consumer assertions inside the
context. Exit publishes the generation, including an empty set, replacing all
old members. Generation failures leave the original untouched. Caught commit
failures restore it; incomplete rollback raises an exception group with the
original failure first and `RecoveryError.target` / `RecoveryError.backup`
identifying the retained original directory. All path components must be free of
symlinks/junctions; generated trees allow only regular files and directories,
with portable case/Unicode aliases rejected. Files are synced before publication.
New directories start owner-only; caller-created file modes are retained and
existing destination directory permission bits are preserved where supported.
Missing transaction-created parents are cleaned up when still empty. Cleanup
is best effort. Ownership/ACL metadata is not preserved. Callers must own the
whole destination exclusively: the two renames can briefly leave it absent,
and neither concurrent-writer serialization nor crash atomicity is promised.

`research_repo_tools.files.replace_many(updates: Mapping[Path, bytes], *, expected: Mapping[Path, bytes] | None = None) -> None`
publishes a mapping in iteration order. An empty mapping is a no-op. It is the
supported named-file publication entry point; pass one entry for one file.

- All targets and byte payloads are checked before staging or directory creation.
  Paths resolve relative to the caller's directory. Duplicate paths and
  ancestor/descendant targets raise `ValueError`. Case and Unicode normalization
  aliases are rejected on every platform, including when targets or parents do
  not exist yet. Leaf symlinks, including dangling symlinks, are rejected.
  Parent symlinks are resolved once before staging, including
  for duplicate/overlap checks. Existing non-regular targets raise
  `IsADirectoryError`. Invalid target or payload types raise `TypeError`.
- Missing parents are created. Every candidate and existing-file backup is written
  to an exclusive sibling temporary file and flushed with `fsync` before any target
  replacement. Each replacement uses the filesystem's atomic rename operation;
  readers can observe intermediate states across multiple files.
- Optional `expected` snapshots must cover every replacement target and may also
  guard unchanged input files. Paths follow the same validation rules; snapshots
  must be bytes. After staging, changed path resolutions or source bytes reject
  before the first replacement, preserving current files and cleaning staged
  artifacts. This optimistic guard does not serialize writers with the renames.
- New files use mode `0600` subject to the platform/umask. Existing permission bits
  are retained where supported; Windows only supports a subset of POSIX modes.
  Publication creates new inodes: ownership, ACLs, extended attributes, timestamps,
  and hard-link identity are not preserved. Distinct hard-linked paths are separate
  targets. Directory permissions follow the platform/umask.
- A caught publication failure restores previously replaced targets in reverse
  order and removes newly published files. Successful rollback re-raises the
  original exception unchanged. Staging failures leave targets unchanged. Empty
  directories created by a failed transaction are removed where possible.
- Incomplete rollback raises an `ExceptionGroup` (or `BaseExceptionGroup` for a
  base exception such as interruption). Its first member is the original failure;
  subsequent members are supported `research_repo_tools.files.RecoveryError`
  instances. Each exposes `target: Path`, `backup: Path | None`, and the underlying
  recovery failure as `__cause__`. A backup contains the original bytes and is
  retained for manual recovery. `backup=None` means removal of a newly created file
  failed. Exception-group splitting preserves these child exception objects.
- Temporary-file cleanup is best effort and reports `OSError` through logging,
  without masking the original failure. Recovery backups are excluded from cleanup.
  A cleanup warning after success does not mean publication failed.

Callers must serialize writers and prevent concurrent path/symlink changes.
This API is neither a filesystem sandbox nor a compare-and-swap operation.
It does not promise multi-file crash atomicity, directory `fsync`, power-loss
recovery, or automatic recovery after process termination. Replacing open files
can fail on Windows. Use the recovery paths in the exception before retrying.

Names not documented here or in the linked API contracts remain implementation
details; importing them creates an unsupported dependency. Configuration
factories above are supported, while their concrete settings types remain private.
The typing marker is not a stability promise for every importable symbol.

## External programs and platforms

Python 3.14+ is required. CI exercises Python 3.14 on Linux, macOS, and Windows,
including separate installations from the same wheel and source archive.
Later Python versions are allowed by metadata but are not yet in the test matrix.
Installed-package checks cover these public imports and representative CLI use
outside the source checkout.

uv is a hard prerequisite and must be available on PATH. The runtime dependency
`rust-just` supplies Just in the project environment. The explicit `setup` command
also installs a persistent user-level Just command through uv and configures PATH.
Recipes select the locked project environment without activation. Setup then
synchronizes Python dependencies with the declared managed Rust tools available.
Explicit toolchain synchronization installs pinned
Rust/Cargo and supported declared Cargo tools, including git-cliff and rumdl.
See [toolchain setup](INSTALLING.md) for declarations, host support, installation
ownership, and remaining native validation gates. Git is a system prerequisite;
Semgrep belongs in the consumer's Python dependencies. GitHub CLI is needed for
automatic discovery of a previous release; an explicit previous release permits
offline preparation. The optional `notebooks` extra supplies notebook validation,
cleanup, synchronization, and execution. Notebook Python linting also requires
consumer-declared Ruff and ty; see [the notebook contract](RUNNING_NOTEBOOKS.md).

## Shared Python adoption API

`research_repo_tools.python_baseline.baseline()` returns a frozen
`PythonBaseline(requirement, selected, package_version)`. The requirement and
package version come from installed distribution metadata; selected is the
package-owned development minor. `drift(root)` returns mirror/pin discrepancies
without changing files or environments. `check(root)` raises `ValueError` on
these discrepancies. Lower consumer Ruff/ty targets are preserved; targets newer
than the selected interpreter fail.

`python_adoption.plan_python_adoption(settings)` prepares an immutable
`PythonAdoptionPlan` with `changed_paths`, source byte snapshots, candidate
manifest/selector/lock bytes, selected groups and notebook-kernel intent.
It resolves and installs a private candidate before returning. Planning may
populate uv caches and download interpreters but does not modify consumer files
or `.venv`. `apply_python_adoption(plan)` rejects changed source snapshots, verifies
managed tools, publishes candidate declarations, recreates `.venv` at its final
path and installs the configured notebook kernel. Caught failures restore prior
files/environment; recovery failures expose retained backup paths. Run from the
standalone target package, outside the consumer environment. Exclude concurrent
writers; abrupt termination is outside the rollback guarantee. External local
path dependencies require separate migration planning.

Tool-version inheritance uses the same adoption API when
`toolchain.inherit-python-tools = true`, independently of `inherit-python`.
The installed `python-tools` extra's Requires-Dist metadata owns exact Ruff, ty,
and pytest versions. `toolchain python-tools-check` checks declarations, the lock,
and PATH executables without modification. Adoption retains explicitly supplied
configuration through candidate validation and final environment verification.
Profile helpers in `python_tools` are
private; use this CLI contract. See [tool-profile adoption and removal](../README.md#shared-python-tool-versions).

## Managed installation cleanup API

`research_repo_tools.toolchain_clean.plan_clean(settings, *, keep_roots=())`
returns a frozen `CleanPlan` with the current root, additional retention roots,
store, host, declaration byte snapshots and `removals`. Each `Removal` identifies
the installation kind, path, directory identity and optional manager command.
Planning inventories existing installations without installing or deleting them.
Private Python inventory requires the declared uv version.
Relative `keep_roots` resolve against the settings' consumer root, independently
of the caller's current directory.

`apply_clean(plan, settings)` recomputes the plan and rejects changed declarations,
installation identities or candidates before removing anything. Pass current
settings, including a freshly loaded standalone configuration if applicable.
It deletes owned Cargo/binary/Python directories and invokes private rustup
uninstall commands for Rust. Failures stop subsequent removals and report partial
progress; there is no rollback. Keep installers and other writers idle throughout
planning and applying; revalidation is not a concurrency lock. See the
[retention contract](INSTALLING.md#cleaning-obsolete-installations).

## Security and documentation scan API

`research_repo_tools.security` exports:

- `scan_osv(settings, lockfiles, *, output="target/security", configuration=None)`:
  explicit relative `uv.lock`/`Cargo.lock` paths from the shared inventory. Each
  produces a numbered native JSON/SARIF pair with verified source coverage.
- `scan_secrets(settings, *, output="target/security", configuration=None, exclude=())`:
  full reachable history plus current files; rejects shallow history and selected
  links/submodules. Native patch/binary/archive limitations remain applicable.
- `security_inventory(root, *, include=(), exclude=())`: sorted tracked/nonignored
  regular files, excluding standard generated/environment directories. Inclusion
  patterns are Git pathspecs and exclusions are case-sensitive POSIX globs.

Scans require declared, already installed exact managed binaries. They return
zero only for complete scans without findings, otherwise the first native
nonzero status, 1 for invalid successful reports/findings, or 124 on timeout.
Invalid arguments/preconditions raise `ValueError`; I/O and Git errors propagate.
Old OSV reports at the selected output names are removed before scanning. OSV
also removes all prior numbered JSON/SARIF reports in its output directory,
including symlinks, so smaller inventories leave no stale reports. OSV and
Semgrep reject output directories with symlinks or Windows
junctions in any path component before creating directories or removing reports.
OSV preserves unrelated files and symlink targets. Semgrep owns and replaces
its complete output directory through `files.publish_directory`. Native
schemas and locations are retained, with Gitleaks source excerpts/commit messages
redacted in addition to native detected-secret redaction. Report directories are
caller-owned outputs. These commands are not an assurance that unknown secrets,
ignored inputs or unsupported binary formats are detected.

`semgrep_docs.rust_blocks(path)` returns line-padded Rust fence source strings
from UTF-8 Markdown or line/block rustdoc comments. It does not execute code or
change the source. Hidden Rust lines remain code; unclosed selected fences raise
`ValueError`. Macro-generated documentation and `#[doc = ...]` are not extracted.

`semgrep_scan.scan(settings, *, include, exclude=(), output="target/security/semgrep",
rust_docs=False, batch_size=None, inline_suppressions=None, jobs=None,
report_category=None, report_layout=None, target_timeout=None)` runs the native
scanner once per bounded batch, generating JSON and SARIF in the same invocation.
Optional overrides follow `config.SemgrepSettings`: positive integer batch size,
jobs and target timeout; boolean inline suppressions; `aggregate` or `numbered`
layout; filename-safe report category. Defaults are 100 files, one job, 120
seconds per rule/target, disabled suppressions, aggregate layout and `semgrep`
category. Existing `semgrep.timeout` is the positive process/fixture timeout
(default 300 seconds). CLI flags use corresponding hyphenated names.

Both report formats must be fresh finite JSON and agree on active rule/path/line
findings. JSON must declare exactly the selected scanned inputs and no errors.
Explicit source files include tests normally ignored by Semgrep, while native
rule path filters remain active. Excluded fixtures are never selected.
Accepted SARIF suppressions and JSON `extra.is_ignored` findings are inactive
only when inline suppressions are enabled. Invalid pairs or process timeouts
commit an empty generation, removing stale members. Precondition, generation
and publication exceptions preserve/restore the previous generation. Valid
reports remain available when findings or native status make the gate fail.

Aggregate layout publishes `semgrep.json` and `semgrep.sarif`. JSON contains
combined `results`, `errors`, `paths.scanned` and full native JSON in `batches`.
SARIF merges one run with deduplicated rule descriptors and reindexed rule,
artifact and invocation references; conflicting metadata/descriptors reject
aggregation. Unrelated run metadata must agree across batches. The declared
category followed by `/` becomes `automationDetails.id`, leaving the run ID
empty so GitHub recognizes the category. Numbered layout publishes `N.json` /
`N.sarif` pairs per batch, retaining native metadata and assigning distinct
categories. Both layouts replace the whole owned output directory, superseding
0.1.7's per-file double launches and partial numbered-file cleanup.

`check_documentation_fixtures(settings)` adapts
annotated Markdown fixtures to the existing shared fixture checker, including
blocking mismatches; count-based expectations remain in a separate fixture gate.

## SARIF API

`sarif.SarifPolicy(drivers: Mapping[str, tuple[str, ...]], category_prefix="analysis")`
selects exact driver names and rule-ID prefixes. The mapping is frozen; empty
prefix tuples keep all rules for that driver. TOML `[sarif]` supports
`category-prefix` and a `drivers` table of name-to-prefix arrays (prefixed with
`tool.research-repo-tools` in pyproject.toml). At least one driver is required.

`sarif.transform(document: object, policy) -> tuple[SarifOutput, ...]` validates
the whole document before selection and returns serialized immutable outputs
with `filename`, `category` and UTF-8 byte `payload`. It requires finite JSON,
version 2.1.0, a runs array, named drivers, unique rule IDs, object result/message
shapes and consistent rule references. Missing results/rules mean empty arrays;
explicit null is invalid. Legacy `ruleId`/`ruleIndex` and nested `rule.id` /
`rule.index` are supported, including ID-only findings without descriptor tables.
Extension `toolComponent` rule references are rejected. Invocations, when
present, must report successful execution. This validates the transform's
supported subset rather than every optional SARIF schema field.

Namespace-selected rules are retained even without findings. Runs without rules
or results are omitted. Rule indices in retained results, descriptor relationships,
notification `associatedRule` metadata and invocation `ruleConfigurationOverrides`
are reindexed. Supplied rule IDs and indices must agree; metadata referencing removed indices
rejects the transform. Arbitrary `properties` are preserved. Other root/run
metadata and automation fields remain unchanged except `automationDetails.id`.
Categories include the prefix, driver slug, driver-name digest and occurrence
among all input runs, including empty ones. Repeated names and slug collisions
have distinct categories; ordering same-name runs defines their stable identity.
Each `automationDetails.id` is the category followed by `/`, leaving the run ID
empty for GitHub code scanning. `SarifOutput.category` and filenames do not
include that separator.

`sarif.split(source: Path, destination: Path, policy) -> tuple[SarifOutput, ...]`
parses duplicate-free strict UTF-8 JSON, transforms and serializes all outputs
before publishing a complete directory generation. Invalid input leaves the old
generation unchanged; empty output removes stale files. CLI `sarif split SOURCE
--output DIRECTORY` uses the configured policy. Optional `--github-output PATH`
calls `ci.export_environment` after publication, exporting `SARIF_DIRECTORY`,
`SARIF_HAS_UPLOADABLE_RUNS` and `SARIF_RUN_COUNT`. That append is a separate
operation: failure reports nonzero after the SARIF directory has committed.
