"""Documents, evidence, and layouts for publication tests."""

from pathlib import Path

from research_repo_tools.criterion import COMPARISON_SCHEMA, ConfidenceInterval, Estimate, Sample, compare_samples, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance, serialize_evidence
from research_repo_tools.publication import (
    MarkerPair,
    TableLayout,
)

MARKERS = MarkerPair("<!-- PERFORMANCE:BEGIN -->", "<!-- PERFORMANCE:END -->")


DOCUMENT = b"\xef\xbb\xbf# Results\r\n[Historical](old/v0.9/report.md)\r\n" + MARKERS.begin.encode() + b"\r\nstale\r\n" + MARKERS.end.encode() + b"\r\nTail\r\n"


LAYOUT = TableLayout((("step/2", "Step | <two>"), ("tiny", "Tiny")), "v1.0.0", "v1.1.0", "ns")


def comparison():
    """Compare samples containing shared, added, and removed benchmarks."""
    return compare_samples(
        Sample((("step/2", Estimate(100, ConfidenceInterval(90, 110, 0.95))), ("tiny", Estimate(3)), ("gone", Estimate(2)))),
        Sample((("step/2", Estimate(80)), ("tiny", Estimate(6)), ("added", Estimate(4)))),
    )


def make_fixture(root: Path) -> str:
    """Write the document, evidence, and publication configuration and return its TOML text."""
    root.joinpath("README.md").write_bytes(DOCUMENT)
    root.joinpath("Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.1.0"\n')
    root.joinpath("report.md").write_bytes(b"Baseline: v1.0.0\r\nCurrent: v1.1.0\r\n")
    retained = Evidence(
        serialize_comparison(comparison()),
        COMPARISON_SCHEMA,
        (
            ("baseline", Provenance("a" * 40, harness_sha256="c" * 64, context=(("release", "v1.0.0"),))),
            ("current", Provenance("b" * 40, harness_sha256="c" * 64, context=(("release", "v1.1.0"),))),
        ),
    )
    payload, manifest = serialize_evidence(retained)
    root.joinpath("evidence.json").write_bytes(payload)
    root.joinpath("manifest.json").write_bytes(manifest)
    configuration = f'''schema = 1
document = "README.md"
payload = "evidence.json"
manifest = "manifest.json"
begin = "{MARKERS.begin}"
end = "{MARKERS.end}"
unit = "ns"
svg = "figures/timing.svg"
rows = [{{benchmark = "step/2", label = "Step | <two>"}}, {{benchmark = "tiny", label = "Tiny"}}]
links = [{{label = "Report", path = "report.md"}}, {{label = "Evidence", path = "evidence.json"}}]

[provenance.baseline]
revision = "{"a" * 40}"
release = "v1.0.0"
harness-sha256 = "{"c" * 64}"

[provenance.current]
revision = "{"b" * 40}"
release = "v1.1.0"
harness-sha256 = "{"c" * 64}"

[[references]]
path = "Cargo.toml"
pattern = '^version="(?P<value>[^"]+)"\\r?$'
source = "version"

[[references]]
path = "report.md"
pattern = '^Baseline: (?P<value>[^\\r\\n]+)'
source = "previous-tag"

[[references]]
path = "report.md"
pattern = '^Current: (?P<value>[^\\r\\n]+)'
source = "tag"
'''
    root.joinpath("publication.toml").write_text(configuration, encoding="utf-8", newline="\n")
    return configuration
