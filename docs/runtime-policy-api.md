# Isolated runtime policy API

The v0.2.9 runtime-policy capability strengthens isolated Python execution by
selecting a complete, platform-specific graph from a dedicated virtual project.
It does not require equality with the consumer's development lock. Consumer
commands and migration examples belong in the
[README](../README.md#reproducible-isolated-release-runtimes).

`research_repo_tools.runtime_policy_build` exports:

- `resolve_runtime(directory, requirements, *, uv=None)`: create a new virtual
  project, canonical source policy and PyPI-only `uv.lock`. The trusted uv
  executable uses structured arguments, a separate cwd, a cleaned environment,
  the bootstrap Python and no source builds. Existing directories are rejected.
  Resolution failures retain the new project for inspection.
- `record_runtime_policy(root, destination, *, provider=None, build_directory=None)`:
  require a dedicated virtual project, current lock and complete `.venv` graph;
  authenticate locked registry identities and wheel bytes. Selected native
  packages must have current, authenticated local source-build receipts. Retain
  verified wheels, native receipts and logs, hash requirements and `runtime.json`
  in a new directory outside the project. Recheck input snapshots, installed
  bytes and native receipts at retention. A provider supplies registry bytes for
  deterministic tests; it does not override verification.
- `install_runtime(policy, directory, *, artifacts, uv=None)`: install into a new
  venv from retained wheels only, with hash enforcement and no source builds or
  dependency re-resolution, then verify the installed graph. The generated
  requirements come from the authenticated policy. Failed partial installations
  are retained for inspection; retry in a new directory.

`research_repo_tools.runtime_policy` exports:

- `parse_runtime_policy(payload, *, expected_sha256)`: check exact bytes against
  an independently trusted digest and return immutable `RuntimePolicy` evidence.
  The schema rejects unknown fields, ambiguous packages and inconsistent native
  provenance. `RuntimePolicy.payload` preserves original bytes; `.digest` hashes
  those bytes. Direct construction is unavailable.
- `runtime_requirements(policy)`: return exact package/version/hash requirements
  for an offline wheel installer.
- `verify_runtime(policy, *, environment, artifacts)`: inspect an inert venv
  without importing its packages. Require the exact selected distributions,
  compatible dependency/extra/marker closure, Python/platform, authenticated
  wheels, matching installed bytes and retained native evidence. Reobserve the
  installed inventory before returning the sorted package names.
- `verify_executing_runtime(policy, *, artifacts)`: require Python `-I -B`, a
  checker installed in the executing environment, and isolated import locations,
  then apply the same complete-graph verification to `sys.prefix`.

`isolated_release_runtime(settings, *, checkout=None, policy=None, artifacts=None)`
in `release_runtime` accepts the optional policy/artifact pair. Verification runs
before managed tool probes and before the caller's release command. Both must be
supplied together. The artifact bundle, executing installation and managed tools
remain outside the observed checkout. Omitting the pair retains the original
external-runtime contract and development-rehearsal behavior.

## Authentication and bootstrap trust

The trust root is the **independently approved manifest digest**, the bootstrap
Python/standard library, uv, and the installed verifier. Merely reading a hash
from the same untrusted download as its manifest establishes no authority.
The manifest records exact registry wheels verified when it was produced. It
also pins native wheel bytes and the receipt/log previously authenticated by the
producer's machine-local key. A receiving writer authenticates those retained
bytes through the approved manifest; it does not verify an HMAC with a copied key.
Receipts include producer context and paths, but the signing key is never exported.

Changing dedicated source/lock/build policy invalidates local build receipts.
Recording requires current evidence; missing or stale evidence stops it. A
receiving writer requires the receipt/log and their manifest-bound input and wheel
identities. Selecting a newer policy requires a newly authenticated digest;
an old approved manifest is not silently refreshed from current project files.

Python imports the verifier and its dependencies before this check can run.
Self-verification cannot establish the integrity of already executed bootstrap
code. Python startup hooks (`.pth`, `.start`, `sitecustomize` and `usercustomize`), cached bytecode, links, direct-source installations,
system site-packages, unexpected files and extra distributions are rejected.
Use a fresh venv without bytecode compilation and launch it with `-I -B`.
The target's `pyvenv.cfg` must explicitly disable system site-packages and identify
the selected Python version without duplicate settings. A shortened uv version
is accepted only when it equals the selected version or its `home` identifies
the already trusted executing base interpreter. Explicit conflicting patch versions
fail. Static inspection does not launch the target; its executable itself remains
part of bootstrap trust.
Installed source/native payloads and wheel `.data` payloads use the existing
artifact verifier; installer-generated entry-point wrappers, the interpreter,
standard library, and external managed tools retain their separate trust boundary.
Filesystem checks are observations, not an atomic snapshot or protection against
an attacker concurrently rewriting the trusted runtime. Do not mutate a runtime
or its approved bundle while a release operation uses it.

The policy is bounded to 256 packages, a 4 MiB manifest, 1 GiB of wheels and the
existing 512 MiB per-wheel/native-observation limit. Native receipts/logs and
individual installed metadata files read during discovery are bounded to 4 MiB.
This initial contract is PyPI-only, one dedicated virtual project, default runtime
requirements (including their requested extras), and one native Python/platform.
It does not export a universal cross-platform environment or a consumer development
workspace. Separate native policies supply Linux, macOS and Windows writers.

This policy neither grants release approval nor replaces fresh dependency and
registry checks, native validation, artifact/run authentication or publication
assertions. Actual consumer adoption and hosted bootstrap/job timings belong to
the consumer's adoption tracker after verified package publication.
