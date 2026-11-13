# Native path identity

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

## Boundaries and failures

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

## Versioned framing

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
