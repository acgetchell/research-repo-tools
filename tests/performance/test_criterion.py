"""Boundary and numerical regressions for shared Criterion data."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from research_repo_tools.criterion import (
    Estimate,
    Sample,
    collect_sample,
    compare_samples,
    parse_comparison,
    parse_estimate,
    render_comparison,
    serialize_comparison,
)


@pytest.mark.parametrize("value", [None, True, False, "1", 0, -1, float("nan"), float("inf"), -float("inf"), 10**400])
def test_rejects_invalid_point_before_storing(value: object) -> None:
    with pytest.raises(ValueError, match="point|finite JSON"):
        parse_estimate(json.dumps({"median": {"point_estimate": value}}).encode())


@pytest.mark.parametrize(
    "interval",
    [
        [],
        {},
        {"lower_bound": 1},
        {"lower_bound": 4, "upper_bound": 2},
        {"lower_bound": 0, "upper_bound": 2},
        {"lower_bound": 1, "upper_bound": True},
        {"lower_bound": 1, "upper_bound": 2, "confidence_level": 1},
    ],
)
def test_rejects_malformed_intervals(interval: object) -> None:
    with pytest.raises(ValueError):
        parse_estimate(json.dumps({"median": {"point_estimate": 1, "confidence_interval": interval}}).encode())


@pytest.mark.parametrize("payload", [b"\xff", b"{", b"[]", b"{}", b'{"median":1}', b'{"median":{"point_estimate":1,"point_estimate":2}}'])
def test_json_failures_are_contextual(payload: bytes) -> None:
    with pytest.raises(ValueError, match="Criterion"):
        parse_estimate(payload)


def test_bootstrap_interval_need_not_contain_point_and_no_invented_confidence() -> None:
    assert Estimate(10, 11, 12).confidence_level is None
    with pytest.raises(ValueError, match="requires an interval"):
        Estimate(1, confidence_level=0.95)
    with pytest.raises(ValueError, match="both bounds"):
        Estimate(1, upper=2)


def test_mean_is_explicit_and_unsupported_statistics_fail() -> None:
    payload = b'{"mean":{"point_estimate":20},"median":{"point_estimate":10}}'
    assert parse_estimate(payload).point == 10
    assert parse_estimate(payload, statistic="mean").point == 20
    with pytest.raises(ValueError, match="statistic"):
        parse_estimate(payload, statistic="slope")  # ty: ignore[invalid-argument-type]


def test_samples_enforce_names_units_statistics_and_immutability() -> None:
    with pytest.raises(ValueError, match="unique"):
        Sample((("x", Estimate(1)), ("x", Estimate(2))))
    with pytest.raises(ValueError, match="benchmark name"):
        Sample((("x\ny", Estimate(1)),))
    with pytest.raises(ValueError, match="unit"):
        Sample((), unit="cycles")  # ty: ignore[invalid-argument-type]
    for different in (Sample((), "mean"), Sample((), unit="s")):
        with pytest.raises(ValueError, match="same statistic and unit"):
            compare_samples(Sample(()), different)
    sample = Sample((("x", Estimate(1)),))
    with pytest.raises(FrozenInstanceError):
        sample.estimates = ()  # ty: ignore[invalid-assignment]


@pytest.mark.parametrize(("baseline", "current"), [(1e308, 1e-308), (1e-308, 1e308), (1e-300, 1e7)])
def test_derived_overflow_and_underflow_are_rejected(baseline: float, current: float) -> None:
    with pytest.raises(ValueError, match="ratio|percent reduction"):
        compare_samples(Sample((("x", Estimate(baseline)),)), Sample((("x", Estimate(current)),)))


def test_slowdown_and_extreme_equal_times_remain_finite() -> None:
    (slower,) = compare_samples(Sample((("x", Estimate(100)),)), Sample((("x", Estimate(125)),))).comparisons
    assert (slower.speedup, slower.percent_reduction) == (0.8, -25)
    (equal,) = compare_samples(Sample((("x", Estimate(1e308)),)), Sample((("x", Estimate(1e308)),))).comparisons
    assert (equal.speedup, equal.percent_reduction) == (1, 0)


def test_collection_distinguishes_absent_root_empty_sample_and_invalid_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        collect_sample(tmp_path / "absent", "new")
    assert collect_sample(tmp_path, "new").estimates == ()
    target = tmp_path / "suite" / "case" / "new" / "estimates.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"bad")
    with pytest.raises(ValueError, match="estimates.json"):
        collect_sample(tmp_path, "new")
    with pytest.raises(ValueError, match="single directory"):
        collect_sample(tmp_path, "../new")


def test_collection_rejects_symlinked_entries(tmp_path: Path) -> None:
    target = tmp_path / "estimates.json"
    target.write_bytes(b'{"median":{"point_estimate":10}}')
    link = tmp_path / "linked"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation requires host privileges")
    with pytest.raises(ValueError, match="symlink"):
        collect_sample(tmp_path, "new")


def test_retained_format_rejects_unknown_fields_and_duplicate_rows() -> None:
    result = compare_samples(Sample((("x", Estimate(1)),)), Sample(()))
    raw = json.loads(serialize_comparison(result))
    for document in ({**raw, "schema": "future/v99"}, {**raw, "surprise": True}, {**raw, "baseline": raw["baseline"] * 2}, {**raw, "current": {}}):
        with pytest.raises(ValueError):
            parse_comparison(json.dumps(document).encode())


def test_empty_coverage_is_preserved_and_markdown_is_escaped() -> None:
    empty = compare_samples(Sample(()), Sample(()))
    assert parse_comparison(serialize_comparison(empty)) == empty
    assert "No benchmark estimates" in render_comparison(empty)
    result = compare_samples(Sample(()), Sample((("<img>|`test`", Estimate(2)),)))
    text = render_comparison(result)
    assert "&lt;img&gt;&#124;&#96;test&#96;" in text
    assert "| added | — | 2 ns | — | — |" in text
