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
release discovery, measurement, scientific conclusions, provenance capture,
eligibility rules, and legacy schema parsing. No consumer name selects behavior.

MCMC's `publish_performance_readme.py` can retain its `load_comparison_artifact`
adapter and its scientific prose. Map validated comparison rows to shared
`Estimate`/`Sample` objects for the common renderers, or keep its current table/SVG
renderers when historical output identity requires them. After checking the
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

The common publisher requires an existing local tag whenever it verifies a tagged
publication. It has no implicit allowance for a future release tag. Prepare a
local-link publication first, then validate tagged links once the intended tag
and artifacts exist. Consumers own the release workflow and the decision to make
new claims about a future release; that decision cannot bypass verification of
already tagged content.

Exact blob verification replaces text-mode or attribute-filtered hashes.
Different CRLF/LF bytes, repaired retained evidence, or a newly rendered SVG cannot
claim the identity of an older tagged artifact. Preserve historical figure bytes
and renderers when required; adopt the dependency-free SVG layout for new
publications explicitly. Do not silently regenerate evidence hashes or mutate tags
to make checks pass.

The shared renderer uses explicit row order, fixed general numeric formatting,
recorded timing units, full coverage counts, and neutral point-ratio language.
It does not preserve MCMC's Matplotlib layout or duration formatter byte for byte.
Retain a small renderer adapter where those details are part of an existing
publication contract. Custom plotting dependencies stay in that consumer's extra;
shared maintenance and SVG rendering add no plotting dependencies.

Marker interiors use LF while surrounding bytes remain untouched. Paths reject
all symlink components and portable aliases, and failures preserve original
outputs with the shared rollback/recovery behavior. These defaults are consistent
across consumers and are not optional compatibility modes.

Before deleting the consumer helpers, pin the released package and exercise its
legacy evidence, release mismatch, malformed markers, historical links, tagged
artifact mismatch, deterministic rerender, and failed multi-output publication
cases. Keep scientific assertions and retained-schema fixtures in the consumer;
shared mechanics and isolated wheel/sdist regression coverage belong here.
