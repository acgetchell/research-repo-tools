"""Exercise the maintainer's coverage merge with real workers and child data."""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "scripts/run_coverage.py"


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
