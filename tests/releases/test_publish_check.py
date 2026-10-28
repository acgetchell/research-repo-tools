"""PyPI checks reject stale artifacts and real Twine warnings without publication."""

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from research_repo_tools.just_inspect import dry_run

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location("check_publish", ROOT / "scripts/check_publish.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(root: Path, *, content_type: bool = True, description: bool = True, metadata_version: str = "1.2.3") -> Path:
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="research-repo-tools"\nversion="1.2.3"\n', encoding="utf-8", newline="\n")
    dist = root / "dist"
    dist.mkdir()
    (dist / ".gitignore").write_bytes(b"*")
    metadata = f"Metadata-Version: 2.4\nName: research-repo-tools\nVersion: {metadata_version}\nSummary: Publication fixture\n"
    if content_type:
        metadata += "Description-Content-Type: text/markdown\n"
    metadata += "\n" + ("A small publication fixture.\n" if description else "")
    with zipfile.ZipFile(dist / "research_repo_tools-1.2.3-py3-none-any.whl", "w") as archive:
        prefix = "research_repo_tools-1.2.3.dist-info"
        archive.writestr(f"{prefix}/METADATA", metadata.encode("utf-8"))
        archive.writestr(f"{prefix}/WHEEL", b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{prefix}/RECORD", b"")
    with tarfile.open(dist / "research_repo_tools-1.2.3.tar.gz", "w:gz") as archive:
        payload = metadata.encode("utf-8")
        member = tarfile.TarInfo("research_repo_tools-1.2.3/PKG-INFO")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
        # A real sdist has a project root, not a sole PKG-INFO member: Twine
        # discovers that root using the common path of the stored member names.
        payload = (root / "pyproject.toml").read_bytes()
        member = tarfile.TarInfo("research_repo_tools-1.2.3/pyproject.toml")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
        assert archive.getnames() == ["research_repo_tools-1.2.3/PKG-INFO", "research_repo_tools-1.2.3/pyproject.toml"]
    return root


def snapshot(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_valid_distributions_pass_without_modification(tmp_path, checker, capfd):
    root = project(tmp_path / "release café with spaces")
    before = snapshot(root)
    assert checker.check(root) == 0
    assert snapshot(root) == before
    output = capfd.readouterr().out
    compact = "".join(output.split())  # Twine wraps long paths to the terminal width.
    assert "research_repo_tools-1.2.3-py3-none-any.whl" in compact
    assert "research_repo_tools-1.2.3.tar.gz" in compact
    assert output.count("PASSED") == 2


@pytest.mark.parametrize("missing", ["content_type", "description"])
def test_twine_warnings_fail_strict_check(tmp_path, checker, missing, capfd):
    root = project(tmp_path / "release", content_type=missing != "content_type", description=missing != "description")
    before = snapshot(root)
    assert checker.check(root) != 0
    assert "FAILED due to warnings" in capfd.readouterr().out
    assert snapshot(root) == before


@pytest.mark.parametrize("damage", ["missing-directory", "empty", "missing-wheel", "old-version", "extra-artifact", "metadata-version", "filename-case"])
def test_incomplete_or_stale_artifacts_fail_before_twine(tmp_path, checker, damage, monkeypatch):
    root = project(tmp_path / "release", metadata_version="1.2.2" if damage == "metadata-version" else "1.2.3")
    dist = root / "dist"
    if damage in {"missing-directory", "empty"}:
        for artifact in dist.iterdir():
            artifact.unlink()
        if damage == "missing-directory":
            dist.rmdir()
    elif damage == "missing-wheel":
        (dist / "research_repo_tools-1.2.3-py3-none-any.whl").unlink()
    elif damage == "old-version":
        (root / "pyproject.toml").write_text('[project]\nname="research-repo-tools"\nversion="1.2.4"\n', encoding="utf-8", newline="\n")
    elif damage == "extra-artifact":
        (dist / "obsolete.whl").write_bytes(b"obsolete artifact")
    elif damage == "filename-case":
        (dist / "research_repo_tools-1.2.3-py3-none-any.whl").rename(dist / "Research_Repo_Tools-1.2.3-py3-none-any.whl")

    def unexpected_twine(*args, **kwargs):
        pytest.fail("invalid artifact inventory reached Twine")

    monkeypatch.setattr(checker, "run_safe_command", unexpected_twine)
    before = snapshot(root)
    with pytest.raises(ValueError):
        checker.check(root)
    assert snapshot(root) == before


def test_local_and_hosted_ci_check_once_after_build_before_retaining_artifacts():
    standalone = dry_run(ROOT, "publish-check").stderr
    assert "scripts/check_publish.py" in standalone
    assert "uv build" not in standalone
    aggregate = dry_run(ROOT, "ci").stderr
    assert aggregate.count("uv build --no-sources --clear") == 1
    assert aggregate.count("scripts/check_publish.py") == 1
    assert aggregate.index("uv build") < aggregate.index("scripts/check_publish.py") < aggregate.index("pytest --cov")
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["build"]["steps"]
    checked = next(index for index, step in enumerate(steps) if step.get("name") == "Build release distributions once")
    uploaded = next(index for index, step in enumerate(steps) if step.get("id") == "distributions")
    assert steps[checked]["run"] == "uv run --locked just build publish-check"
    assert checked < uploaded
