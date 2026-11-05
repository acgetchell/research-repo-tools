"""Collect branch and subprocess coverage before merging completed data files."""

import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from contextlib import ExitStack
from pathlib import Path

from coverage import Coverage
from coverage.exceptions import CoverageException, CoverageWarning

from research_repo_tools.files import replace_many


def main(arguments: list[str]) -> int:
    root = Path.cwd()
    report = root / "coverage" / "cobertura.xml"
    report.parent.mkdir(exist_ok=True)
    with ExitStack() as cleanup:
        directory = tempfile.mkdtemp(prefix="failed-", dir=report.parent)
        cleanup.callback(shutil.rmtree, directory)
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
        # strict=True only requires one usable shard; unreadable siblings would
        # otherwise be warned about and silently omitted from the new report.
        # Keep every shard until aggregation succeeds so a failed run remains
        # diagnosable without replacing the prior valid database/report pair.
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", CoverageWarning)
                coverage.combine(strict=True, keep=True)
        except (CoverageException, CoverageWarning) as error:
            # A corrupt SQLite read can leave its file open in coverage's error
            # traceback. Retain the directory in place: Windows cannot rename
            # or delete it while that handle remains open.
            cleanup.pop_all()
            raise RuntimeError(f"Incomplete coverage: {error}. Raw shards retained at {data.parent}; previous report preserved.") from error
        coverage.save()
        coverage.report(show_missing=True)
        staged_report = Path(directory) / report.name
        coverage.xml_report(outfile=str(staged_report))
        replace_many({root / ".coverage": data.read_bytes(), report: staged_report.read_bytes()})
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
