"""Deterministic rendering regressions, also run from installed distributions."""

import base64
import hashlib
import json
import sys
import unittest
from dataclasses import replace
from unittest.mock import patch

from research_repo_tools.complete_runs import (
    RUN_SCHEMA,
    RUN_SCHEMA_V2,
    RUN_SCHEMA_V3,
    CompleteCase,
    CompletePolicy,
    CompleteRun,
    CompleteSample,
    RunSeries,
    render_run,
    serialize_policy,
    serialize_run,
    validate_run_evidence,
)
from research_repo_tools.evidence import Evidence, Provenance, serialize_evidence
from research_repo_tools.host_metadata import HostMetadata, serialize_host


def encoded(value):
    return (json.dumps(value, ensure_ascii=False) + "\r\n").encode("utf-8")


def fixture(size=3, *, schema=RUN_SCHEMA, disjoint=False):
    """Keep phase/statistic estimates distinct and peers deliberately sparse."""
    phases, series, sources = [], [], []
    for phase, offset in (("baseline", 10), ("current", 100)):
        cases, rows = [], []
        for index in range(size):
            identity = f"{phase if disjoint else 'shared'}/café|<{index:03}>"
            point = offset + index
            estimates = {
                statistic: {
                    "point_estimate": point + delta,
                    "confidence_interval": {"lower_bound": point + delta - 1, "upper_bound": point + delta + 1, "confidence_level": 0.95},
                }
                for statistic, delta in (("mean", 1), ("median", 0))
            }
            cases.append(
                CompleteCase(
                    f"stored/{index:03}",
                    encoded({"full_id": identity}),
                    encoded(estimates),
                    encoded({"iters": [1, 2, 3], "times": [point, point * 2, point * 3]}),
                    raw_files=(("tukey.json", b"[1, 2, 3, 4]\r\n"),) if schema == RUN_SCHEMA_V3 else (),
                )
            )
            rows.append((f"row|<{index:03}>", identity))
        policy = CompletePolicy(tuple(case.full_id for case in cases), sample_count=3)
        phases.append((phase, CompleteSample(policy, tuple(cases))))
        series.append(RunSeries(phase, phase, tuple(rows)))
        if phase == "baseline":
            series.append(RunSeries("sparse peer", phase, (rows[-1],)))
    compatible = ("context.harness-modes", "context.tool.python", "harness_sha256")
    run = CompleteRun(tuple(phases), tuple(series), compatible, schema=schema, harness_mode="independent" if schema == RUN_SCHEMA_V3 else "common")
    declaration = {
        "schema": 4,
        "harness-mode": run.harness_mode,
        "backend": "worktree",
        "sources": ["source"],
        "harness": ["bench.py"],
        "workloads": ["cases.json"],
        "raw-files": ["tukey.json"],
        "criterion-dir": "target/criterion",
        "sample": "new",
        "timeout": 300,
        "probes": {"python": ["python", "--version"]},
        "dependencies": {},
        "context": {},
        "compatible": sorted(compatible),
        "statistics": ["mean", "median"],
        "confidence-level": 0.95,
        "unit": "ns",
        "phases": {
            phase: {"command": ["python", "bench.py"], "gate": ["python", "gate.py"], "environment": {}, "sample-count": 3, "expected": sample.policy.expected}
            for phase, sample in phases
        },
        "series": [{"name": item.name, "phase": item.phase, "rows": dict(item.rows)} for item in series],
    }
    host = HostMetadata("fixture OS", "fixture arch", "fixture CPU", 2, 4, 1024, (("python", "Python fixture"),))
    for phase, sample in phases:
        context = {
            "phase": phase,
            "release": "v1.0.0" if phase == "baseline" else "v1.1.0",
            "mode": "tag",
            "command": json.dumps(["python", "bench.py"]),
            "gate-command": json.dumps(["python", "gate.py"]),
            "gate-status": "passed",
            "environment": "{}",
            "host": json.dumps(json.loads(serialize_host(host))),
            "source-inventory": '["source"]',
            "original-source-inventory": '["source"]',
            "harness-inventory": '["bench.py"]',
            "harness-modes": '[["bench.py", 420]]',
            "original-source-sha256": ("1" if phase == "baseline" else "2") * 64,
            "fingerprint-schema": "research-repo-tools/files/v1",
            "tool.python": json.dumps("Python fixture"),
        }
        if schema != RUN_SCHEMA:
            context["sampling-policy"] = serialize_policy(sample.policy).decode()
        if schema == RUN_SCHEMA_V3:
            context.update(
                {
                    "measurement-declaration": json.dumps(declaration, sort_keys=True, ensure_ascii=False),
                    "saved-sample": "none",
                    "workload-sha256": "5" * 64,
                    "workload-inventory": '["cases.json"]',
                }
            )
        sources.append((phase, Provenance(("a" if phase == "baseline" else "b") * 40, context["original-source-sha256"], "4" * 64, tuple(context.items()))))
    evidence = Evidence(serialize_run(run), schema, tuple(sources))
    validate_run_evidence(evidence)
    return evidence


def report(evidence):
    return render_run(evidence, title="Timings | <&>", report_path="docs/report.md", payload_path="data/run.json", manifest_path="data/evidence.json")


class TestRenderingConsumer(unittest.TestCase):
    def test_exact_historical_bytes_and_unchanged_raw_evidence(self):
        # SHA-256 of complete reports from the original renderer, before the
        # lookup optimization. Fixtures use fixed provenance on every platform.
        expected = {
            (RUN_SCHEMA, False): "f2d6a30b557d535c0d583495c1e0c71977a15e67071897b077155b07fbdf57e2",
            (RUN_SCHEMA_V2, False): "81d5179aa2dd46fb01d425057cb9bf1dac98bbf961f70ace50a421e15dd45611",
            (RUN_SCHEMA_V3, False): "c0352fda05f24bec810444d02ebd2e6b2b178fe4bf11d67645f6243a9ce4928d",
            (RUN_SCHEMA_V3, True): "a6d90a07e3a754c94031c4141d27e1625b56a4ca6e61b7efe0ecf72f58120a8b",
        }
        for (schema, disjoint), digest in expected.items():
            with self.subTest(schema=schema, disjoint=disjoint):
                evidence = fixture(schema=schema, disjoint=disjoint)
                before = serialize_evidence(evidence)
                rendered = report(evidence)
                self.assertEqual(hashlib.sha256(rendered).hexdigest(), digest)
                self.assertEqual(serialize_evidence(evidence), before)
                self.assertIn(b"| row&#124;&lt;000&gt; | 11 ns [10, 12] (0.95 confidence) |", rendered)
                self.assertIn("| — | 101 ns [100, 102] (0.95 confidence) |".encode(), rendered)
                self.assertIn(b"[run payload](../data/run.json)", rendered)
                if disjoint:
                    self.assertIn(b"no common benchmark IDs", rendered)
                    self.assertIn(b"| unavailable | 101 ns [100, 102] (0.95 confidence) |", rendered)

    def test_id_decoding_and_row_mapping_work_scale_with_inventory(self):
        for schema, disjoint in ((RUN_SCHEMA, False), (RUN_SCHEMA_V2, False), (RUN_SCHEMA_V3, False), (RUN_SCHEMA_V3, True)):
            counts = []
            for size in (8, 64):
                evidence = fixture(size, schema=schema, disjoint=disjoint)
                rows = {item.rows for item in validate_run_evidence(evidence).series}
                decoded = 0

                def profile(frame, event, argument):
                    nonlocal decoded
                    if event == "call" and frame.f_code.co_name == "full_id":
                        decoded += 1

                previous = sys.getprofile()
                with patch("research_repo_tools.complete_runs.dict", wraps=dict, create=True) as mapped:
                    try:
                        sys.setprofile(profile)
                        report(evidence)
                    finally:
                        sys.setprofile(previous)
                # Count copied row entries, not wall time or a particular local
                # index variable. Validation remains active throughout rendering.
                copied = sum(len(call.args[0]) for call in mapped.call_args_list if call.args and isinstance(call.args[0], tuple) and call.args[0] in rows)
                counts.append((decoded, copied))
            with self.subTest(schema=schema, disjoint=disjoint, counts=counts):
                self.assertGreater(counts[0][0], 0)
                for small, large in zip(counts[0], counts[1], strict=True):
                    self.assertLessEqual(large, 8 * small)

    def test_rendering_revalidates_changed_evidence_after_success(self):
        evidence = fixture(schema=RUN_SCHEMA_V3)
        report(evidence)
        document = json.loads(evidence.payload)
        document["phases"]["current"]["cases"][0]["sample"] = base64.b64encode(encoded({"iters": [1, 2], "times": [100, 200]})).decode()
        with self.assertRaisesRegex(ValueError, "expected 3 raw samples, got 2"):
            report(replace(evidence, payload=encoded(document)))


if __name__ == "__main__":
    unittest.main()
