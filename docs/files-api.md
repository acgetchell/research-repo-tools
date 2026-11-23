# File selection and publication APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Native path observations](#native-path-observations)
- [Raw text API](#raw-text-api)
- [Python file-publication API](#python-file-publication-api)

## Native path observations

`research_repo_tools.path_identity` observes an explicit inventory without Git
selection, recursion, imports from the observed tree, or filesystem writes.
It is separate from `evidence.fingerprint_files`: that helper still requires
regular files, rejects links and aliases, and retains its existing digest format.
See [release availability](../README.md#release-availability) before adoption.

```python
from pathlib import Path

from research_repo_tools.path_identity import fingerprint_paths, observe_paths

inventory = observe_paths(Path("/absolute/checkout"), [b"script.py", b"removed.py"])
digest = fingerprint_paths(inventory)
```

`observe_paths(root, paths, *, max_file_bytes=67108864,
max_total_bytes=268435456, max_paths=100000) -> PathInventory` accepts an explicit
sequence of relative `bytes`, `str`, or `Path` names. Bytes retain the native
filename representation. Text and paths use `os.fsencode`; no Unicode
normalization, newline conversion, Git filters or content decoding is applied.
Empty inventories are supported. Budgets are nonnegative integers and count
regular-file content plus symlink target bytes. Oversized regular files are
rejected before reading; reads also enforce the budget if a file grows.

Each frozen `PathObservation(name, value)` has a relative byte name and one of:

| Value | Observed fact |
| --- | --- |
| `MissingPath()` | The leaf or an ancestor was absent. Permission and other I/O errors are failures, never absence. |
| `RegularFile(content, executable)` | Exact bytes and a Boolean executable flag. |
| `Symlink(target)` | `os.fsencode(os.readlink(path))`, with no target traversal or normalization; dangling links are included. |

The public `PathValue` alias is the union of these alternatives. Direct
construction and dataclass replacement validate the models. `PathInventory`
copies observations into an immutable tuple, rejects duplicate names, and sorts
by raw name. It carries one explicit `NativePathPolicy`:

- `POSIX` preserves filename bytes and defines executable as any `0o111` mode
  bit. Other permission bits, owners and timestamps are not fingerprint inputs.
  Newlines and undecodable names are supported where the native filesystem
  permits them. For example, APFS can reject invalid UTF-8 names even though
  Linux filesystems accept them.
- `WINDOWS` requires UTF-8 names, stores slash separators, rejects drive, UNC,
  alternate-stream and reserved Windows names, and rejects case duplicates.
  Executable is always false: neither `PATHEXT` nor ACL execution permission is
  inferred from POSIX permission bits. Symlink target bytes preserve the native
  `readlink` representation, including any extended-path prefix. Creating
  symlink fixtures can require Windows developer mode or symlink privileges.

The policy is part of the fingerprint. Cross-platform observations are not
implicitly interchangeable, even when their file contents match.

### Boundaries and failures

The caller-selected root is resolved once and must be an existing directory.
Every selected name must be relative, without NUL, traversal, or empty path
components. Links and junctions below that root cannot be traversed as parents.
A leaf symlink records its target, including an outside-root target, without
reading it. Junctions, directories, devices, sockets and FIFOs are unsupported
leaves. Existing inode aliases and duplicate native path spellings are rejected.
No case folding is imposed on distinct POSIX filename bytes.

Submodule selection remains caller-owned: a submodule directory is an
unsupported leaf, and the primitive does not discover Git boundaries. A caller
that deliberately selects files inside a submodule must own that separate
inventory and provenance. This API does not construct a repository source
triple or decide whether review/check evidence can be reused.

`PathObservationError` exposes `problem: PathProblem` and `path: bytes`.
Categories distinguish invalid inventories, unsupported kinds, unreadable
inputs, observed concurrent changes, and exceeded limits. Original I/O failures
are chained. Invalid model construction or argument types raise `ValueError` or
`TypeError`; an unavailable root can also raise its native filesystem error.

The observer compares ancestry and leaf metadata before and after reads, checks
the opened regular-file descriptor before reading, and rechecks all selected
paths at the end. It detects ordinary concurrent changes; it does **not** provide
an atomic snapshot, protection from hostile change-and-restore races, or a
filesystem sandbox. Stop writers when stable observation matters. Fingerprints
provide identity, not authentication, and cannot prove inventory completeness.

### Versioned framing

`fingerprint_paths(inventory) -> str` returns a lowercase SHA-256 hex digest.
The v1 stream begins with `research-repo-tools/path-observations/v1` and a NUL.
Every framed field uses an unsigned 8-byte big-endian byte length followed by
those bytes. First frame the ASCII native-policy value, then write the number of
observations as an unsigned 8-byte big-endian integer. For each sorted name:

1. Frame the raw name.
2. Frame `missing`, `regular`, or `symlink`.
3. For `regular`, frame `1` or `0` for executable, then frame exact file bytes.
4. For `symlink`, frame the native target bytes. Missing paths have no payload.

The policy values are `posix-bytes-executable-v1` and
`windows-utf8-no-executable-v1`. Kind tags and explicit lengths distinguish
absence from literal `missing` content, file bytes from link targets, and
executable-mode changes. Consumers adopting this format must version their own
envelopes rather than silently replacing historical wire identities.

## Raw text API

`files check-lines` reads optional `text.line-limit`, `text.include` and
`text.exclude` configuration; the positive limit is required there or through
`--limit`. Supplied CLI options replace their configured lists/value.
Public imports from `research_repo_tools.text_lines`:

- `check_lines(root: Path, *, limit: int, include=(), exclude=()) -> LineCheck`
  checks shared tracked/nonignored selection. Includes are Git pathspecs and
  excludes are case-sensitive POSIX globs. Discovery/path/read/decode errors
  raise; an empty selection returns `LineCheck(0, ())`.
- `inspect_lines(path: Path, *, limit: int, label=None) -> tuple[LineViolation, ...]`
  checks an explicit UTF-8 file without Git selection.
- `LineCheck.files` is the file count; `.violations` contains immutable records
  with `path`, one-based `line`, `length`, `limit` and the documented diagnostic
  string. Limits must be positive integers, not bools.

Every raw line counts, including fences, tables, URLs and trailing whitespace.
Counts use Unicode code points; tabs and combining marks count individually.
Physical LF/CRLF/CR endings are removed, including on a final line without a
newline; Unicode separators remain characters. The APIs never write files.
The CLI returns 1 for violations/errors and 0 for clean/empty selections.
The established `docs check-lines` Markdown policy retains its table exemption;
it is separate from the strict raw gate.

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

`research_repo_tools.files.replace_many(updates: Mapping[Path, bytes], *,
expected: Mapping[Path, bytes] | None = None, verify: Callable[[], None] | None = None) -> None`
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
- Optional `verify` runs synchronously once after staging and snapshot checks,
  before the first replacement. It receives no arguments and may raise to abort
  publication; staged files and newly created empty parents are then cleaned up.
  It does not run for an empty update mapping. Keep the callback read-only:
  publication rollback does not undo effects performed by the callback itself.
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
details; importing them creates an unsupported dependency. The
[configuration factories](configuration-api.md#python-configuration-api) are
supported, while their concrete settings types remain private.
The typing marker is not a stability promise for every importable symbol.
