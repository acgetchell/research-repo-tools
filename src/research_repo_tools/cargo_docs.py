"""Validate public Rust library documentation from Cargo's packaged sources."""

import hashlib
import json
import os
import re
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.archives import extract_archive
from research_repo_tools.process import run_command
from research_repo_tools.tool_pins import STABLE

__all__ = ["DocumentationEvidence", "check_documentation"]


@dataclass(frozen=True, slots=True)
class DocumentationEvidence:
    """Identity and explicit settings of a successful packaged-docs build."""

    package: str
    version: str
    archive_sha256: str
    source_manifest_sha256: str
    source_lock_sha256: str
    toolchain: str
    compiler: str
    target: str
    features: tuple[str, ...]
    default_features: bool
    all_features: bool
    cfg: tuple[str, ...]
    configuration_sha256: str


def _configuration_digest(root: Path, env: dict[str, str]) -> str:
    # Include both discovered config files and inherited compiler/doc flags in
    # the evidence without disclosing registry credentials or config contents.
    cargo_home = (root / env["CARGO_HOME"]).resolve() if env.get("CARGO_HOME") else Path.home() / ".cargo"
    directories = [cargo_home, *(parent / ".cargo" for parent in (*reversed(root.parents), root))]
    files = []
    for directory in directories:
        for name in ("config", "config.toml"):
            path = directory / name
            if not path.is_file():
                continue
            payload = path.read_bytes()
            config = tomllib.loads(payload.decode("utf-8"))
            if any(key.upper() == "RUSTC_BOOTSTRAP" for key in config.get("env", {})):
                raise ValueError(f"stable Cargo documentation requires RUSTC_BOOTSTRAP to be unset in {path}")
            files.append(hashlib.sha256(payload).hexdigest())
    flags = {
        key: value
        for key, value in env.items()
        if key.upper() in {"RUSTFLAGS", "RUSTDOCFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_ENCODED_RUSTDOCFLAGS"}
        or key.upper().startswith(("CARGO_BUILD_RUST", "CARGO_TARGET_"))
    }
    return hashlib.sha256(json.dumps([files, flags], sort_keys=True).encode("utf-8")).hexdigest()


def _options(values: object, context: str) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"Cargo documentation {context} must be a sequence of nonempty strings without NUL")
    result = []
    for value in values:
        if not isinstance(value, str) or not value or "\0" in value:
            raise ValueError(f"Cargo documentation {context} must be a sequence of nonempty strings without NUL")
        result.append(value)
    return tuple(result)


def _target(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+", value) is None:
        raise ValueError("Cargo documentation requires a Rust target triple")
    return value


def check_documentation(
    root: Path,
    package: str,
    *,
    features: Sequence[str] = (),
    default_features: bool = True,
    all_features: bool = False,
    cfg: Sequence[str] = (),
    target: str | None = None,
) -> DocumentationEvidence:
    """Package one workspace member and build its library docs with warnings denied.

    Requires Cargo.lock and an already installed release from rust-toolchain.toml.
    Cargo reads consumer configuration from root. Builds and unpacked sources
    live in a disposable directory. Cargo diagnostics propagate on failure.
    """
    if not isinstance(package, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", package):
        raise ValueError("Cargo documentation requires an exact package name")
    features, cfg = _options(features, "features"), _options(cfg, "cfg")
    if type(default_features) is not bool or type(all_features) is not bool:
        raise ValueError("Cargo documentation feature switches must be boolean")
    if target is not None:
        _target(target)
    if all_features and (features or not default_features):
        raise ValueError("--all-features cannot be combined with explicit features or --no-default-features")
    if any(not name or name.startswith("-") or re.search(r"[\s,]", name) for name in features):
        raise ValueError("provide one Cargo feature name per --features option")
    root = root.resolve()
    toolchain = tomllib.loads((root / "rust-toolchain.toml").read_text(encoding="utf-8")).get("toolchain")
    channel = toolchain.get("channel") if isinstance(toolchain, dict) else None
    if not isinstance(channel, str) or not STABLE.fullmatch(channel):
        raise ValueError("rust-toolchain.toml channel must pin an exact stable X.Y.Z release for documentation")
    env = dict(os.environ)
    if "RUSTC_BOOTSTRAP" in env or "CARGO_ENV_RUSTC_BOOTSTRAP" in env:
        raise ValueError("stable Cargo documentation requires RUSTC_BOOTSTRAP to be unset")
    env.update(RUSTUP_AUTO_INSTALL="0", RUSTUP_NO_UPDATE_CHECK="1", RUSTUP_TOOLCHAIN=channel)
    tools = {}
    for name in ("cargo", "rustc", "rustdoc"):
        path = Path(run_command("rustup", ["which", "--toolchain", channel, name], cwd=root, env=env, timeout=30).stdout.strip())
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"rustup did not locate {name} for stable Rust {channel}")
        tools[name] = path
    compiler = run_command(tools["rustc"], ["--version", "--verbose"], cwd=root, env=env, timeout=30).stdout.strip()
    if f"release: {channel}" not in compiler.splitlines():
        raise ValueError(f"rustc does not report the declared stable release {channel}: {compiler}")
    if target is None:
        target = next((line.removeprefix("host: ") for line in compiler.splitlines() if line.startswith("host: ")), "")
    _target(target)
    env.update(RUSTC=str(tools["rustc"]), RUSTDOC=str(tools["rustdoc"]), RUSTC_WRAPPER="", RUSTC_WORKSPACE_WRAPPER="")
    configuration = _configuration_digest(root, env)
    flags = ["--all-features"] if all_features else [arg for name in features for arg in ("--features", name)]
    if not default_features:
        flags.append("--no-default-features")
    rustdoc_flags = [arg for value in cfg for arg in ("--cfg", value)]

    with tempfile.TemporaryDirectory(prefix="research-cargo-docs-") as directory:
        scratch = Path(directory)
        build = scratch / "target"
        env["CARGO_BUILD_BUILD_DIR"] = str(scratch / "build")
        source = root / "Cargo.toml"
        workspace = json.loads(
            run_command(
                tools["cargo"],
                ["metadata", "--locked", "--no-deps", "--format-version", "1", "--manifest-path", str(source)],
                cwd=root,
                env=env,
                timeout=60,
            ).stdout
        )
        members = set(workspace["workspace_members"])
        matches = [item for item in workspace["packages"] if item["name"] == package and item["id"] in members]
        if len(matches) != 1:
            raise ValueError(f"Cargo workspace does not contain exactly one package named {package}")
        member = matches[0]
        library = next((item["name"] for item in member["targets"] if set(item["kind"]) & {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}), None)
        if library is None:
            raise ValueError(f"Cargo package {package} has no library target to document")
        manifest = Path(member["manifest_path"])
        lock = Path(workspace["workspace_root"]) / "Cargo.lock"
        if not lock.is_file():
            raise ValueError("Cargo documentation requires the workspace Cargo.lock")
        manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        lock_digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        common = ["--locked", "--target-dir", str(build)]
        run_command(
            tools["cargo"],
            ["package", *common, "--manifest-path", str(source), "--package", package, "--allow-dirty", "--no-verify", *flags],
            cwd=root,
            env=env,
            timeout=300,
        )
        version = member["version"]
        crate = f"{package}-{version}"
        archive = build / "package" / f"{crate}.crate"
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        unpacked = scratch / "unpacked"
        extract_archive(archive, unpacked, expected_sha256=digest)
        packaged = unpacked / crate / "Cargo.toml"
        run_command(
            tools["cargo"],
            [
                "rustdoc",
                *common,
                "--manifest-path",
                str(packaged),
                "--target",
                target,
                "--lib",
                *flags,
                "--",
                *rustdoc_flags,
                "-D",
                "warnings",
            ],
            cwd=root,
            env=env,
            timeout=600,
        )
        if not (build / target / "doc" / library.replace("-", "_") / "index.html").is_file():
            raise ValueError(f"rustdoc did not generate public HTML for {package}")
    return DocumentationEvidence(
        package, version, digest, manifest_digest, lock_digest, channel, compiler, target, features, default_features, all_features, cfg, configuration
    )
