"""Runtime and CLI versions follow installed distribution metadata."""

import subprocess
import sys
from pathlib import Path


def test_runtime_and_cli_follow_distribution_version(tmp_path: Path) -> None:
    metadata = tmp_path / "research_repo_tools-9.8.7.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.4\nName: research-repo-tools\nVersion: 9.8.7\n", encoding="utf-8")
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import research_repo_tools
assert research_repo_tools.__version__ == "9.8.7"
from research_repo_tools.cli import main
main(["--version"])
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "9.8.7\n"
    assert result.stderr == ""
