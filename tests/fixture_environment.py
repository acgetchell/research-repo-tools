"""Test-only subprocess isolation shared by source and standalone consumers."""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path


def rust_channel() -> str:
    """Read the repository pin in source or copied installed-consumer layouts."""
    directory = Path(__file__).resolve().parent
    pin = directory / "rust-toolchain.toml"
    if not pin.is_file():
        pin = directory.parent / "rust-toolchain.toml"
    channel = tomllib.loads(pin.read_text(encoding="utf-8"))["toolchain"]["channel"]
    assert isinstance(channel, str), "repository Rust toolchain channel must be a string"
    return channel


def git_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep fixture Git inside its cwd, without user configuration or hooks."""
    environment = {name: value for name, value in (os.environ if source is None else source).items() if not name.upper().startswith("GIT_")}
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_ATTR_NOSYSTEM="1",
        GIT_TEMPLATE_DIR="",
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="core.hooksPath",
        GIT_CONFIG_VALUE_0=os.devnull,
        GIT_CONFIG_KEY_1="core.attributesFile",
        GIT_CONFIG_VALUE_1=os.devnull,
    )
    return environment


def cargo_environment(home: Path) -> dict[str, str]:
    """Keep installed rustup toolchains while isolating fixture Cargo settings."""
    overrides = {"RUSTC", "RUSTDOC", "RUSTFLAGS", "RUSTDOCFLAGS", "RUSTC_BOOTSTRAP", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTUP_TOOLCHAIN"}
    environment = {name: value for name, value in os.environ.items() if not name.upper().startswith("CARGO_") and name.upper() not in overrides}
    environment.update(CARGO_HOME=str(home), RUSTUP_TOOLCHAIN=rust_channel())
    return environment
