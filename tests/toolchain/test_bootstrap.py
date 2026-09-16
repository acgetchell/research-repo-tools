"""Execute the native launcher with a fake uv; never contact installer services."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from research_repo_tools.toolchain_bootstrap import render


@pytest.mark.parametrize("fail_setup", [False, True])
def test_native_launcher_uses_locked_tooling_then_managed_project_sync(tmp_path, fail_setup):
    consumer = tmp_path / "consumer with spaces"
    consumer.mkdir()
    binaries = tmp_path / "binaries with spaces"
    binaries.mkdir()
    log = tmp_path / "commands.jsonl"
    stub = tmp_path / "uv_stub.py"
    stub.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version']:\n"
        "    print('uv 0.12.15 (synthetic)')\n"
        "    sys.exit(0)\n"
        "with Path(os.environ['RRT_TEST_LOG']).open('a') as stream:\n"
        "    stream.write(json.dumps({'args': args, 'cwd': os.getcwd()}) + '\\n')\n"
        "sys.exit(19 if os.environ['RRT_TEST_FAIL'] == '1' else 0)\n"
    )
    if os.name == "nt":
        powershell = shutil.which("powershell.exe")
        assert powershell is not None, "Windows validation requires its native PowerShell launcher"
        wrapper = binaries / "uv.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
        script = consumer / "bootstrap.ps1"
        script.write_text(render(script.name, "0.12.15"))
        command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    else:
        wrapper = binaries / "uv"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n')
        wrapper.chmod(0o700)
        script = consumer / "bootstrap.sh"
        script.write_text(render(script.name, "0.12.15"))
        command = ["/bin/sh", str(script)]
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", ""),
        "RESEARCH_REPO_TOOLS_HOME": str(tmp_path / "cache"),
        "RRT_TEST_LOG": str(log),
        "RRT_TEST_FAIL": "1" if fail_setup else "0",
    }
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == (19 if fail_setup else 0), result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == {
        "args": ["run", "--locked", "--managed-python", "--only-group", "tooling", "research-repo-tools", "toolchain", "sync"],
        "cwd": str(consumer),
    }
    assert len(calls) == (1 if fail_setup else 2)
    if not fail_setup:
        assert calls[1]["args"] == [
            "run",
            "--locked",
            "--managed-python",
            "--only-group",
            "tooling",
            "research-repo-tools",
            "toolchain",
            "run",
            "--",
            "uv",
            "sync",
            "--locked",
            "--managed-python",
            "--group",
            "tooling",
        ]
    assert not (tmp_path / "cache").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer transport; PowerShell transport runs on Windows")
def test_download_failure_never_executes_partial_installer(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    script = tmp_path / "bootstrap.sh"
    script.write_text(render("bootstrap.sh", "0.12.15"))
    marker = tmp_path / "installer_was_executed"
    curl = binaries / "curl"
    curl.write_text(f'#!/bin/sh\nfor arg do output="$arg"; done\nprintf "touch \'{marker}\'\\n" > "$output"\nexit 22\n')
    curl.chmod(0o700)
    for name in ("mktemp", "rm", "sh"):
        source = shutil.which(name)
        assert source
        (binaries / name).symlink_to(source)
    env = {**os.environ, "PATH": str(binaries), "RESEARCH_REPO_TOOLS_HOME": str(tmp_path / "cache")}
    result = subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, timeout=30)
    assert result.returncode == 22
    assert not marker.exists()
