"""Collect branch and subprocess coverage before merging completed data files."""

import os
import shutil
import signal
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
    """Run pytest with the coverage environment and write the Cobertura report."""
    root = Path.cwd()
    report = root / "coverage" / "cobertura.xml"
    report.parent.mkdir(exist_ok=True)
    with ExitStack() as cleanup:
        directory = tempfile.mkdtemp(prefix="failed-", dir=report.parent)
        cleanup.callback(shutil.rmtree, directory)
        data = Path(directory) / ".coverage"
        env = os.environ | {"COVERAGE_FILE": str(data)}
        command = [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--parallel-mode",
            "-m",
            "pytest",
            "-p",
            "no:cov",
            f"--junitxml={data.parent / 'pytest.xml'}",
            *arguments,
        ]
        try:
            status = subprocess.run(command, cwd=root, env=env, check=False).returncode
        except KeyboardInterrupt:
            status = 130
        if status:
            # Keep the failure report even if aggregation or publication fails.
            # Parallel installed-consumer output can obscure pytest's traceback.
            cleanup.pop_all()
        # A signaled test driver can leave workers finishing their last shard.
        # Windows reports Ctrl-C as the unsigned STATUS_CONTROL_C_EXIT.
        if status in {130, -signal.SIGINT, 0xC000013A}:
            print(f"Coverage interrupted. Diagnostics retained at {data.parent}", file=sys.stderr)
            return 130
        if status not in (0, 1):
            # pytest reserves 1 for completed tests with failing assertions.
            # A collection, usage, or internal error cannot yield a full report.
            print(f"Pytest did not complete (exit status {status}). Diagnostics retained at {data.parent}", file=sys.stderr)
            return 128 - status if status < 0 else status
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
        if status:
            print(f"Pytest failed with exit status {status}. Diagnostics retained at {data.parent}", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
