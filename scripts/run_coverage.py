"""Collect branch and subprocess coverage before merging completed data files."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from coverage import Coverage

from research_repo_tools.files import replace_many


def main(arguments: list[str]) -> int:
    root = Path.cwd()
    report = root / "coverage" / "cobertura.xml"
    report.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".run-", dir=report.parent) as directory:
        data = Path(directory) / ".coverage"
        env = os.environ | {"COVERAGE_FILE": str(data)}
        result = subprocess.run(
            [sys.executable, "-m", "coverage", "run", "--parallel-mode", "-m", "pytest", "-p", "no:cov", *arguments],
            cwd=root,
            env=env,
            check=False,
        )
        # Terminated children can leave a zero-byte SQLite file. It contains no
        # measurements, and coverage's read-only combiner cannot initialize it.
        for shard in data.parent.glob(".coverage.*"):
            if shard.stat().st_size == 0:
                shard.unlink()
        coverage = Coverage(data_file=str(data))
        coverage.combine(strict=True)
        coverage.save()
        coverage.report(show_missing=True)
        staged_report = Path(directory) / report.name
        coverage.xml_report(outfile=str(staged_report))
        replace_many({root / ".coverage": data.read_bytes(), report: staged_report.read_bytes()})
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
