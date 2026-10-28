# Process and Just inspection APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Python Just inspection API](#python-just-inspection-api)
- [Python process API](#python-process-api)

## Python Just inspection API

`research_repo_tools.just_inspect` exports `Justfile`,
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

Use these functions to replace recipe-inspection boilerplate.
Keep assertions about CI dependencies, Rust features, scanner inventory,
credentials and review boundaries in the consumer. See the
[worked example](notebooks-api.md#consumer-integration-tests).

## Python process API

The coordinated workflow additions, including `measurement`, `worktrees`,
`release_pairs`, `release_assets`, `legacy_evidence`, `performance_reports`,
`selection`, `validation`, and `ci`, are documented in
[configured workflow contracts](workflow-api.md).

The following imports from `research_repo_tools.process` follow the same
patch/minor compatibility policy as the CLI and the
[file-publication API](files-api.md#python-file-publication-api).
Type annotations are shipped through `py.typed`.

| Import | Contract |
| --- | --- |
| `ExecutableNotFoundError` | Executable discovery failed, before a child was launched |
| `cpu_description() -> str` | Available host CPU description, or `unavailable` without invented provenance |
| `format_exception_diagnostics(error, *, single_line=False) -> str` | Render command, timeout, or grouped publication failures; byte diagnostics use UTF-8 with replacement; text is human-readable, not a parsing contract |
| `resolve_executable(command, *, cwd=None, env=None) -> Path` | Resolve a name or explicit `str`/`Path` to an absolute executable path without executing it |
| `run_command(command, args=(), *, cwd=None, env=None, input=None, encoding="utf-8", errors="strict", timeout=300.0, check=True) -> CompletedProcess[str]` | Encode text stdin and decode captured stdout/stderr with the specified codec; preserve newlines on every platform |
| `run_command_bytes(command, args=(), *, cwd=None, env=None, input=None, timeout=300.0, check=True) -> CompletedProcess[bytes]` | Capture stdout/stderr and transport stdin without decoding or newline translation |
| `run_command_live(command, args=(), *, cwd=None, env=None, timeout=300.0, check=True, stdout_markers=(), stderr_markers=()) -> CompletedProcess[bytes]` | Inherit streams by default; independent exact-byte stdout/stderr assertions echo output live; result streams are not captured |
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
the live runner inherits them by default and returns `None` for stdout/stderr. They do not
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
policy. See the [Python examples](#calling-from-python).

The release containing the [Cargo example runner](cargo-examples-api.md) adds
`stdout_markers: Sequence[bytes] = ()` to `run_command_live`. The additive #132
contract also provides `stderr_markers: Sequence[bytes] = ()`. Each selected
stream uses its own temporary spool, exact live forwarding and incremental
matching with bounded memory; a marker on the wrong stream cannot satisfy it.
Stdin and unasserted streams remain inherited; disk use scales with selected
output. This changes the selected streams' TTY/buffering behavior. Markers must be
nonempty bytes; invalid options raise `TypeError` before launch. Missing markers
raise `ValueError` after exit 0, including with `check=False`; nonzero exits and
timeouts retain precedence and their original process contract. No output is
retained in the result or exception. See the Cargo contract for detailed limits.

## Calling from Python

A consumer that needs a Python wrapper can invoke the same CLI contract without
a subprocess. For a script in the consumer's `scripts/` directory:

```python
from pathlib import Path

from research_repo_tools import __version__
from research_repo_tools.cli import main

root = Path(__file__).resolve().parents[1]
print(f"Using research-repo-tools {__version__}")
raise SystemExit(main(["--root", str(root), "release", "check"]))
```

The process and file APIs leave command policy and scientific decisions in the
consumer. Validate the installed replacement before removing local helpers:

```python
import subprocess
import sys
from pathlib import Path

from research_repo_tools.files import replace_many
from research_repo_tools.process import format_exception_diagnostics, run_command, run_git_bytes

root = Path(__file__).resolve().parents[1]
try:
    result = run_command(sys.executable, ["--version"], cwd=root, timeout=30)
    payload = (root / "results.json").read_bytes()
    # Git receives the original bytes and applies its own configured filters.
    digest = run_git_bytes(
        ["--no-pager", "hash-object", "--path=results.json", "--stdin"],
        cwd=root, input=payload,
    ).stdout.strip()
    replace_many({
        root / "reports/results.json": payload,
        root / "reports/results.hash": digest + b"\n",
    })
except (OSError, subprocess.SubprocessError, ExceptionGroup) as error:
    raise SystemExit(format_exception_diagnostics(error)) from error
```

Text execution uses explicit UTF-8 by default; byte execution preserves LF, CRLF,
and binary data. Publication stages every file first and rolls back caught
replacement failures. See [supported interfaces](api.md) for lookup behavior, typed
results/errors, recovery backups, concurrency limits, and API stability.
