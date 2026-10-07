"""Execute real Just composition with recording processes instead of live updates."""

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from research_repo_tools import changelog, process

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable stub; native setup covers the Windows shell")


@pytest.fixture(params=["consumer", "maintainer"])
def recipes(tmp_path, monkeypatch, request):
    consumer = request.param == "consumer"
    source = changelog.template("justfile") if consumer else Path(__file__).resolve().parents[2].joinpath("justfile").read_text()
    justfile = tmp_path / "justfile"
    justfile.write_text(source, newline="\n")
    if not consumer:
        template = tmp_path / "src/research_repo_tools/templates/justfile"
        template.parent.mkdir(parents=True)
        template.write_bytes(changelog.template("justfile").encode())
    # Record the outer uv invocations, including the checked toolchain command.
    # No updater, installer, resolver, or Git command is executed.
    python = tmp_path / "Python's executable with spaces"
    python.symlink_to(sys.executable)
    recorder = tmp_path / "record calls.py"
    recorder.write_text(
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "log = pathlib.Path('calls.jsonl')\n"
        "with log.open('a', newline='\\n') as stream: stream.write(json.dumps(args) + '\\n')\n"
        "sys.exit(23 if len(log.read_text().splitlines()) == int(os.environ.get('FAIL_STEP', '0')) else 0)\n",
        newline="\n",
    )
    uv = tmp_path / "uv"
    uv.write_text(f'#!/bin/sh\nexec {shlex.quote(str(python))} {shlex.quote(str(recorder))} "$@"\n', newline="\n")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("FAIL_STEP", raising=False)
    return justfile, consumer


def invoke(justfile, recipe):
    result = process.run_safe_command("just", ["--justfile", str(justfile), recipe], cwd=justfile.parent, check=False)
    log = justfile.parent / "calls.jsonl"
    return result, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def commands(consumer, cargo):
    prefix = ["run", "--locked", *(["--only-group", "tooling", "--inexact"] if consumer else []), "research-repo-tools"]
    uv = [["run", "--no-config", "--no-sync", "--no-python-downloads", "research-repo-tools", "deps", "update-uv"]]
    tools = [[*prefix, "toolchain", "upgrade"]] if consumer else []
    rust = [[*prefix, "toolchain", "run", "--", "cargo", *args] for args in (["upgrade", "--incompatible", "allow"], ["update"])] if cargo else []
    sync = ["sync", "--locked", "--group", "dev"]
    if consumer:
        sync = [
            "run",
            "--locked",
            "--no-sync",
            "--no-python-downloads",
            "research-repo-tools",
            "toolchain",
            "run",
            "--",
            "uv",
            "sync",
            "--locked",
            "--managed-python",
            "--group",
            "dev",
        ]
    python = [[*prefix, "deps", "update-python"], ["lock", "--upgrade"], sync]
    return {
        "update": uv + tools + rust + python,
        "update-cargo-dependencies": rust,
        "update-cargo-tools": tools,
        "update-dependencies": rust + python,
        "update-python-dependencies": python,
        "update-python-deps": python,
        "update-tools": uv + tools,
        "update-uv": uv,
    }


@pytest.mark.parametrize("cargo", [False, True], ids=["python-only", "rust"])
@pytest.mark.parametrize(
    "recipe",
    [
        "update",
        "update-cargo-dependencies",
        "update-cargo-tools",
        "update-dependencies",
        "update-python-dependencies",
        "update-python-deps",
        "update-tools",
        "update-uv",
    ],
)
def test_update_workflows_compose_once_in_order(recipes, cargo, recipe):
    justfile, consumer = recipes
    if not consumer and (cargo or recipe in {"update-cargo-dependencies", "update-cargo-tools", "update-python-deps"}):
        pytest.skip("the maintainer is a Python-only project with no legacy alias")
    if cargo:
        justfile.with_name("Cargo.toml").write_text("[workspace]\nmembers=[]\n", newline="\n")
    result, calls = invoke(justfile, recipe)
    assert result.returncode == 0, result.stderr
    assert calls == commands(consumer, cargo)[recipe]


@pytest.mark.parametrize("step", range(1, 9))
def test_update_failure_stops_all_later_steps(recipes, monkeypatch, step):
    justfile, consumer = recipes
    expected = commands(consumer, consumer)["update"]
    if step > len(expected):
        pytest.skip("maintainer has fewer update steps")
    justfile.with_name("Cargo.toml").touch()
    monkeypatch.setenv("FAIL_STEP", str(step))
    result, calls = invoke(justfile, "update")
    assert result.returncode == 23, result.stderr
    assert calls == expected[:step]


def test_consumer_owns_exclusions_and_additional_resolution_roots(recipes):
    justfile, consumer = recipes
    if not consumer:
        pytest.skip("consumer-only Cargo policy")
    source = justfile.read_text()
    source = source.replace("cargo upgrade --incompatible allow; fi", "cargo upgrade --incompatible allow --exclude coupled-a --exclude coupled-b; fi")
    extra = (
        "    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo {command} "
        '--manifest-path "fixtures/extra root/Cargo.toml"{args}\n'
    )
    start = source.rfind("\n\n", 0, source.index("\nupdate-cargo-tools:")) + 1
    source = source[:start] + extra.format(command="upgrade", args=" --incompatible allow") + extra.format(command="update", args="") + source[start:]
    justfile.write_text(source, newline="\n")
    justfile.with_name("Cargo.toml").touch()
    result, calls = invoke(justfile, "update-dependencies")
    assert result.returncode == 0, result.stderr
    cargo_calls = [call[call.index("cargo") + 1 :] for call in calls if "cargo" in call]
    assert cargo_calls == [
        ["upgrade", "--incompatible", "allow", "--exclude", "coupled-a", "--exclude", "coupled-b"],
        ["update"],
        ["upgrade", "--manifest-path", "fixtures/extra root/Cargo.toml", "--incompatible", "allow"],
        ["update", "--manifest-path", "fixtures/extra root/Cargo.toml"],
    ]


def test_opted_in_actions_stage_checks_final_dependency_pins(recipes):
    justfile, consumer = recipes
    if not consumer:
        pytest.skip("Actions maintenance is a consumer opt-in")
    source = justfile.read_text().replace("update: update-tools update-dependencies", "update: update-tools update-dependencies update-actions")
    justfile.write_text(source, newline="\n")
    result, calls = invoke(justfile, "update")
    assert result.returncode == 0, result.stderr
    assert calls == commands(True, False)["update"] + [
        [
            "run",
            "--locked",
            "--only-group",
            "tooling",
            "--inexact",
            "research-repo-tools",
            "actions",
            "update",
            "--policy",
            ".github/action-updates.toml",
            ".github/workflows",
        ]
    ]


def maintainer_checks():
    return [
        ["lock", "--check"],
        ["run", "--locked", "python", "scripts/check_newlines.py"],
        ["run", "--locked", "--no-sync", "--no-python-downloads", "research-repo-tools", "python", "check"],
        ["run", "--locked", "actionlint"],
        ["run", "--locked", "actionlint", "src/research_repo_tools/templates/publish-crates.yml"],
        ["run", "--locked", "research-repo-tools", "zizmor", "check", ".github", "src/research_repo_tools/templates/publish-crates.yml"],
    ]


def test_maintainer_ci_checks_early_and_coalesces_lock_validation(recipes):
    justfile, consumer = recipes
    if consumer:
        pytest.skip("maintainer validation tiers")
    result, calls = invoke(justfile, "ci")
    assert result.returncode == 0, result.stderr
    assert calls == maintainer_checks() + [
        ["run", "--locked", "pytest", "--cov", "--cov-report=term-missing", "--cov-report=xml:coverage/cobertura.xml"],
        ["build", "--no-sources", "--clear"],
        ["run", "--locked", "python", "scripts/check_install.py"],
    ]


@pytest.mark.parametrize("failure_step", [1, 3])
def test_maintainer_static_failure_prevents_workflow_audits_tests_and_builds(recipes, monkeypatch, failure_step):
    justfile, consumer = recipes
    if consumer:
        pytest.skip("maintainer validation tiers")
    monkeypatch.setenv("FAIL_STEP", str(failure_step))
    result, calls = invoke(justfile, "ci")
    assert result.returncode == 23, result.stderr
    assert calls == maintainer_checks()[:failure_step]


def test_maintainer_format_failure_stops_before_python_and_workflow_checks(recipes):
    justfile, consumer = recipes
    if consumer:
        pytest.skip("maintainer validation tiers")
    template = justfile.parent / "src/research_repo_tools/templates/justfile"
    source = template.read_bytes()
    malformed = source.replace(b"{{ quote(justfile()) }}", b"{{quote(justfile())}}")
    assert malformed != source
    template.write_bytes(malformed)
    result, calls = invoke(justfile, "ci")
    assert result.returncode != 0
    assert "formatted justfile differs" in result.stderr
    assert calls == [["lock", "--check"]]
