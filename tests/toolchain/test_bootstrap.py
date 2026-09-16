"""Execute the native launcher with a fake uv; never contact installer services."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from research_repo_tools.toolchain_bootstrap import render


@pytest.fixture
def powershell():
    command = shutil.which("powershell.exe" if os.name == "nt" else "pwsh")
    if os.name == "nt":
        assert command is not None, "Windows validation requires its native PowerShell launcher"
    elif command is None:
        pytest.skip("PowerShell is not installed on this POSIX host")
    return command


@pytest.mark.parametrize("launcher", ["posix", "powershell"])
@pytest.mark.parametrize("fail_setup", [False, True])
def test_native_launcher_uses_locked_tooling_then_managed_project_sync(tmp_path, fail_setup, launcher, request):
    powershell = None
    if launcher == "posix" and os.name == "nt":
        pytest.skip("POSIX launcher runs on Linux and macOS")
    if launcher == "powershell":
        powershell = request.getfixturevalue("powershell")
    consumer = tmp_path / "consumer with spaces"
    consumer.mkdir()
    binaries = tmp_path / "binaries with spaces"
    binaries.mkdir()
    other_binaries = tmp_path / "other binaries"
    other_binaries.mkdir()
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
        wrapper = binaries / "uv.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
        (other_binaries / "uv.cmd").write_text("@echo Wrong uv selected >&2\n@exit /b 91\n", encoding="utf-8")
    else:
        wrapper = binaries / "uv"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n')
        wrapper.chmod(0o700)
        other = other_binaries / "uv"
        other.write_text("#!/bin/sh\necho 'Wrong uv selected' >&2\nexit 91\n")
        other.chmod(0o700)
        curl = binaries / "curl"
        curl.write_text("#!/bin/sh\necho 'Unexpected network access in launcher test' >&2\nexit 92\n")
        curl.chmod(0o700)
    if launcher == "powershell":
        assert powershell is not None
        script = consumer / "bootstrap.ps1"
        script.write_text(render(script.name, "0.12.15"))
        harness = tmp_path / "run-bootstrap.ps1"
        harness.write_text(
            "param([string]$Bootstrap)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "function Invoke-WebRequest { throw 'Unexpected network access in launcher test' }\n"
            + (
                # Exercise PowerShell on POSIX using a Windows-shaped cache path.
                "New-PSDrive -Name T -PSProvider FileSystem -Root $env:RRT_TEST_ROOT | Out-Null\n$env:RESEARCH_REPO_TOOLS_HOME = 'T:\\cache'\n"
                if os.name != "nt"
                else ""
            )
            + "& $Bootstrap\nexit $LASTEXITCODE\n"
        )
        command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), str(script)]
    else:
        script = consumer / "bootstrap.sh"
        script.write_text(render(script.name, "0.12.15"))
        command = ["/bin/sh", str(script)]
    env = {
        **os.environ,
        "PATH": os.pathsep.join([str(binaries), str(other_binaries), os.environ.get("PATH", "")]),
        "RESEARCH_REPO_TOOLS_HOME": str(tmp_path / "cache"),
        "RRT_TEST_LOG": str(log),
        "RRT_TEST_FAIL": "1" if fail_setup else "0",
        "RRT_TEST_ROOT": str(tmp_path),
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


def test_powershell_download_is_unattended_and_cleans_up_on_failure(tmp_path, powershell):
    script = tmp_path / "bootstrap.ps1"
    script.write_text(render(script.name, "0.12.15"))
    log = tmp_path / "download.json"
    harness = tmp_path / "download-test.ps1"
    harness.write_text(
        r"""param([string]$Bootstrap)
$ErrorActionPreference = 'Stop'
function Get-Command { return $null }
function Invoke-WebRequest {
    param([string]$Uri, [string]$OutFile, [int]$TimeoutSec, [switch]$UseBasicParsing)
    @{
        uri = $Uri
        path = $OutFile
        timeout = $TimeoutSec
        basicParsing = [bool]$UseBasicParsing
        tls12 = [bool]([Net.ServicePointManager]::SecurityProtocol -band [Net.SecurityProtocolType]::Tls12)
    } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $env:RRT_TEST_LOG
    [IO.File]::WriteAllText($OutFile, "throw 'Partial installer must not run'")
    throw 'Synthetic download failure'
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls
"""
        + (
            "New-PSDrive -Name T -PSProvider FileSystem -Root $env:RRT_TEST_ROOT | Out-Null\n$env:RESEARCH_REPO_TOOLS_HOME = 'T:\\cache'\n"
            if os.name != "nt"
            else ""
        )
        + r"""
try {
    & $Bootstrap -UvOnly
    throw 'Expected download failure'
} catch {
    if ($_.Exception.Message -ne 'Synthetic download failure') { throw }
}
if ([Net.ServicePointManager]::SecurityProtocol -ne [Net.SecurityProtocolType]::Tls) {
    throw 'TLS policy was not restored'
}
"""
    )
    env = {
        **os.environ,
        "RESEARCH_REPO_TOOLS_HOME": str(tmp_path / "cache"),
        "RRT_TEST_ROOT": str(tmp_path),
        "RRT_TEST_LOG": str(log),
    }
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    download = json.loads(log.read_text(encoding="utf-8-sig"))
    assert download["uri"] == "https://astral.sh/uv/0.12.15/install.ps1"
    assert download["timeout"] == 300
    assert download["basicParsing"] is True
    assert download["tls12"] is True
    assert not os.path.exists(download["path"])


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
