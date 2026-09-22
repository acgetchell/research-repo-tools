# Document publication migration

The [publication API](publication-api.md) requires a published release after
`0.1.2` containing it. Implementation, release, and downstream migration are
separate checkpoints. Keep the consumer's retained schemas and original evidence
bytes throughout adoption; follow the
[performance migration contract](performance-migration.md).

## Shared mechanics and consumer policy

Move marker validation, byte-preserving section replacement, candidate snapshots,
reference assertions, deterministic timing tables/SVGs, local tagged-blob checks,
and multi-file publication into shared calls. Consumers retain benchmark selection,
scientific conclusions, eligibility rules, release/measurement choices, source
inventories and historical layout declarations. Shared configured workflows own
discovery, measurement, provenance capture and conversion mechanics. No consumer
name selects behavior.

MCMC's `publish_performance_readme.py` is replaced by publication TOML and separate
scientific prose. Convert historical input to shared evidence at new paths;
preserve historical tables/SVGs as immutable files without retaining their writers.
For a consumer with additional scientific rendering requirements, after checking the
artifact/report release pair and current package release, pass the original CSV,
JSON provenance, report, and package metadata bytes as `inputs` to
`plan_publication`. Use `MarkerPair` for the existing PERFORMANCE delimiters,
`figures` for candidate SVG bytes, and fixed-value `ReleaseRule` assertions for
active references. Call `publish_publication` once for all outputs.

The supported adapter structure is:

```python
from research_repo_tools.publication import GitCheck, MarkerPair, plan_publication


def plan_validated_report(root, original_inputs, section, svg_path, svg_bytes, tag):
    # The consumer schema adapter has already checked payload integrity,
    # release/report consistency, source/harness identity, and eligibility.
    linked_artifacts = ("docs/PERFORMANCE.md", "evidence/report.csv", "evidence/report.json", svg_path)
    return plan_publication(
        root, "README.md", MarkerPair("<!-- PERFORMANCE:BEGIN -->", "<!-- PERFORMANCE:END -->"),
        section, inputs=original_inputs, figures={svg_path: svg_bytes},
        git_checks=(GitCheck(tag, linked_artifacts),),
    )
```

Snapshot inputs before parsing them, and validate those same bytes rather than
rereading a file after validation to populate `inputs`. This lets the planner
reject changes between parsing, rendering, preview, and publication. Include
all files that affect consumer validation, not only the table payload. The
shared API's file checks do not replace domain/schema validation.

For new shared-envelope evidence, the configuration adapter can replace this
Python wrapper. Record baseline/current releases under provenance `context.release`,
maintain independent revision and optional context/digest expectations in the
publication TOML, and configure all three package/report reference selectors.
The [README](../README.md#document-publication) contains a complete example.

## Intentional contract changes

The default tagged publisher requires an existing local tag. Explicit
`tag-policy="prepare"` permits a missing future tag only with newer working-tree
evidence, a repository, and matching current source/harness inventories using
shared fingerprint framing. If the tag exists at planning or publication time,
exact blob verification is mandatory. This replaces the former two-step advice
to publish local links first; see the [workflow contract](workflow-api.md).

Exact blob verification replaces text-mode or attribute-filtered hashes.
Different CRLF/LF bytes, repaired retained evidence, or a newly rendered SVG cannot
claim the identity of an older tagged artifact. Preserve historical figure bytes
and renderers when required; adopt the dependency-free SVG layout for new
publications explicitly. Do not silently regenerate evidence hashes or mutate tags
to make checks pass.

The shared renderer uses explicit row order, fixed general numeric formatting,
recorded timing units, full coverage counts, and neutral point-ratio language.
It does not preserve MCMC's Matplotlib layout or duration formatter byte for byte.
Preserve old figures at their original paths. Custom scientific plotting belongs
in consumer notebooks; shared maintenance and SVG rendering add no plotting
dependencies. Historical visual identity alone does not require legacy writers.

Marker interiors use LF while surrounding bytes remain untouched. Paths reject
all symlink components and portable aliases, and failures preserve original
outputs with the shared rollback/recovery behavior. These defaults are consistent
across consumers and are not optional compatibility modes.

Before deleting the consumer helpers, pin the released package and exercise its
legacy evidence, release mismatch, malformed markers, historical links, tagged
artifact mismatch, deterministic rerender, and failed multi-output publication
cases. Keep scientific assertions and retained-schema fixtures in the consumer;
shared mechanics and isolated wheel/sdist regression coverage belong here.
