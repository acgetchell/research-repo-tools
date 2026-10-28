# Publishing reports and figures

Begin with retained, verified evidence from [measurement](RECORDING_PERFORMANCE.md).
Declare document markers, output paths and view selections before previewing a
publication. Rendering does not acquire measurements. Preview/check failures
leave existing outputs in place; successful publication stages the complete
planned set, with rollback and recovery reporting on caught failures.
See the [publication API](publication-api.md) for the precise transaction limits.

- [Document publication](#document-publication)
- [Complete-run document sections and coordinate plots](#complete-run-document-sections-and-coordinate-plots)

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
[migration guide](publication-migration.md). Scientific prose remains consumer-owned.

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

Schema 2 selects a validated complete-run `archive`, or explicit `payload` and
`manifest` paths. Archive selection works after scratch cleanup; explicit missing
or partial scratch inputs fail. The document section links the original full
payload and envelope, preserving raw cases outside the selected presentation.
The selected history remains immutable and is revalidated before publication.

Each configured series names an existing retained view, supplies a display label,
and maps row keys to semantic benchmark IDs within that view. Row order controls
the table and CSV; series order controls columns and the plot legend. Every row
needs a finite, unique numeric coordinate for a coordinate plot. Plot lines follow
numeric coordinate order. Mean or median and the timing unit must match recorded
evidence; no unit conversion occurs. Consumer configuration owns workload
coordinates, labels, scientific eligibility and interpretation.

The optional `plot` produces a deterministic accessible SVG with point timings
and recorded marginal confidence intervals. Both axes support `linear` or `log`;
log coordinates must be positive. Reused peers keep their original baseline phase,
revision and release in tables, figures and CSV, with the complete-run identity.
Optional `comparisons` select numerator/denominator series by name. Ratios are
point estimates; time reduction is `100 × (numerator − denominator) / numerator`,
so positive values mean less denominator time. `svg` requires exactly one such
comparison; `csv` exports selected timings with provenance and confidence bounds.

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

Cells become `5<br>[4.5, 5.5]`; the caption states the selected statistic,
recorded unit and common confidence level once. Ratios such as `2×` retain
explicit numerator/denominator labels below the table. Use `["reduction"]`
for percentage reductions, `["ratio", "reduction"]` for both, or `[]` for
timings alone. `reduction-headings` customizes the percentage headings. Heading
arrays follow comparison order and must cover every comparison when supplied.
Omitting `table` preserves the existing inline intervals, `Case` heading and
both comparison columns. Differing confidence levels fail complete-run
validation; they cannot be presented as one shared level.

The same controls are available through the public Python API:

```python
from research_repo_tools.run_presentation import RunTableStyle, render_run_table

table = render_run_table(
    evidence,
    layout,
    style=RunTableStyle(
        row_heading="Dimension",
        intervals="below",
        timing_legend=True,
        comparison_columns=("ratio",),
        ratio_headings=("Baseline / current", "Reference / current"),
        ratio_suffix="×",
    ),
)
```

Here `layout.comparisons` selects the same two pairs as the TOML example.
These table style controls are available from v0.2.0. Older consumers must
update their published pin before adopting this configuration.

Missing selections fail by default. Explicit `missing = "unavailable"` permits
unmapped cells, labels them in tables and CSV, and breaks plot lines at those
rows. Invalid or unmeasured mapped IDs always fail. Coverage states selected,
unavailable and omitted phase cases. Without an optional formatter, document bytes
outside markers survive, and
the document, SVGs and CSV move in one transaction with the existing freshness,
release-reference, tagged-link and rollback checks. See the
[publication configuration](publication-api.md#publication-configuration-v2)
and [presentation API](complete-run-api.md#configured-presentation).

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
[consumer deletion map](shared-capability-migration.md) before removing
working implementations; adoption requires an exact published release.
