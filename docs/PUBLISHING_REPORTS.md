# Publishing reports and figures

Begin with retained, verified evidence from [measurement](RECORDING_PERFORMANCE.md).
Declare document markers, output paths and view selections before previewing a
publication. Rendering does not acquire measurements. Preview/check failures
leave existing outputs in place; successful publication stages the complete
planned set, with rollback and recovery reporting on caught failures.
See the [publication API](publication-api.md) for the precise transaction limits.

- [Document publication](#document-publication)
- [Complete-run document sections and coordinate plots](#complete-run-document-sections-and-coordinate-plots)
- [Complete figure generations](#complete-figure-generations)
- [Publishing a custom scientific view](#publishing-a-custom-scientific-view)
- [Document publication migration](performance-migration.md#document-publisher-migration)

## Document publication

Publish a selected timing table and optional SVG into an existing marked section:

```sh
just performance publish publication.toml --preview
just performance publish publication.toml
just performance publish publication.toml --check
```

Preview prints validated candidate diffs without writing. Check exits `1` for
stale or invalid outputs and `0` when current. Default mode updates the document
and figure in one rollback-capable transaction. All modes verify the retained
payload hash, selected measurements, configured provenance, and release/report
references. No measurements or network retrieval run.

Place exactly one ordered `<!-- PERFORMANCE:BEGIN -->` and
`<!-- PERFORMANCE:END -->` pair in the document. Configure shared comparison
evidence and explicit benchmark selection in `publication.toml`:

```toml
schema = 1
document = "README.md"
payload = "evidence/comparison.json"
manifest = "evidence/manifest.json"
begin = "<!-- PERFORMANCE:BEGIN -->"
end = "<!-- PERFORMANCE:END -->"
unit = "ns"
svg = "figures/timings.svg" # Omit for table-only publication.
rows = [{benchmark = "step/2", label = "Two-dimensional step"}]
links = [
    {label = "Report and measurement context", path = "PERFORMANCE.md"},
    {label = "Retained measurements", path = "evidence/comparison.json"},
    {label = "Provenance", path = "evidence/manifest.json"},
]

[provenance.baseline]
revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" # Replace with expected commit.
release = "v1.0.0"

[provenance.current]
revision = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" # Replace with expected commit.
release = "v1.1.0"

[[references]]
path = "Cargo.toml"
pattern = '^version = "(?P<value>[^"]+)"\r?$'
source = "version"

[[references]]
path = "PERFORMANCE.md"
pattern = '^Baseline: (?P<value>[^\r\n]+)'
source = "previous-tag"

[[references]]
path = "PERFORMANCE.md"
pattern = '^Current: (?P<value>[^\r\n]+)'
source = "tag"
```

Use the consumer's actual package/report paths and precise capture patterns.
This example expects `version = "1.1.0"` and report lines `Baseline: v1.0.0`
and `Current: v1.1.0`. Record each source's release in its evidence
`Provenance.context`, for example `(("release", "v1.1.0"),)`. Maintain expected
revisions independently of the files being checked. Add `source-sha256`,
`harness-sha256`, or a `context` table to a provenance table when those identities
are required. Unknown or mismatched values fail.

Rows follow configuration order; each must have both baseline and current data.
The unit must match the recorded unit. Optional `baseline-label` and
`current-label` change displayed headings without changing release expectations.
The dependency-free SVG shows point ratios; scientific commentary stays in the
consumer. Bytes outside the marker interior, including historical links and
line endings, are preserved.

Links default to document-relative paths. To publish tagged GitHub links, set
top-level `repository = "owner/repository"`. The current release tag must exist
locally and contain the exact retained, referenced, linked, and generated figure
bytes. To assert that a measured source itself is a tagged commit, also set
`verify-tag = true` in that source's provenance table. These are read-only local
Git checks; fetching release assets remains a separate explicit operation.

Historical CSV/JSON formats can use declarative conversion into shared companions
at new paths. See the [publication API](publication-api.md) and
[migration guide](performance-migration.md#document-publisher-migration). Scientific prose remains consumer-owned.

## Complete-run document sections and coordinate plots

Configure `complete-publication.toml` under
`tooling/` using the packaged scaffold, then edit its named series, exact
benchmark IDs, rows, coordinates, statistic, unit, release references and
independent provenance pins to match your evidence:

```sh
just templates complete-publication.toml --output tooling/complete-publication.toml
just performance publish tooling/complete-publication.toml --preview
just performance publish tooling/complete-publication.toml
just performance publish tooling/complete-publication.toml --check
```

Select the retained `archive` to publish after scratch cleanup, or supply both
`payload` and `manifest`. Choose named views and map each row to a measured
benchmark ID. Match the recorded statistic and unit. For a coordinate plot,
give each row a distinct finite coordinate and use positive values on log axes.
The generated section links the original evidence, including cases outside the view.

For paired timings, name the numerator and denominator of each comparison.
The ratio SVG accepts one comparison. A [new series baseline](REUSING_BENCHMARKS.md#starting-a-benchmark-series-after-earlier-releases)
uses absolute current timings and omits comparisons.

For compact tables, schema 2 also accepts a `table` style. This example selects
one row column, three configured timing series and two ratio-only comparisons.
Keep the existing three `series` entries; replace `comparisons` and omit `svg`
(the ratio figure requires exactly one comparison):

```toml
comparisons = [
    {numerator = "Library baseline", denominator = "Library current"},
    {numerator = "Reference reused", denominator = "Library current"},
]

[table]
comparison-columns = ["ratio"]
intervals = "below"
ratio-headings = ["Baseline / current", "Reference / current"]
ratio-suffix = "×"
row-heading = "Dimension"
timing-legend = true
```

The example renders a timing as `5<br>[4.5, 5.5]` and a ratio as `2×`.
The shared caption identifies the statistic, unit and confidence level.
Use `missing = "unavailable"` to display gaps for unmapped cells. A mapped ID
must exist in its retained view.

See the [publication fields and accepted evidence](publication-api.md#publication-configuration-v2)
for input and output constraints, and the [presentation API](complete-run-api.md#configured-presentation)
for the Python equivalent, style defaults and comparison arithmetic.

## Complete figure generations

Use the same directory primitive for a complete scientific figure set:

```python
from pathlib import Path

from research_repo_tools.files import publish_directory

with publish_directory(Path("figures/validation")) as candidate:
    render_figures(candidate)  # Consumer-owned rendering and figure names.
    validate_figures(candidate)  # Consumer-owned scientific assertions.
```

Only after generation and validation finish does the directory replace the
previous figure set. Caught commit failures restore the original tree; incomplete
rollback reports a retained recovery directory. See the
[consumer deletion map](migration.md#shared-capability-migration) before removing
working implementations; adoption requires an exact published release.

## Publishing a custom scientific view

For a custom renderer, check the release pair and scientific eligibility,
then pass the original CSV, JSON provenance, report and package metadata bytes
as `inputs`. Use `MarkerPair` for the existing section, `figures` for rendered
SVGs and `ReleaseRule` for fixed-value reference assertions. Call
`publish_publication` once with the resulting plan.

```python
from research_repo_tools.publication import GitCheck, MarkerPair, plan_publication


def plan_validated_report(root, original_inputs, section, svg_path, svg_bytes, tag):
    """Plan report edits from the input bytes the consumer validated for this release.

    The consumer must already have checked payload integrity, release and report
    consistency, source and harness identity, and eligibility.
    """
    linked_artifacts = ("docs/PERFORMANCE.md", "evidence/report.csv", "evidence/report.json", svg_path)
    return plan_publication(
        root,
        "README.md",
        MarkerPair("<!-- PERFORMANCE:BEGIN -->", "<!-- PERFORMANCE:END -->"),
        section,
        inputs=original_inputs,
        figures={svg_path: svg_bytes},
        git_checks=(GitCheck(tag, linked_artifacts),),
    )
```

Snapshot all inputs that affect validation before parsing and pass those same
bytes to `plan_publication`. The planner can then detect changes before preview
or publication. The consumer checks schema, source identity and scientific
eligibility. For shared-envelope evidence, the [declarative publication
example](#document-publication) replaces this wrapper.

## Document publication migration

When replacing a local publisher, follow the [document publisher migration](performance-migration.md#document-publisher-migration)
for retained schemas, rendering changes and consumer acceptance checks.
