# Toolchain adoption and cleanup APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Shared Python adoption API](#shared-python-adoption-api)
- [Managed installation cleanup API](#managed-installation-cleanup-api)

## Shared Python adoption API

The packaged `python-bootstrap.py VERSION --dry-run|--apply` is a standalone
standard-library-only entrypoint for an older consumer interpreter. It reads the
exact target release's PyPI `Requires-Python` and passes it to isolated managed uvx
startup. `--metadata-file PATH` accepts saved exact-release JSON for offline/local
artifact evaluation; uv configuration such as `UV_FIND_LINKS` locates the artifact.
The installed distribution independently owns support checks and adoption.

`research_repo_tools.python_baseline.baseline()` returns a frozen
`PythonBaseline(requirement, selected, package_version)`. The requirement and
package version come from installed distribution metadata; selected is the
package-owned development minor. `minimum_requirement` extracts the published
lower bound independently of that selector. `reconcile_requirement(requirement,
authority)` adds the floor while preserving all consumer restrictions and rejects
empty intersections. `check_minimum(root)` enforces application metadata without
an opt-in or mutation; normal Python/toolchain/setup gates call it.
`inherit-python` only selects development mirrors. `drift(root)` returns
minimum/mirror/pin discrepancies
without changing files or environments. `check(root)` raises `ValueError` on
these discrepancies. Lower consumer Ruff/ty targets are preserved; targets newer
than the selected interpreter fail.

`python_adoption.plan_python_adoption(settings)` prepares an immutable
`PythonAdoptionPlan` with `changed_paths`, source byte snapshots, candidate
manifest/selector/lock bytes, selected groups and notebook-kernel intent.
It resolves and installs a private candidate before returning. Adoption can
run without either inheritance setting. It reconciles application metadata for
both installable and dependency-only projects, all exact shared-package pins and
extras, tooling-group restrictions, the interpreter selector and lock.
Without development mirroring it preserves compatible local selectors; obsolete
selectors advance to the shared selection. Conflicting application or group
ranges fail before publication. Planning may
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
private; use this CLI contract. See [tool-profile adoption and removal](UPDATING_DEPENDENCIES.md#shared-python-tool-versions).

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
