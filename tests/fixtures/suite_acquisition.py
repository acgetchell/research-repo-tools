"""Benchmark sources, plans, and modeled Git objects for suite acquisition."""

import hashlib
import json
import sys
from pathlib import Path

from research_repo_tools.common_measurement import parse_plan
from research_repo_tools.host_metadata import HostMetadata
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.suite_measurement import AcquisitionSuite, SuiteMeasurementPlan

PROGRAM = """import json, subprocess, sys
from pathlib import Path
suite, action, log = sys.argv[1:4]
supported = json.loads(Path('apis.json').read_bytes())
assert suite in supported, 'unsupported API was compiled/invoked'
with Path(log).open('ab') as output:
    output.write((suite + ' ' + action + ' ' + Path('revision').read_text() + '\\n').encode())
case = suite + '/case'
if action == 'compile' and Path('doc.rs').exists():
    subprocess.run(['rustc', '--crate-type=lib', '--emit=dep-info=inputs.d', 'doc.rs'], check=True)
    dependencies = Path('inputs.d').read_text()
    assert ('README.md' in dependencies) == (Path('revision').read_bytes() == b'old')
elif action == 'list':
    print(case + ': benchmark')
elif action == 'measure':
    target = Path('target/criterion') / suite / 'new'
    target.mkdir(parents=True)
    data = {
        'benchmark.json': {'full_id': case},
        'sample.json': {'iters': [1, 1], 'times': [5, 5]},
        'estimates.json': {s: {'point_estimate': 5, 'confidence_interval': {'confidence_level': .95, 'lower_bound': 4, 'upper_bound': 6}} for s in ('mean', 'median')},
        'tukey.json': [1, 2, 3, 4],
    }
    for name, value in data.items():
        (target / name).write_bytes((json.dumps(value) + '\\r\\n').encode())
"""


HOST = HostMetadata("fixture-os", "fixture-arch", "fixture-cpu", 4, 8, 1024, (("python", "fixture-python"),))


PAIR = ReleasePair("v1.1.0", "v1.0.0")


def toml(value):
    """Serialize the scalar, list, and inline-table subset used in measurement plans."""
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(key) + "=" + toml(item) for key, item in value.items()) + "}"
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(toml(item) for item in value) + "]"
    return json.dumps(value)


def sources(apis: tuple[str, ...], revision: str) -> dict[str, bytes]:
    """Return the tracked benchmark inputs for a synthetic revision."""
    return {
        "bench.py": PROGRAM.encode(),
        "apis.json": json.dumps(apis).encode(),
        "revision": revision.encode(),
        "Cargo.toml": b'[package]\nname="fixture"\nversion="1.1.0"\n',
        **{f"input-{name}.json": b"[1, 2]\r\n" for name in ("shared", "new", "removed", "later")},
    }


def plan(log: Path, names: tuple[str, ...] = ("shared", "new", "removed"), *, mode: str = "common") -> SuiteMeasurementPlan:
    """Build a suite plan with separate compile, discovery, gate, and measure commands."""
    suites = []
    for name in names:
        command = [sys.executable, "bench.py", name]
        phases = {
            phase: {
                "command": [*command, "measure", str(log)],
                "gate": [*command, "gate", str(log)],
                "discovery": {"required": [f"{name}/case"], "suites": [{"name": name, "command": [*command, "list", str(log)]}]},
            }
            for phase in ("baseline", "current")
        }
        measured = parse_plan(
            {
                "schema": 4,
                "harness-mode": mode,
                "backend": "export",
                "sources": ["revision", "apis.json"],
                "harness": ["bench.py", f"input-{name}.json"],
                "workloads": [f"input-{name}.json"],
                "sample-count": 2,
                "probes": {"python": [sys.executable, "--version"]},
                "phases": phases,
                "series": [{"name": phase, "phase": phase, "select": {"include": ["*"]}} for phase in phases],
            }
        )
        suites.append(
            AcquisitionSuite(
                name,
                name + "-v1",
                measured,
                since="v1.1.0" if name == "new" else None,
                before="v1.1.0" if name == "removed" else None,
                compilation=tuple((phase, (*command, "compile", str(log))) for phase in phases),
            )
        )
    return SuiteMeasurementPlan(tuple(suites))


def git_model(revisions: dict[str, dict[str, bytes]]):
    """Model read-only Git object queries over the supplied revision contents."""

    def oid(data: bytes) -> bytes:
        return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode()

    keys = {tag: chr(ord("a") + index) * 40 for index, tag in enumerate(revisions)}
    blobs = {oid(data): data for files in revisions.values() for data in files.values()}

    def git(directory: Path, *args: str, input: bytes | None = None) -> bytes:
        if args == ("rev-parse", "--show-prefix"):
            return b""
        if args[0] == "rev-parse":
            tag = args[-1].removeprefix("refs/tags/").removesuffix("^{commit}")
            if tag == "HEAD":
                tag = next(reversed(keys))
            return ((tag if tag in keys.values() else keys[tag]) + "\n").encode()
        if args[0] == "ls-tree":
            files = next(files for tag, files in revisions.items() if keys[tag] == args[-1])
            return b"".join(b"100644 blob " + oid(data) + b"\t" + name.encode() + b"\0" for name, data in sorted(files.items()))
        if args != ("cat-file", "--batch") or input is None:
            raise AssertionError(f"unexpected Git operation: {args}")
        return b"".join(key + b" blob " + str(len(blobs[key])).encode() + b"\n" + blobs[key] + b"\n" for key in input.splitlines())

    return git
