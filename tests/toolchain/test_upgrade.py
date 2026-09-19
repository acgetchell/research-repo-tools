"""Managed upgrades preserve declarations and old installations on failure."""

import io
import json
import os
import stat
import tomllib

import pytest

from research_repo_tools import cli, config, files, toolchain
from research_repo_tools import toolchain_upgrade as upgrade


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_bytes(
        b'[project]\r\nrequires-python = ">=3.14"\r\n[tool.uv]\r\nrequired-version = "==0.12.16"\r\n'
        b"[tool.research-repo-tools.toolchain.cargo]\r\ncargo-nextest = \"0.9.100\" # retained\r\ngit-cliff = '2.14.1'\r\n"
    )
    (tmp_path / ".python-version").write_text("3.14\n")
    (tmp_path / "rust-toolchain.toml").write_text('[toolchain]\nchannel="1.98.0"\n')
    monkeypatch.setattr(toolchain.Runtime, "uv_status", lambda self: toolchain.Status("uv", "0.12.16", "0.12.16", "uv", True))
    monkeypatch.setattr(upgrade, "latest_stable", lambda package: {"cargo-nextest": "0.9.101", "git-cliff": "2.15.0"}[package])
    return manifest


def test_publishes_only_after_all_installations_verify_and_preserves_source(consumer, monkeypatch):
    original = consumer.read_bytes()
    consumer.chmod(0o640)
    mode = stat.S_IMODE(consumer.stat().st_mode)
    installed = []

    def sync(runtime):
        assert consumer.read_bytes() == original
        installed.extend((tool.package, tool.version, runtime.cargo_root(tool)) for tool in runtime.plan.cargo)

    monkeypatch.setattr(toolchain.Runtime, "sync", sync)
    assert cli.main(["--root", str(consumer.parent), "toolchain", "upgrade"]) == 0
    assert [(name, version) for name, version, _ in installed] == [("cargo-nextest", "0.9.101"), ("git-cliff", "2.15.0")]
    assert all(path.parts[-2:] == (name, version) for name, version, path in installed)
    assert consumer.read_bytes() == original.replace(b"0.9.100", b"0.9.101").replace(b"2.14.1", b"2.15.0")
    assert stat.S_IMODE(consumer.stat().st_mode) == mode


@pytest.mark.parametrize("phase", ["resolve", "install", "publish"])
def test_failures_keep_original_declarations(consumer, monkeypatch, phase):
    original = consumer.read_bytes()
    installed = []

    def resolve(package):
        if package == "git-cliff":
            raise ValueError("broken registry response")
        return "0.9.101"

    def install(runtime):
        installed.append(runtime.plan.cargo[0].version)
        if phase == "install":
            raise RuntimeError("second installer failed")

    def publish(*args):
        raise PermissionError("read-only manifest")

    monkeypatch.setattr(toolchain.Runtime, "sync", install)
    if phase == "resolve":
        monkeypatch.setattr(upgrade, "latest_stable", resolve)
    if phase == "publish":
        monkeypatch.setattr(files, "_replace_path", publish)
    with pytest.raises((ValueError, RuntimeError)):
        upgrade.upgrade(config.load(root=consumer.parent))
    assert consumer.read_bytes() == original
    assert installed == ([] if phase == "resolve" else ["0.9.101"])


@pytest.mark.parametrize("phase", ["install", "stage"])
def test_concurrent_manifest_edit_is_retained(consumer, monkeypatch, phase):
    changed = consumer.read_bytes() + b"# concurrent edit\r\n"
    monkeypatch.setattr(toolchain.Runtime, "sync", lambda self: None)
    if phase == "install":
        monkeypatch.setattr(toolchain.Runtime, "sync", lambda self: consumer.write_bytes(changed))
    else:
        stage_bytes = files._stage_bytes

        def stage(path, payload):
            staged = stage_bytes(path, payload)
            consumer.write_bytes(changed)
            return staged

        monkeypatch.setattr(files, "_stage_bytes", stage)
    with pytest.raises(RuntimeError, match="changed"):
        upgrade.upgrade(config.load(root=consumer.parent))
    assert consumer.read_bytes() == changed
    assert not list(consumer.parent.glob("*.tmp"))
    assert not list(consumer.parent.glob("*.bak"))


@pytest.mark.parametrize("found", ["0.12.15", "0.12.17", "0.12.16-rc.1", "missing"])
def test_uv_preflight_precedes_resolution_and_mutation(consumer, monkeypatch, found):
    original = consumer.read_bytes()
    monkeypatch.setattr(toolchain.Runtime, "uv_status", lambda self: toolchain.Status("uv", "0.12.16", found, "uv", False))
    monkeypatch.setattr(upgrade, "latest_stable", lambda _: pytest.fail("resolved before uv preflight"))
    with pytest.raises(ValueError, match="no changes made"):
        upgrade.upgrade(config.load(root=consumer.parent))
    assert consumer.read_bytes() == original


def test_dry_run_resolves_without_installing_or_writing(consumer, monkeypatch, capsys):
    original = consumer.read_bytes()
    monkeypatch.setattr(toolchain.Runtime, "sync", lambda _: pytest.fail("dry run installed tools"))
    upgrade.upgrade(config.load(root=consumer.parent), dry_run=True)
    assert consumer.read_bytes() == original
    assert "git-cliff: 2.14.1 -> 2.15.0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "pin,latest,expected",
    [
        ("2.14.1", "2.14.1", "2.14.1"),
        ("3.0.0-rc.2", "2.15.0", "3.0.0-rc.2"),
        ("2.15.0-rc.1+build.3", "2.15.0", "2.15.0"),
        ("2.15.0+build.3", "2.15.0+build.4", "2.15.0+build.3"),
    ],
)
def test_version_precedence_never_downgrades_or_replaces_equal_builds(consumer, monkeypatch, pin, latest, expected):
    consumer.write_text(consumer.read_text().replace("2.14.1", pin))
    monkeypatch.setattr(upgrade, "latest_stable", lambda name: latest if name == "git-cliff" else "0.9.100")
    calls = []
    monkeypatch.setattr(toolchain.Runtime, "sync", lambda self: calls.append(self.plan))
    upgrade.upgrade(config.load(root=consumer.parent))
    assert config.load(root=consumer.parent).toolchain.cargo["git-cliff"] == expected
    assert bool(calls) == (pin != expected)


def test_standalone_config_updates_its_own_pins(consumer, monkeypatch):
    standalone = consumer.parent / "settings.toml"
    standalone.write_text('[toolchain.cargo]\n"git-cliff" = "2.14.1" # keep\n')
    before = consumer.read_bytes()
    monkeypatch.setattr(toolchain.Runtime, "sync", lambda _: None)
    assert cli.main(["--config", str(standalone), "toolchain", "upgrade"]) == 0
    assert standalone.read_text() == '[toolchain.cargo]\n"git-cliff" = "2.15.0" # keep\n'
    assert consumer.read_bytes() == before


def test_unsupported_tools_fail_before_registry_or_install(consumer, monkeypatch):
    consumer.write_text(consumer.read_text().replace("git-cliff", "unknown-tool"))
    monkeypatch.setattr(upgrade, "latest_stable", lambda _: pytest.fail("unsupported tool queried"))
    with pytest.raises(ValueError, match="unsupported Cargo tool"):
        upgrade.upgrade(config.load(root=consumer.parent))


def test_registry_rejects_yanked_and_prerelease_versions_and_uses_semver_order(monkeypatch):
    entries = [
        {"name": "git-cliff", "vers": version, "yanked": yanked}
        for version, yanked in [("2.9.0", False), ("2.10.0+build.1", False), ("3.0.0", True), ("4.0.0-rc.1", False)]
    ]
    payload = "\n".join(json.dumps(entry) for entry in entries).encode()
    requests = []

    def fetch(request, timeout):
        requests.append((request.full_url, timeout))
        return io.BytesIO(payload)

    monkeypatch.setattr(upgrade.urllib.request, "urlopen", fetch)
    assert upgrade.latest_stable("git-cliff") == "2.10.0+build.1"
    assert requests == [("https://index.crates.io/gi/t-/git-cliff", 30)]


@pytest.mark.parametrize(
    "entry",
    [
        [],
        {"name": "wrong", "vers": "1.0.0", "yanked": False},
        {"name": "git-cliff", "vers": "01.0.0", "yanked": False},
        {"name": "git-cliff", "vers": "1.0.0", "yanked": "false"},
    ],
)
def test_registry_malformed_entries_fail(entry, monkeypatch):
    monkeypatch.setattr(upgrade.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(entry).encode()))
    with pytest.raises(ValueError, match="malformed registry"):
        upgrade.latest_stable("git-cliff")


def test_dotted_keys_preserve_comments_and_other_values(consumer, monkeypatch):
    text = consumer.read_text().replace("[tool.research-repo-tools.toolchain.cargo]\n", "[tool.research-repo-tools]\n")
    text = text.replace("cargo-nextest = ", "toolchain.cargo.cargo-nextest = ").replace("git-cliff = ", "toolchain.cargo.'git-cliff' = ")
    consumer.write_text(text)
    monkeypatch.setattr(toolchain.Runtime, "sync", lambda _: None)
    upgrade.upgrade(config.load(root=consumer.parent))
    assert consumer.read_text() == text.replace("0.9.100", "0.9.101").replace("2.14.1", "2.15.0")
    assert tomllib.loads(consumer.read_text())["tool"]["uv"]["required-version"] == "==0.12.16"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs elevated Windows privileges")
def test_pyproject_symlink_keeps_schema_and_target(consumer, monkeypatch):
    target = consumer.with_name("manifest.toml")
    consumer.rename(target)
    consumer.symlink_to(target)
    monkeypatch.setattr(toolchain.Runtime, "sync", lambda _: None)
    upgrade.upgrade(config.load(root=consumer.parent))
    assert consumer.is_symlink()
    assert config.load(root=consumer.parent).toolchain.cargo["git-cliff"] == "2.15.0"
    assert consumer.read_bytes() == target.read_bytes()
