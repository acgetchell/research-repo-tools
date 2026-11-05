"""Exercise the maintainer's coverage merge with real workers and child data."""

import importlib.util
import json
import os
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from coverage import CoverageData

from research_repo_tools import files

RUNNER = Path(__file__).resolve().parents[2] / "scripts/run_coverage.py"


def test_project_coverage_measures_owned_roots_without_generated_cache_inventory(tmp_path):
    policy = tomllib.loads((RUNNER.parent.parent / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]["run"]
    names = ("src/research_repo_tools/owned.py", "scripts/owned.py", "target/review-uv-cache/unrelated.py")
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"answer = 42\n")
    child = (
        "import json,runpy,sys; from pathlib import Path; from coverage import Coverage; "
        "policy=json.loads(sys.argv[1]); cov=Coverage(config_file=False, source=policy['source'], omit=policy['omit']); "
        "cov.start(); [runpy.run_path(name) for name in json.loads(sys.argv[2])]; cov.stop(); "
        "print(json.dumps(sorted(Path(name).relative_to(Path.cwd()).as_posix() for name in cov.get_data().measured_files())))"
    )
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("COVERAGE_")}
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", child, json.dumps(policy), json.dumps(names)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == ["scripts/owned.py", "src/research_repo_tools/owned.py"]


@pytest.mark.parametrize("failure", ["render", "publish"])
def test_failed_coverage_publication_preserves_previous_pair(tmp_path, monkeypatch, failure):
    spec = importlib.util.spec_from_file_location("run_coverage", RUNNER)
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    monkeypatch.chdir(tmp_path)
    database = tmp_path / ".coverage"
    report = tmp_path / "coverage" / "cobertura.xml"
    report.parent.mkdir()
    before = {database: b"previous database", report: b"previous report"}
    for path, payload in before.items():
        path.write_bytes(payload)

    class Coverage:
        def __init__(self, *, data_file):
            self.data = Path(data_file)

        def combine(self, **kwargs):
            pass

        def save(self):
            self.data.write_bytes(b"new database")

        def report(self, **kwargs):
            pass

        def xml_report(self, *, outfile):
            Path(outfile).write_bytes(b"new report")
            if failure == "render":
                raise OSError("injected render failure")

    replace = files.os.replace

    def fail_report(source, destination):
        if Path(destination) == report and Path(source).read_bytes() == b"new report":
            raise OSError("injected publish failure")
        return replace(source, destination)

    monkeypatch.setattr(harness, "Coverage", Coverage)
    monkeypatch.setattr(harness.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0))
    if failure == "publish":
        monkeypatch.setattr(files.os, "replace", fail_report)
    with pytest.raises(OSError, match=f"injected {failure} failure"):
        harness.main([])
    assert {path: path.read_bytes() for path in before} == before
    assert set(tmp_path.iterdir()) == {database, report.parent}
    assert list(report.parent.iterdir()) == [report]


@pytest.mark.parametrize("test_status", [0, 1])
def test_coverage_keeps_child_branch_data_and_reports_test_status(tmp_path, test_status):
    (tmp_path / "pyproject.toml").write_bytes(
        b'[tool.coverage.run]\nsource=["."]\nomit=["test_*.py"]\nbranch=true\nrelative_files=true\npatch=["subprocess"]\n'
    )
    (tmp_path / "application.py").write_bytes(b'def choice(value):\n    if value:\n        return "yes"\n    return "no"\nassert choice(True) == "yes"\n')
    (tmp_path / "test_worker.py").write_bytes(
        b"import os, subprocess, sys\nfrom pathlib import Path\n"
        b"def test_child():\n"
        b'    Path(os.environ["COVERAGE_FILE"] + ".interrupted").touch()\n'
        b'    subprocess.run([sys.executable, "application.py"], check=True)\n' + (b"    assert False\n" if test_status else b"")
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith(("COVERAGE_", "PYTEST_"))}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [sys.executable, str(RUNNER), "-p", "xdist.plugin", "-n", "2", "--dist", "worksteal"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert result.returncode == test_status, result.stdout + result.stderr
    report = ET.parse(tmp_path / "coverage/cobertura.xml")
    assert report.find(".//class[@filename='application.py']/lines/line[@number='3'][@hits='1']") is not None
    branch = report.find(".//class[@filename='application.py']/lines/line[@number='2'][@branch='true']")
    assert branch is not None and branch.get("condition-coverage") == "50% (1/2)"


@pytest.mark.parametrize("damage", [b"not a coverage database\n", b"SQLite format 3\0" + b"\0" * 100])
def test_corrupt_shard_preserves_prior_report_and_all_raw_measurements(tmp_path, damage):
    (tmp_path / "pyproject.toml").write_bytes(b'[tool.coverage.run]\nsource=["."]\n')
    source = tmp_path / "application.py"
    source.write_bytes(b"answer = 42\n")
    report = tmp_path / "coverage" / "cobertura.xml"
    report.parent.mkdir()
    database = tmp_path / ".coverage"
    before = {database: b"previous database", report: b"previous report"}
    for path, payload in before.items():
        path.write_bytes(payload)
    valid = tmp_path / "complete.sqlite"
    data = CoverageData(str(valid))
    data.add_lines({str(source): {1}})
    data.write()
    raw = {".coverage.a-complete": valid.read_bytes(), ".coverage.z-broken": damage}
    # A real Coverage() controller disables automatic coverage in its process.
    # Keep it out of the pytest worker so this fixture cannot discard that
    # worker's measurements for unrelated tests.
    child = """import runpy, subprocess, sys
from pathlib import Path
runner = runpy.run_path(sys.argv[1])
def completed_tests(args, *, env, **kwargs):
    base = Path(env["COVERAGE_FILE"])
    base.with_name(base.name + ".a-complete").write_bytes(Path("complete.sqlite").read_bytes())
    base.with_name(base.name + ".z-broken").write_bytes(bytes.fromhex(sys.argv[2]))
    return subprocess.CompletedProcess(args, 0)
subprocess.run = completed_tests
try:
    runner["main"]([])
except RuntimeError as error:
    print(error, file=sys.stderr)
    raise SystemExit(1)
"""
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("COVERAGE_")}
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", child, str(RUNNER), damage.hex()],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""
    assert "Incomplete coverage" in result.stderr and "previous report preserved" in result.stderr, result.stderr
    assert {path: path.read_bytes() for path in before} == before
    (retained,) = report.parent.glob("failed-*")
    assert str(retained) in result.stderr
    assert {name: (retained / name).read_bytes() for name in raw} == raw
    assert {path.name for path in retained.iterdir()} <= {*raw, ".coverage"}
    complete = CoverageData(str(retained / ".coverage.a-complete"), read_only=True)
    complete.read()
    assert complete.lines(str(source)) == [1]
    assert not list(report.parent.glob(".run-*"))
