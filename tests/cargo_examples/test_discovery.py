"""Fail-closed Cargo metadata, configuration and artifact regressions."""

import copy
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from research_repo_tools import cargo_examples
from research_repo_tools.cargo_examples import discover_examples, run_examples


@pytest.fixture
def metadata(tmp_path: Path) -> dict:
    return {
        "version": 1,
        "workspace_members": ["fixture-id"],
        "packages": [
            {
                "id": "fixture-id",
                "name": "fixture",
                "targets": [
                    {"name": "flat", "kind": ["example"], "crate_types": ["bin"], "src_path": str(tmp_path / "examples/flat.rs")},
                    {"name": "nested", "kind": ["example"], "crate_types": ["bin"], "src_path": str(tmp_path / "examples/nested/main.rs")},
                    {"name": "library", "kind": ["example"], "crate_types": ["rlib"], "src_path": str(tmp_path / "examples/lib.rs")},
                ],
            }
        ],
    }


def response(value: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["cargo"], 0, json.dumps(value), "")


@pytest.mark.parametrize(
    ("field", "value"),
    [("version", True), ("version", 2), ("workspace_members", []), ("workspace_members", ["unknown"]), ("packages", []), ("packages", {})],
)
def test_malformed_metadata_fails_closed(tmp_path, metadata, monkeypatch, field, value):
    metadata[field] = value
    runner = Mock(return_value=response(metadata))
    monkeypatch.setattr(cargo_examples, "run_command", runner)
    with pytest.raises(ValueError):
        discover_examples(tmp_path)
    runner.assert_called_once()


@pytest.mark.parametrize("stdout", ["", "not json", "[]", '{"version":1,"version":1}', '{"version":NaN}'])
def test_invalid_json_never_becomes_empty_success(tmp_path, monkeypatch, stdout):
    monkeypatch.setattr(cargo_examples, "run_command", Mock(return_value=subprocess.CompletedProcess(["cargo"], 0, stdout, "")))
    with pytest.raises(ValueError):
        discover_examples(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", []),
        ("kind", "example"),
        ("kind", ["example", "bin"]),
        ("crate_types", None),
        ("crate_types", ["bin", "rlib"]),
        ("crate_types", ["unknown"]),
        ("name", ""),
        ("src_path", "relative.rs"),
        ("required-features", [1]),
    ],
)
def test_malformed_example_fails_closed(tmp_path, metadata, monkeypatch, field, value):
    metadata["packages"][0]["targets"][0][field] = value
    monkeypatch.setattr(cargo_examples, "run_command", Mock(return_value=response(metadata)))
    with pytest.raises(ValueError):
        discover_examples(tmp_path)


def test_empty_duplicate_and_library_only_discovery_are_rejected(tmp_path, metadata, monkeypatch):
    targets = metadata["packages"][0]["targets"]
    for inventory in ([], [targets[0], targets[0]], [targets[2]]):
        metadata["packages"][0]["targets"] = inventory
        monkeypatch.setattr(cargo_examples, "run_command", Mock(return_value=response(metadata)))
        with pytest.raises(ValueError, match="nonempty, unique"):
            discover_examples(tmp_path)


def test_workspace_selection_is_explicit_and_deterministic(tmp_path, metadata, monkeypatch):
    second = copy.deepcopy(metadata["packages"][0])
    second.update(id="second-id", name="second")
    metadata["packages"].append(second)
    metadata["workspace_members"].append("second-id")
    monkeypatch.setattr(cargo_examples, "run_command", Mock(return_value=response(metadata)))
    with pytest.raises(ValueError, match="select exactly one"):
        discover_examples(tmp_path)
    result = discover_examples(tmp_path, package="second")
    assert [example.name for example in result] == ["flat", "nested"]
    assert {example.package_id for example in result} == {"second-id"}
    with pytest.raises(ValueError, match="select exactly one"):
        discover_examples(tmp_path, package="absent")


@pytest.mark.parametrize(
    "configuration",
    [
        "schema=true",
        "schema=2",
        "schema=1\nunknown=true",
        "schema=1\ninclude=[]",
        "schema=1\ntimeout=0",
        "schema=1\nbuild-timeout=-1",
        "schema=1\nmetadata-timeout=true",
        "schema=1\nexclude=[1]",
        "schema=1\nexclude=['flat','flat']",
        "schema=1\n[examples.flat]\nfeatures=['a,b']",
        "schema=1\n[examples.flat]\nno-default-features=1",
        "schema=1\n[examples.flat]\nexpect=['']",
        "schema=1\n[examples.flat]\nargs=[3]",
        "schema=1\n[examples.flat]\nunknown=true",
    ],
)
def test_invalid_policy_fails_before_any_command(tmp_path, monkeypatch, configuration):
    (tmp_path / "examples.toml").write_text(configuration, encoding="utf-8", newline="\n")
    runner = Mock()
    monkeypatch.setattr(cargo_examples, "run_command", runner)
    with pytest.raises(ValueError):
        run_examples(tmp_path, "examples.toml")
    runner.assert_not_called()


@pytest.mark.parametrize("selection", ["include=['absent']", "exclude=['absent']", "exclude=['flat','nested']", "[examples.absent]\nfeatures=['x']"])
def test_unknown_or_empty_selection_fails_before_build(tmp_path, metadata, monkeypatch, selection):
    (tmp_path / "examples.toml").write_text("schema=1\n" + selection, encoding="utf-8", newline="\n")
    runner = Mock(return_value=response(metadata))
    monkeypatch.setattr(cargo_examples, "run_command", runner)
    with pytest.raises(ValueError):
        run_examples(tmp_path, "examples.toml")
    runner.assert_called_once()


@pytest.mark.parametrize("failure", [subprocess.CalledProcessError(17, ["cargo"], b"partial", b"error"), subprocess.TimeoutExpired(["cargo"], 1)])
def test_discovery_and_build_errors_are_preserved(tmp_path, metadata, monkeypatch, failure):
    (tmp_path / "examples.toml").write_text("schema=1", encoding="utf-8", newline="\n")
    for replies in ([failure], [response(metadata), failure]):
        monkeypatch.setattr(cargo_examples, "run_command", Mock(side_effect=replies))
        with pytest.raises(type(failure)) as raised:
            run_examples(tmp_path, "examples.toml")
        assert raised.value is failure


@pytest.mark.parametrize("mode", ["empty", "malformed", "wrong-package", "relative", "duplicate", "no-executable"])
def test_missing_or_malformed_build_artifacts_never_run(tmp_path, metadata, monkeypatch, mode):
    (tmp_path / "examples.toml").write_text("schema=1\ninclude=['flat']", encoding="utf-8", newline="\n")
    artifact = {
        "reason": "compiler-artifact",
        "package_id": "fixture-id",
        "target": metadata["packages"][0]["targets"][0],
        "executable": str(tmp_path / "native-binary.exe"),
    }
    if mode == "wrong-package":
        artifact["package_id"] = "foreign"
    if mode == "relative":
        artifact["executable"] = "native-binary.exe"
    if mode == "no-executable":
        artifact["executable"] = None
    stdout = json.dumps(artifact)
    if mode == "empty":
        stdout = ""
    if mode == "malformed":
        stdout = "not-json"
    if mode == "duplicate":
        stdout += "\n" + stdout
    monkeypatch.setattr(cargo_examples, "run_command", Mock(side_effect=[response(metadata), subprocess.CompletedProcess(["cargo"], 0, stdout, "")]))
    execute = Mock()
    monkeypatch.setattr(cargo_examples, "run_command_live", execute)
    with pytest.raises(ValueError):
        run_examples(tmp_path, "examples.toml")
    execute.assert_not_called()


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_separators_inside_cargo_json_are_not_record_boundaries(tmp_path, metadata, monkeypatch, separator):
    (tmp_path / "examples.toml").write_text("schema=1\ninclude=['flat']", encoding="utf-8", newline="\n")
    executable = tmp_path / f"directory{separator}name" / "flat"
    artifact = {
        "reason": "compiler-artifact",
        "package_id": "fixture-id",
        "target": metadata["packages"][0]["targets"][0],
        "executable": str(executable),
    }
    stdout = json.dumps(artifact, ensure_ascii=False) + '\n{"reason":"build-finished","success":true}\n'
    assert separator in stdout and stdout.count("\n") == 2
    monkeypatch.setattr(cargo_examples, "run_command", Mock(side_effect=[response(metadata), subprocess.CompletedProcess(["cargo"], 0, stdout, "")]))
    execute = Mock()
    monkeypatch.setattr(cargo_examples, "run_command_live", execute)
    run_examples(tmp_path, "examples.toml")
    assert execute.call_args.args[0] == executable
    execute.assert_called_once()
