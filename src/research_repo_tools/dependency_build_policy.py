"""Explicit native source builds and external payloads owned by a checkout."""

import platform
import re
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

from packaging.utils import canonicalize_name

from research_repo_tools._archive_paths import _member_path
from research_repo_tools.dependency_sources import string, table


def native_platform() -> str:
    machine = platform.machine().lower()
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(machine, machine)
    return f"{sys.platform}-{machine}"


def member_name(value: object) -> str:
    value = string(value, "archive member")
    path = _member_path(value, directory=False)
    if path.as_posix() != value:
        raise ValueError("build artifact members must be canonical relative paths")
    return value


@dataclass(frozen=True, slots=True)
class ExternalBuildInput:
    package: str
    platform: str
    url: str
    sha256: str
    member: str
    wheel_member: str

    def __post_init__(self) -> None:
        if canonicalize_name(self.package, validate=True) != self.package:
            raise ValueError("build package names must be normalized")
        if self.platform not in {"darwin-arm64", "darwin-x86_64", "linux-arm64", "linux-x86_64", "win32-arm64", "win32-x86_64"}:
            raise ValueError("unsupported external build input platform")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or not re.fullmatch(r"/[^/]+/[^/]+/releases/download/[^/]+/[^/]+", parsed.path)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("external build inputs require an exact GitHub release-asset URL")
        if re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("external build input requires a SHA-256")
        member_name(self.member)
        member_name(self.wheel_member)


@dataclass(frozen=True, slots=True)
class SourceBuildPolicy:
    packages: tuple[str, ...]
    group: str
    external: tuple[ExternalBuildInput, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "packages", tuple(self.packages))
        object.__setattr__(self, "external", tuple(self.external))
        if not self.packages or len(set(self.packages)) != len(self.packages) or any(canonicalize_name(name, validate=True) != name for name in self.packages):
            raise ValueError("source builds require distinct normalized package names")
        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", self.group) is None:
            raise ValueError("source builds require a dependency group")
        identities = [(item.package, item.platform, item.wheel_member) for item in self.external]
        if len(set(identities)) != len(identities) or any(item.package not in self.packages for item in self.external):
            raise ValueError("external inputs must uniquely bind a configured build's wheel members")


def parse_build_policy(raw: object) -> SourceBuildPolicy | None:
    if raw is None:
        return None
    data = table(raw, "source-builds")
    if set(data) - {"packages", "group", "external"}:
        raise ValueError("unknown source-builds field")
    packages = data.get("packages")
    external = data.get("external", [])
    if not isinstance(packages, list) or any(not isinstance(name, str) for name in packages) or not isinstance(external, list):
        raise ValueError("source-build packages and external inputs must be arrays")
    inputs = []
    for raw_input in external:
        item = table(raw_input, "external build input")
        if set(item) != {"package", "platform", "url", "sha256", "member", "wheel-member"}:
            raise ValueError("external build input fields are incomplete or unknown")
        inputs.append(ExternalBuildInput(*(string(item[key], key) for key in ("package", "platform", "url", "sha256", "member", "wheel-member"))))
    return SourceBuildPolicy(tuple(packages), string(data.get("group"), "build group"), tuple(inputs))
