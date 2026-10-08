# Cargo example runner contract

The release containing this capability adds `validation cargo-examples CONFIG`
and `research_repo_tools.cargo_examples`. Consumers must pin the exact published
release containing it before adoption. Implementation of issue #71 does not
publish a release or perform consumer rollout. The existing `validation run`
contract remains supported. See the [consumer example](../README.md#cargo-examples)
and [migration guidance](cargo-examples-migration.md).

## CLI and discovery

`research-repo-tools --root ROOT validation cargo-examples CONFIG` reads trusted
TOML relative to the consumer root. Configuration paths follow the shared
root-contained path contract. Cargo, its native toolchain, a current lockfile,
and dependencies needed for the build must already be available. No tools are
installed and no lockfile update is requested.

Discovery runs `cargo metadata --locked --no-deps --format-version=1` in ROOT.
Select exactly one workspace package by name with `package`; omission requires
exactly one workspace member. This is deliberately explicit for virtual and
multi-package workspaces, independent of Cargo's default-member selection.
Only executable examples (`kind = ["example"]`, `crate_types = ["bin"]`) are
supported. Library examples are excluded. Metadata discovers flat, nested and
explicitly declared targets without filesystem globbing or source parsing.
Required features are reported but never automatically enabled.

Empty, duplicate, malformed or ambiguous discovery fails. All configuration is
parsed before discovery; unknown selected/excluded/policy target names and an
empty final selection fail before building or executing examples. New discovered
targets join the default selection automatically. Explicit `include` narrows that
selection; consumer coverage tests own any requirement to exercise every target.

## Configuration

The root table requires integer `schema = 1`. Unknown fields are errors.

| Root key | Contract and default |
| --- | --- |
| `build-timeout` | Positive integer seconds per Cargo build; 1800 |
| `examples` | Table keyed by discovered target name, containing overrides below; empty |
| `exclude` | Unique known target names removed from selection; empty |
| `include` | Nonempty unique known target names; omitted means all discovered binary examples |
| `metadata-timeout` | Positive integer seconds for Cargo metadata; 300 |
| `package` | Exact workspace package name; omitted requires one member |
| `profile` | Cargo build profile; `release`; `dev` selects a development build |
| `timeout` | Positive integer seconds per example execution; 300 |

| Per-example key | Contract and default |
| --- | --- |
| `args` | Literal argument array, including empty strings and spaces; empty; no shell expansion |
| `expect` | Nonempty literal UTF-8 stdout marker strings; empty array means inherited stdout |
| `features` | Unique feature names, each in its own string; empty; names cannot contain commas or whitespace |
| `no-default-features` | Boolean; false |
| `timeout` | Positive integer execution budget, overriding root `timeout` |

Arguments and markers cannot contain NUL. Markers may contain whitespace and
newlines; byte matching is exact, including LF versus CRLF. Target selection and
feature names must be nonempty UTF-8 strings without surrounding whitespace or
controls. Include/exclude arrays describe sets, not execution order. Policies for
discovered but unselected examples are validated but do not execute.

## Build and execution order

Selected examples without nonempty `features` or `no-default-features = true`
are ordinary examples. One locked `cargo build --examples` builds the selected
package with its default features and configured profile. Cargo may also compile
unselected targets and default stubs in that build; only selected ordinary
examples execute. If no ordinary examples are selected, this build is omitted.

Compiler-artifact JSON supplies each executable's native absolute path, respecting
Cargo target directories, profiles and Windows suffixes. Missing, duplicate or
malformed selected executable records fail; an artifact omitted because of
`required-features` needs an explicit consumer feature override. No conventional
`target/release/examples/NAME` path is guessed or reused from stale files.

Ordinary examples run by target name in lexicographic order. Then each selected
feature override is built with `--example NAME`, its own features/default-feature
policy, and the same profile, and executed immediately before any later build.
Overrides run by target name too. This prevents shared artifact paths from being
executed with features from another build. Default features remain enabled unless
the consumer explicitly disables them. A diagnostics example whose default build
is a stub therefore needs a feature override even when metadata has no
`required-features` entry.

The runner inherits the caller's environment and runs all commands in ROOT.
Native host execution is supported on Linux, macOS and Windows. Consumers must
not select a cross-compilation target whose binaries cannot execute on that host;
Cargo target runners, emulation and shell wrappers are not used. The budgets apply
separately to metadata, each build and each execution, not to the complete run.

## Live streams and assertions

Without `expect`, binaries inherit stdin, stdout and stderr directly, retaining
terminal detection and native output. With `expect`, stdin and stderr remain
inherited; stdout is spooled to a private temporary file and its exact bytes are
echoed to the caller's stdout while the binary runs. A disposable forwarding
process keeps a blocked stdout sink from blocking the parent's deadline. After
completion, incremental literal matching uses bounded memory and handles markers
split across chunks. Stderr cannot satisfy
a stdout assertion. Progress messages go to stderr.

Assertion mode changes the child's stdout from a terminal to a regular file, so
programs may buffer differently. Programs needing immediate progress must flush
stdout themselves. The spool uses disk proportional to stdout volume until the
command ends, then is removed. On Windows, a descendant that retains inherited
stdout can delay deletion until its last spool handle closes; this does not delay
the direct child's result or replace its failure. Consumers own output volume and budgets. There is
no guarantee of relative ordering between the separately inherited stderr and
forwarded stdout. Matching performs no Unicode or newline normalization and
makes no scientific judgment: marker choice and validity remain consumer policy.
The runner does not flush output already buffered by its caller; callers own that
buffering, as with inherited streams.

Both modes fail fast on command errors, deadline expiry or missing markers. A
command error or timeout takes precedence over absent markers. No later example
runs after failure. Successful output is never replayed a second time at exit.
The CLI follows the package's status convention: 0 on success, 1 for operational
or assertion failure, 2 for argument syntax errors. Diagnostics retain original
command exit codes and timeout budgets. Python exceptions retain the process
API's native statuses. Timeout cleanup terminates and reaps the direct child;
it does not promise descendant cleanup. Consumers needing process-tree isolation
must provide it externally.

## Python API

These supported imports follow the [public compatibility policy](api.md):

| Import from `research_repo_tools.cargo_examples` | Contract |
| --- | --- |
| `CargoExample` | Frozen discovery record: `package: str`, `package_id: str`, `name: str`, `source: Path`, `required_features: tuple[str, ...]` |
| `discover_examples(root: Path, *, package: str \| None = None, timeout: int = 300) -> tuple[CargoExample, ...]` | Perform metadata discovery only, returning targets sorted by name; no build or execution |
| `run_examples(root: Path, configuration: str) -> None` | Load consumer policy and discover, build and execute as above |

Discovery does not inspect scientific source, prove complete consumer coverage or
infer the features required to avoid stubs. Returned records report Cargo data;
constructing a record yourself is not a validation operation. Unknown future
metadata fields are tolerated, while the fields used for discovery are checked.

Both functions raise `ValueError` for malformed configuration/discovery/artifacts,
`ExecutableNotFoundError` for missing commands, and standard process or OS errors
for execution failures. The existing
[`run_command_live`](api.md#python-process-api) also accepts
`stdout_markers: Sequence[bytes] = ()`. Nonempty markers enable the same streaming
assertion mode, with exact binary matching and no captured output in its result
or exceptions. Invalid marker arguments raise `TypeError` before launch; absent
markers raise `ValueError` only after a successful command, including when
`check=False`. An unchecked nonzero exit is returned without asserting markers.
