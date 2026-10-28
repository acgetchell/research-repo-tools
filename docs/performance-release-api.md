# Prepared performance release API

The `research_repo_tools.performance_release` module owns the independent
prepare → acquire/retain → README handoff. Consumer commands are in the
[task guide](RECORDING_PERFORMANCE.md#prepared-performance-releases). Scientific workloads,
acceptance, interpretation and deployment remain consumer decisions.

| Public interface | Contract |
| --- | --- |
| `PerformanceRelease` | Immutable repository, measurement/report/publication paths, ignored preparation path, raw output paths, selection mode/order and optional explicit pair |
| `Preparation` | Immutable independent release labels and full commits, source mode, package version, selection policy and measurement declaration SHA-256 |
| `load_performance_release(root, configuration)` | Strict schema-1 declaration with root-relative portable paths; rejects unknown fields and path aliases |
| `load_preparation(root, settings)` | Require a complete supported record and recheck local package/declaration/reference identity; never reads evidence to supply assertions |
| `load_readme_plan(root, settings, *, identities=None)` | Use preparation or all four explicit assertions with the existing retained-evidence publisher; no acquisition, promotion, network or Git mutation |
| `measure_release(root, settings, *, allow_git_mutations=False)` | Acquire the prepared pair, retain raw evidence, then promote the report; README is independent |
| `parse_preparation(payload)`, `serialize_preparation(record)` | Strict deterministic UTF-8 JSON using `research-repo-tools/performance-preparation/v1`; reject unsupported schemas, missing/extra/duplicate fields and invalid identities |
| `prepare_performance(root, settings, *, fetch_tags=False, allow_git_mutations=False)` | Independently select/resolve and atomically save all assertions; no acquisition or README publication |
| `select_measurement_pair(root, configuration, *, repository=None, mode="infer-release", order="published", current=None, baseline=None)` | Common direct/prepared selection, returning `(ReleasePair, working_tree)` with the measurement release floor enforced |

The schema-1 workflow declaration is illustrated by the packaged
`performance-release.toml`. Required fields are `schema`, `repository`,
`measurement`, `report`, `publication`, `preparation`, `payload`, and `manifest`.
Optional `mode` selects `infer-release` (default), `explicit`, or
`current-vs-latest`; explicit mode requires both `current` and `baseline`.
`order` is `published` by default or explicitly `version`.

Preparation uses the same package/publication chronology as direct measurement.
Prospective packages resolve HEAD; published and explicit pairs resolve their
actual local tags. Existing tags are never silently replaced. Optional fetching
requires both `--fetch-tags` and `--allow-git-mutations`, fetches tags from the
declared GitHub repository, and may update refs even when later preparation
fails. The default uses already reviewed local refs. A missing tag needs an
explicit authorized fetch or other maintainer repair; no fallback occurs.

Preparation storage must be untracked and ignored by Git. Its write is staged
and identity rechecked before replacement. Failed preparation preserves any prior
record and raw measurements. Records are assertions, not authentication tokens;
keep them under the same trusted writer control as release declarations.
Measurement binds the selected refs and declaration, including source/controller
freshness checks inherited from direct acquisition. The underlying common
`performance_acquisition.measure_and_retain` API exposes the same guarded raw
publication for callers that already have an independently selected pair.

README retries need no shell variables or provider queries. Prepared publication
rechecks HEAD/tags, package version, declaration bytes and the saved record at
planning and publication, in addition to the publisher's source/harness,
references, archived-evidence and rendering checks. A newly present prospective
tag must identify captured HEAD. Missing or stale preparation fails before
publication; recapture reviewed assertions without rerunning measurements when
the retained evidence still satisfies the new assertions. Explicit four-assertion
publication remains available through both `performance readme` and the existing
`performance publish` interface. Formatting/README configuration repairs do not
invalidate independent preparation.

Report-promotion failure leaves successful raw acquisition available at the
declared payload/manifest paths. Retry `performance promote` with those explicit
paths. Calling `performance release` deliberately starts acquisition again;
README publication never does. Select new raw paths to keep multiple acquisitions.

## First-use report inventory

`research_repo_tools.report_inventory.check_report(root, configuration)` returns
`ReportCheck(status, changed_paths)`, with `status` equal to `uninitialized`,
`current`, or `stale`. `performance promote CONFIG --check` uses this API when
no new payload/manifest pair is supplied. Explicit inputs never fall back.

Both report schemas accept an optional `[initialization]` table containing
`allow-empty`, `directories`, and `guards`. The archive is always inventoried;
`directories` adds disjoint output trees and `guards` adds exact companion paths
outside those trees. No suffix is exempt: any archive/output entry, even an empty
partial run directory, defeats first use when the current report is missing.
Empty declared root directories may exist. Companion plans, raw payloads,
manifests, CSVs, figures, index/pointer files and archived reports all count.
Links/junctions, unsafe portable paths and case/Unicode aliases fail. Configuration
and source prose/measurement declarations must remain valid.

Without explicit `allow-empty = true`, missing reports still fail. Initialized
inventories use the existing strict report planner, evidence/provenance checks,
immutable history and render comparison. Checks create nothing, never acquire
measurements, and never alter history or README. The CLI prints the empty status
explicitly and returns nonzero for stale reports or rejected inventories.
