"""Parse the shared toolchain contract before running any installers."""

import os
import platform
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import SpecifierSet

from research_repo_tools.config import Config
from research_repo_tools.tool_pins import SEMVER, STABLE

# Package -> executable and the arguments needed for its version command.
# Cargo subcommands need their subcommand argument even when invoked directly.
CARGO_TOOLS = {
    "git-cliff": ("git-cliff", ("--version",)),
    "rumdl": ("rumdl", ("--version",)),
    "cargo-nextest": ("cargo-nextest", ("nextest", "--version")),
    "cargo-llvm-cov": ("cargo-llvm-cov", ("llvm-cov", "--version")),
    "cargo-edit": ("cargo-upgrade", ("upgrade", "--version")),
    "dprint": ("dprint", ("--version",)),
    "taplo-cli": ("taplo", ("--version",)),
    "typos-cli": ("typos", ("--version",)),
    "zizmor": ("zizmor", ("--version",)),
}
RUSTUP_VERSION = "1.29.1"


@dataclass(frozen=True)
class CargoTool:
    package: str
    version: str
    binary: str
    version_args: tuple[str, ...]

    @property
    def version_label(self) -> str:
        return {"cargo-edit": "cargo-edit-upgrade", "typos-cli": "typos-cli"}.get(self.package, self.binary)


@dataclass(frozen=True)
class RustToolchain:
    channel: str
    components: tuple[str, ...]
    targets: tuple[str, ...]
    profile: str


@dataclass(frozen=True)
class Toolchain:
    root: Path
    uv: str
    python: str
    rust: RustToolchain | None
    cargo: tuple[CargoTool, ...]


def _names(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not re.fullmatch(r"[a-zA-Z0-9]+(?:[-_][a-zA-Z0-9]+)*", item) for item in value):
        raise ValueError(f"{field} must be a list of component/target names")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} contains duplicate names")
    return tuple(value)


def load(settings: Config) -> Toolchain:
    """Require exact pins; floating toolchain updates belong to an explicit upgrade."""
    root = settings.root
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        uv = document["tool"]["uv"]["required-version"]
    except (KeyError, TypeError) as error:
        raise ValueError('toolchain setup requires [tool.uv] required-version = "==X.Y.Z"') from error
    if not isinstance(uv, str) or not uv.startswith("==") or not STABLE.fullmatch(uv[2:]):
        raise ValueError("tool.uv.required-version must pin exactly ==X.Y.Z")
    if tuple(map(int, uv[2:].split("."))) < (0, 12, 10):
        raise ValueError("toolchain setup requires uv 0.12.10 or newer")
    python = (root / ".python-version").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"3\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?", python) or int(python.split(".")[1]) < 14:
        raise ValueError(".python-version must select Python 3.14 or newer as 3.MINOR[.PATCH]")
    project = document.get("project", {})
    if not isinstance(project, dict):
        raise ValueError("project must be a table")
    requires = project.get("requires-python", ">=3.14")
    if not isinstance(requires, str) or python not in SpecifierSet(requires):
        raise ValueError(".python-version must satisfy project.requires-python")
    rust = None
    rust_file = root / "rust-toolchain.toml"
    if rust_file.exists():
        rust_document = tomllib.loads(rust_file.read_text(encoding="utf-8"))
        table = rust_document.get("toolchain")
        if set(rust_document) != {"toolchain"} or not isinstance(table, dict) or set(table) - {"channel", "components", "targets", "profile"}:
            raise ValueError("rust-toolchain.toml requires a toolchain table with channel, components, targets, and profile only")
        channel = table.get("channel")
        if not isinstance(channel, str) or not STABLE.fullmatch(channel):
            raise ValueError("rust-toolchain.toml channel must pin a stable X.Y.Z release")
        profile = table.get("profile", "minimal")
        if not isinstance(profile, str) or profile not in {"minimal", "default"}:
            raise ValueError("rust-toolchain.toml profile must be minimal or default")
        components = _names(table.get("components", []), "toolchain.components")
        # These are the documented default profile additions to the minimal profile.
        if profile == "default":
            components = tuple(sorted(set(components) | {"clippy", "rustfmt", "rust-docs"}))
        rust = RustToolchain(channel, components, _names(table.get("targets", []), "toolchain.targets"), profile)
    tools = settings.section("toolchain").get("cargo", {})
    if not isinstance(tools, dict):
        raise ValueError("toolchain.cargo must map supported Cargo package names to exact versions")
    cargo = []
    for package, version in sorted(tools.items()):
        if package not in CARGO_TOOLS:
            raise ValueError(f"unsupported Cargo tool {package!r}; supported packages: {', '.join(CARGO_TOOLS)}; just is supplied by rust-just")
        if not isinstance(version, str) or not SEMVER.fullmatch(version):
            raise ValueError(f"toolchain.cargo.{package} must pin a canonical SemVer")
        binary, args = CARGO_TOOLS[package]
        cargo.append(CargoTool(package, version, binary, args))
    if cargo and rust is None:
        raise ValueError("Cargo tools require a pinned rust-toolchain.toml")
    return Toolchain(root, uv[2:], python, rust, tuple(cargo))


def host_target() -> str:
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(machine)
    system = platform.system()
    suffix = {"Darwin": "apple-darwin", "Linux": "unknown-linux-gnu", "Windows": "pc-windows-msvc"}.get(system)
    if arch is None or suffix is None or (system == "Linux" and platform.libc_ver()[0] != "glibc"):
        raise ValueError("toolchain installation supports x86_64/aarch64 macOS, glibc Linux, and Windows MSVC")
    return f"{arch}-{suffix}"


def home() -> Path:
    """Use one cache convention shared with both generated bootstrap launchers."""
    override = os.environ.get("RESEARCH_REPO_TOOLS_HOME")
    if override:
        result = Path(override).expanduser()
        if not result.is_absolute():
            raise ValueError("RESEARCH_REPO_TOOLS_HOME must be absolute")
        return result
    return Path.home() / ".cache" / "research-repo-tools"


def executable(directory: Path, name: str) -> Path:
    return directory / (name + ".exe" if os.name == "nt" else name)
