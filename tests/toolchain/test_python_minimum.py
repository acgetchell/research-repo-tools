"""Mandatory installed-release support, independent of development opt-ins."""

import tomllib
from email.message import Message

import pytest
from packaging.specifiers import SpecifierSet

from research_repo_tools import cli, config, python_baseline, toolchain_config
from research_repo_tools import python_adoption as adoption
from tests.toolchain.test_python_adoption import consumer as consumer
from tests.toolchain.test_python_adoption import snapshot


@pytest.mark.parametrize("package", [True, False])
@pytest.mark.parametrize("opt_in", [None, False, True])
@pytest.mark.parametrize("command", [["toolchain", "python-check"], ["toolchain", "check"], ["setup"], ["python", "check"]])
def test_stale_application_metadata_fails_all_normal_gates_without_effects(consumer, package, opt_in, command, capsys):
    root, calls = consumer
    path = root / "pyproject.toml"
    text = path.read_bytes().decode().replace("package = false", f"package = {str(package).lower()}")
    text = text.replace("inherit-python = true", "" if opt_in is None else f"inherit-python = {str(opt_in).lower()}")
    path.write_bytes(text.encode())
    before = snapshot(root)
    assert cli.main(["--root", str(root), *command]) == 1
    error = capsys.readouterr().err
    assert "project.requires-python" in error and "toolchain adopt --dry-run" in error
    assert not calls and snapshot(root) == before


@pytest.mark.parametrize("package", [True, False])
@pytest.mark.parametrize("opt_in", [None, False])
def test_no_opt_in_adoption_reconciles_metadata_selector_and_exact_extras(consumer, package, opt_in):
    root, _ = consumer
    path = root / "pyproject.toml"
    text = path.read_bytes().decode().replace("package = false", f"package = {str(package).lower()}")
    text = text.replace("inherit-python = true", "" if opt_in is None else "inherit-python = false")
    path.write_bytes(text.encode())
    before = snapshot(root)
    plan = adoption.plan_python_adoption(config.load(root=root))
    assert snapshot(root) == before
    adoption.apply_python_adoption(plan)
    document = tomllib.loads(path.read_bytes().decode())
    assert "3.14.99" not in SpecifierSet(document["project"]["requires-python"])
    assert "3.15" in SpecifierSet(document["project"]["requires-python"])
    assert document["dependency-groups"]["notebook"] == ["research-repo-tools[notebooks]==0.1.7"]
    assert (root / ".python-version").read_bytes() == b"3.15\r\n"
    assert "target-version" not in document["tool"]["ruff"]
    python_baseline.check_minimum(root)
    after = snapshot(root)
    repeat = adoption.plan_python_adoption(config.load(root=root))
    assert not repeat.changed_paths
    adoption.apply_python_adoption(repeat)
    assert snapshot(root) == after


@pytest.mark.parametrize("requirement", [">=3.15.2,<3.16,!=3.15.7", ">3.15.3", "~=3.15.2", "==3.15.*"])
def test_stricter_consumer_ranges_are_preserved_exactly(consumer, requirement):
    root, _ = consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes().replace(b">=3.14", requirement.encode()))
    plan = adoption.plan_python_adoption(config.load(root=root))
    document = tomllib.loads(dict(plan.replacements)["pyproject.toml"].decode())
    assert document["project"]["requires-python"] == requirement


@pytest.mark.parametrize("requirement", ["<3.15", ">=3.12,!=3.15.*", "<=3.14.99", ">=3.15,<3.15"])
def test_conflicting_application_ranges_reject_before_resolution(consumer, requirement):
    root, calls = consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes().replace(b">=3.14", requirement.encode()))
    before = snapshot(root)
    with pytest.raises(ValueError, match="conflicts|excludes shared development"):
        adoption.plan_python_adoption(config.load(root=root))
    assert not calls and snapshot(root) == before


def test_unrelated_application_and_group_bounds_survive_adoption(consumer):
    root, _ = consumer
    path = root / "pyproject.toml"
    text = path.read_bytes().decode().replace("package = false", "package = true").replace(">=3.14", ">=3.14,<3.17,!=3.15.2")
    text += '\r\n[tool.uv.dependency-groups]\r\ntooling = {requires-python=">=3.14,<3.16,!=3.15.4"}\r\n'
    path.write_bytes(text.encode())
    plan = adoption.plan_python_adoption(config.load(root=root))
    document = tomllib.loads(dict(plan.replacements)["pyproject.toml"].decode())
    assert document["project"]["requires-python"] == ">=3.14,<3.17,!=3.15.2,>=3.15"
    constraint = document["tool"]["uv"]["dependency-groups"]["tooling"]["requires-python"]
    assert "3.15.3" in SpecifierSet(constraint)
    assert "3.15.4" not in SpecifierSet(constraint) and "3.16" not in SpecifierSet(constraint)


def test_nested_tooling_constraints_retain_table_and_comments(consumer):
    root, _ = consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes() + b'\r\n[tool.uv.dependency-groups.tooling]\r\nrequires-python = ">=3.14,<3.16" # keep restriction\r\n')
    plan = adoption.plan_python_adoption(config.load(root=root))
    text = dict(plan.replacements)["pyproject.toml"]
    assert b"# keep restriction\r\n" in text
    document = tomllib.loads(text.decode())
    requirement = document["tool"]["uv"]["dependency-groups"]["tooling"]["requires-python"]
    assert SpecifierSet(requirement) == SpecifierSet(">=3.14,>=3.15,<3.16")


def test_support_minimum_does_not_follow_newer_development_selector(consumer, monkeypatch):
    root, _ = consumer
    monkeypatch.setattr(python_baseline, "baseline", lambda: python_baseline.PythonBaseline(">=3.14", "3.15", "0.1.7"))
    plan = adoption.plan_python_adoption(config.load(root=root))
    document = tomllib.loads(dict(plan.replacements)["pyproject.toml"].decode())
    assert document["project"]["requires-python"] == ">=3.14"
    assert dict(plan.replacements)[".python-version"] == b"3.15\r\n"


def test_stricter_local_selector_survives_without_development_mirroring(consumer):
    root, _ = consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes().replace(b">=3.14", b">=3.16,<3.17").replace(b"inherit-python = true", b"inherit-python = false"))
    (root / ".python-version").write_bytes(b"3.16\r\n")
    plan = adoption.plan_python_adoption(config.load(root=root))
    assert plan.tools.python == "3.16"
    assert tomllib.loads(dict(plan.replacements)["pyproject.toml"].decode())["project"]["requires-python"] == ">=3.16,<3.17"


def test_baseline_reads_exact_installed_metadata_not_checkout_or_interpreter(monkeypatch):
    published = Message()
    published["Requires-Python"] = ">=3.14.2,<3.16,!=3.14.7"
    monkeypatch.setattr(python_baseline, "metadata", lambda name: published if name == "research-repo-tools" else pytest.fail(name))
    monkeypatch.setattr(python_baseline, "version", lambda name: "0.1.8")
    authority = python_baseline.baseline()
    assert authority.minimum_requirement == ">=3.14.2"
    assert authority.selected == "3.14" and authority.package_version == "0.1.8"
    assert python_baseline.reconcile_requirement(">=3.13,<3.17,!=3.15.1", authority) == ">=3.13,<3.17,!=3.15.1,>=3.14.2"


@pytest.mark.parametrize("requires", ["<3.16", "!=3.13.*", ""])
def test_published_metadata_must_declare_a_minimum(requires):
    with pytest.raises(ValueError, match="must declare a minimum"):
        python_baseline.PythonBaseline(requires, "3.14", "0.1.8")


def test_unopted_selected_interpreter_cannot_bypass_raised_floor(consumer):
    root, _ = consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes().replace(b">=3.14", b">=3.15").replace(b"inherit-python = true", b"inherit-python = false"))
    with pytest.raises(ValueError, match="toolchain adopt --dry-run"):
        toolchain_config.load(config.load(root=root))
    with pytest.raises(ValueError, match=".python-version.*toolchain adopt --dry-run"):
        python_baseline.check_minimum(root)


@pytest.mark.parametrize("package", [True, False])
def test_conflicting_tooling_bounds_reject_without_edits_or_resolution(consumer, package):
    root, calls = consumer
    path = root / "pyproject.toml"
    text = path.read_bytes().decode().replace("package = false", f"package = {str(package).lower()}")
    text += '\r\n[tool.uv.dependency-groups]\r\ntooling = {requires-python="<3.15"}\r\n'
    path.write_bytes(text.encode())
    before = snapshot(root)
    with pytest.raises(ValueError, match="tool.uv.dependency-groups.tooling.requires-python conflicts"):
        adoption.plan_python_adoption(config.load(root=root))
    assert not calls and snapshot(root) == before


@pytest.mark.parametrize(
    "pin",
    [
        "research-repo-tools>=0.1.6",
        "research-repo-tools==0.1.*",
        "research-repo-tools==0.1.6; python_version>='3.14'",
        "research-repo-tools @ https://example.invalid/release.whl",
    ],
)
def test_adoption_rejects_nonexact_or_nonregistry_package_authority(consumer, pin):
    root, calls = consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes().replace(b"research-repo-tools==0.1.6", pin.encode()))
    before = snapshot(root)
    with pytest.raises(ValueError, match="exact registry package pins"):
        adoption.plan_python_adoption(config.load(root=root))
    assert not calls and snapshot(root) == before


def test_missing_application_metadata_is_created_by_adoption_but_rejected_by_checks(consumer):
    root, _ = consumer
    path = root / "pyproject.toml"
    path.write_bytes(path.read_bytes().replace(b'requires-python = ">=3.14"\r\n', b""))
    with pytest.raises(ValueError, match="project.requires-python must enforce"):
        python_baseline.check_minimum(root)
    plan = adoption.plan_python_adoption(config.load(root=root))
    assert tomllib.loads(dict(plan.replacements)["pyproject.toml"].decode())["project"]["requires-python"] == ">=3.15"
