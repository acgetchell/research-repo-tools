# File selection and publication APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Raw text API](#raw-text-api)
- [Python file-publication API](#python-file-publication-api)

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
it is separate from this new strict raw gate.

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
details; importing them creates an unsupported dependency. The
[configuration factories](configuration-api.md#python-configuration-api) are
supported, while their concrete settings types remain private.
The typing marker is not a stability promise for every importable symbol.
