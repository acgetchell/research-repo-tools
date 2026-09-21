# Supported interfaces

The `0.1` series supports the `research-repo-tools` command, its documented TOML
configuration, packaged templates, and the Python APIs listed below. Consumers
should pin an exact released version in their uv development dependencies.
Patch releases preserve these contracts; a minor release may introduce a
documented breaking change while the package remains below `1.0`.

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

## Command line and configuration

The [README](../README.md#workflow-examples) contains runnable examples and
help commands for inspecting CLI arguments.

Place global `--root` and `--config` options before the command group. Configuration
defaults to `[tool.research-repo-tools]` in the consumer's `pyproject.toml`.
A standalone configuration uses unprefixed tables and `schema = 1`, as shown in
the [packaged template](../src/research_repo_tools/templates/research-repo-tools.toml).
Unknown settings fail. Relative paths and explicit executable paths resolve
against the consumer root; bare executable names use `PATH`.

Commands return zero on success. Validation failures and handled operational
errors return nonzero; argument errors return `2`. Help and version output are
successful exits. Diagnostics use stderr, while reports and generated content
use stdout. Human-readable diagnostics and progress messages are not a structured
machine API. Template output and extracted release notes are intended for reuse.

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
or output cleanup. Notebook execution publishes separate artifacts.
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

## Python process API

The following imports from `research_repo_tools.process` are supported starting
with the release containing these APIs (they are absent from published `0.1.2`).
Pin that subsequent published package before migrating consumers. These functions
and the publication API below follow the same patch/minor compatibility policy
as the CLI. Type annotations are shipped through `py.typed`.

| Import | Contract |
| --- | --- |
| `ExecutableNotFoundError` | Executable discovery failed, before a child was launched |
| `format_exception_diagnostics(error, *, single_line=False) -> str` | Render command, timeout, or grouped publication failures; byte diagnostics use UTF-8 with replacement; text is human-readable, not a parsing contract |
| `resolve_executable(command, *, cwd=None, env=None) -> Path` | Resolve a name or explicit `str`/`Path` to an absolute executable path without executing it |
| `run_command(command, args=(), *, cwd=None, env=None, input=None, encoding="utf-8", errors="strict", timeout=300.0, check=True) -> CompletedProcess[str]` | Encode text stdin and decode captured stdout/stderr with the specified codec; preserve newlines on every platform |
| `run_command_bytes(command, args=(), *, cwd=None, env=None, input=None, timeout=300.0, check=True) -> CompletedProcess[bytes]` | Capture stdout/stderr and transport stdin without decoding or newline translation |
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

The runners request no shell and always capture both output streams in memory.
They do not provide streaming, pipelines, redirection, or detached processes.
`input=None` inherits stdin; an empty string/byte string supplies an empty pipe.
Text input is encoded once, without platform newline conversion. Binary input
must be `bytes`; use `run_git_bytes` for Git blobs and filter-sensitive data.
Git options, mutating operations, and policy decisions remain the caller's choice.

`CompletedProcess` is the standard-library type; `args`, `returncode`, `stdout`,
and `stderr` are available. Both streams are present, possibly empty. With
`check=True`, a nonzero status raises `subprocess.CalledProcessError`; with
`check=False`, it is returned. Timeouts raise `subprocess.TimeoutExpired` and
terminate/reap the direct child, without promising descendant cleanup. Timeouts
must be positive and finite, or `None` for no deadline. OS launch errors propagate
as `OSError`. Invalid arguments raise `TypeError` or `ValueError`; invalid codecs
raise `LookupError`. Strict encoding/decoding errors raise `UnicodeError`.

Both runners retain raw captured bytes on checked failures and timeouts, including
the original argument vector and exit status/deadline. The text runner checks
failure before decoding, so invalid output cannot hide a command error. Successful
or unchecked text output is decoded using `encoding` and `errors`. Diagnostic
formatting does not redact arguments or output; consumers own sensitive-data
policy. See the [Python examples](../README.md#calling-from-python).

## Python file-publication API

`research_repo_tools.files.replace_many(updates: Mapping[Path, bytes]) -> None`
publishes a mapping in iteration order. An empty mapping is a no-op. It is the
single supported publication entry point; pass one entry for one file.

- All targets and byte payloads are checked before staging or directory creation.
  Paths resolve relative to the caller's directory. Duplicate resolved paths and
  ancestor/descendant targets raise `ValueError`. Leaf symlinks, including dangling
  symlinks, are rejected. Parent symlinks are resolved once before staging, including
  for duplicate/overlap checks. Existing non-regular targets raise
  `IsADirectoryError`. Invalid target or payload types raise `TypeError`.
- Missing parents are created. Every candidate and existing-file backup is written
  to an exclusive sibling temporary file and flushed with `fsync` before any target
  replacement. Each replacement uses the filesystem's atomic rename operation;
  readers can observe intermediate states across multiple files.
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

Other module functions, configuration objects, parsers, and constants remain
implementation details; importing them creates an unsupported dependency.
`__all__` in the process and files modules names only the supported library API.
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
