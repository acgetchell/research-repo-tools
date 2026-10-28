# Verifying release evidence and attachments

Use this guide when composing trusted preflight and writer jobs. Follow your
consumer's adapted release procedure, using the
[packaged release template](../src/research_repo_tools/templates/RELEASING.md)
for the overall order and approvals. These
interfaces require a verified published v0.2.4 package. Exact-release-commit
evidence is the default; [CI reuse](REUSING_CI.md) is a separate explicit policy.

## Release evidence and attachment handoffs

Local builds and merged source do not establish package availability.
Install the verified supplying release before composing these jobs.

The packaged `release-evidence.toml` declares exact repository/workflow/native
job/step/signer policy; `release-assets.toml` declares repository and optional
release-title policy. Merge the `release-evidence` and `release-assets` recipes.
Keep the trusted installed runtime and policy separate from the source checkout
in release workflows. The observed checkout must be clean at the independently
asserted commit and package version.

```sh
just release-evidence verify tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --output /trusted/selection.json
# After approval, before token acquisition:
just release-evidence verify tooling/release-evidence.toml \
  --checkout /path/to/source --commit FULL_COMMIT --version 1.2.3 \
  --expected /trusted/selection.json --output /trusted/revalidated.json
just release-assets preflight tooling/release-assets.toml \
  --checkout /path/to/source --tag v1.2.3 --commit FULL_COMMIT \
  --output /trusted/target.json
just release-assets upload tooling/release-assets.toml \
  --checkout /path/to/source --tag v1.2.3 --commit FULL_COMMIT \
  --target /trusted/target.json --asset /path/to/baseline.tar.gz --approve
```

For Actions, append `--github-output "$GITHUB_OUTPUT"` to evidence verification
or asset preflight. It requires an existing writable command file; normal and
quiet Just execution produce the same evidence. Evidence records bind the exact
run, attempt, jobs, artifact and digest/signature policy. Existing output records
can only be reused with identical bytes; use new paths for changed selections.

`release-evidence create` produces the manifest only in the matching trusted
push/run/attempt. Cargo evidence also consumes the independently retained archive
and tool identity JSON; signing, Cargo gates and archive reproduction stay in the
workflow. `release-assets upload` reuses identical existing attachments, rejects
conflicting bytes, and publishes the draft only with a separate `--publish` flag.
Neither interface acquires a registry token or runs scientific workloads. See the
[release evidence API](release-evidence-api.md) and
[migration map](performance-migration.md#release-interface-reconciliation).

## Trusted release target handoff

Install the same exact published pin in trusted preflight and writer jobs. With the installed console command
on PATH, preflight
captures the expected workflow commit and a title-equals-tag assertion:

```sh
research-repo-tools --root "$RUNNER_TEMP" performance release-draft \
  "$GITHUB_REPOSITORY" "$RELEASE_TAG" --commit "$GITHUB_SHA" \
  --expected-title "$RELEASE_TAG" --output "$RUNNER_TEMP/release-target.json"
```

Omitting `--output` prints only target JSON on stdout. Pass this target through a
consumer-controlled trusted job output or artifact, separately from benchmark
output. The benchmark job retains read-only permissions and owns its correctness
checks and archive format. The final writer reads the original trusted target and
inert asset, then explicitly requests publication:

```sh
research-repo-tools --root "$RUNNER_TEMP" performance release-upload \
  "$GITHUB_REPOSITORY" "$RELEASE_TAG" "$RELEASE_ASSET" \
  --target "$RUNNER_TEMP/release-target.json" --publish
```

Without `--publish`, attachment and verification leave the draft unpublished.
Without `--target`, the existing upload command captures a fresh target inside
that invocation and cannot bind an earlier benchmark run. The old `release-draft`
command without `--commit` still checks draft state and emits no target. Output
and title assertions require `--commit`. These commands require authenticated
`gh`; consumers own token permissions, workflow/ref policy and fresh-preflight
run-attempt checks. JSON parsing alone does not authenticate the handoff.

The same supported Python API can run in a trusted job containing only inert files:

```python
from pathlib import Path
from research_repo_tools.release_assets import (
    parse_release_target, preflight_release_target, publish_release_asset,
    serialize_release_target,
)

root = Path("/trusted/job")  # Choose the native job directory on each platform.
target = preflight_release_target(root, "example/project", "v1.2.3",
                                 "a" * 40, expected_title="v1.2.3")
(root / "release-target.json").write_bytes(serialize_release_target(target))
# After the consumer's trusted handoff and benchmark gates:
captured = parse_release_target((root / "release-target.json").read_bytes())
publish_release_asset(root, captured.repository, captured.tag,
                      root / "baseline.tar.gz", target=captured, publish=True)
```

An invalid or unavailable publication response reports an unknown outcome.
Inspect the captured release ID and attached asset before retrying; no rollback
or automatic publication retry occurs. The [API contract](workflow-api.md#release-assets)
details validation, retry guarantees and the limits of client-side rechecks.
The [la-stack deletion map](performance-migration.md#captured-release-target-adoption)
keeps downstream migration tied to an exact published package and integration checks.

Only when no local executable modules remain and the consumer neither installs
nor distributes its own Python package, remove its build backend, console scripts
and self-referencing notebook extra. Set `[tool.uv] package=false`,
retain exact shared pins in tooling/notebook dependency groups, refresh the lock,
and remove local wheel/entry-point tests. Rust/scientific and configuration tests
remain. Declare `release.tag-policy="canonical-stable"` where `vX.Y.Z` is required;
use `just release-update TAG PREVIOUS DATE` for offline release preparation.
Version-independent documentation examples avoid release-update callbacks.

## Release benchmark jobs

Release benchmarks use three jobs: validate a mutable draft, measure a checked-out
tag with read-only permissions, then attach/publish inert bytes in a separate
writer job. The measurement recipe invokes `performance baseline benchmark.toml
v1.2.0 project-v1.2.0-baseline.tar.gz`; it requires HEAD and configured source bytes
to match the tag. Writer jobs install the exact published package and use the
[captured release target](#trusted-release-target-handoff) to attach and publish
without checking out benchmark code. Identical completed attachment retries are
accepted; conflicting bytes or incomplete provider metadata fail.
