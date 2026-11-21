"""Retained comparison evidence for performance report consumers."""

from research_repo_tools.criterion import COMPARISON_SCHEMA, ConfidenceInterval, Estimate, Sample, compare_samples, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance


def retained(current: str = "v1.1.0", baseline: str = "v1.0.0", point: float = 5) -> Evidence:
    """Build serialized comparison evidence with independently labeled baseline and current revisions."""
    comparison = compare_samples(
        Sample((("shared", Estimate(10, ConfidenceInterval(8, 12))),)), Sample((("shared", Estimate(point, ConfidenceInterval(4, 6))),))
    )
    return Evidence(
        serialize_comparison(comparison),
        COMPARISON_SCHEMA,
        (("baseline", Provenance("a" * 40, context=(("release", baseline),))), ("current", Provenance("c" * 40, context=(("release", current),)))),
    )
