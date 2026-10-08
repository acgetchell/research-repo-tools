"""Read-only Tectonic native dependency environment discovery."""

import os
import platform
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from research_repo_tools.process import run_command

__all__ = ["discover_environment"]


def discover_environment(
    *, environment: Mapping[str, str] | None = None, system: str | None = None, pkg_config: str = "pkg-config", prefixes: Sequence[Path] = ()
) -> dict[str, str]:
    """Return validated assignments without changing process state or installing.

    Linux/macOS probe pkg-config, retaining sysroot/search settings. macOS also
    discovers existing Homebrew metadata and SDK shims. Windows requires an
    explicitly provisioned vcpkg triplet. This checks discovery, not compilation
    or ABI compatibility. Consumers own libraries, SDKs, versions and permissions.
    """
    env = dict(os.environ if environment is None else environment)
    system = system or platform.system()
    if system not in {"Linux", "Darwin", "Windows"}:
        raise ValueError(f"unsupported Tectonic native platform: {system}")
    if system == "Windows":
        triplet = env.get("VCPKGRS_TRIPLET", "x64-windows-static-md")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", triplet) is None:
            raise ValueError("VCPKGRS_TRIPLET must be a single portable triplet name")
        root = env.get("VCPKG_ROOT", "")
        if env.get("TECTONIC_DEP_BACKEND") != "vcpkg" or not root or not (Path(root) / "installed" / triplet).is_dir():
            raise ValueError("configure TECTONIC_DEP_BACKEND=vcpkg and a provisioned VCPKG_ROOT/installed/VCPKGRS_TRIPLET before building Tectonic")
        result = {"TECTONIC_DEP_BACKEND": "vcpkg", "VCPKG_ROOT": str(Path(root).resolve()), "VCPKGRS_TRIPLET": triplet}
        if env.get("VCPKGRS_DYNAMIC"):
            result["VCPKGRS_DYNAMIC"] = env["VCPKGRS_DYNAMIC"]
    else:
        if env.get("TECTONIC_DEP_BACKEND", "pkg-config") != "pkg-config":
            raise ValueError("Linux/macOS native discovery requires TECTONIC_DEP_BACKEND=pkg-config")
        directories = [item for item in env.get("PKG_CONFIG_PATH", "").split(os.pathsep) if item]
        candidates = [path / suffix for path in prefixes for suffix in ("lib/pkgconfig", "share/pkgconfig")]
        if system == "Darwin":
            for prefix in (Path("/opt/homebrew"), Path("/usr/local")):
                candidates.extend(prefix / suffix for suffix in ("lib/pkgconfig", "share/pkgconfig"))
                for package in ("fontconfig", "freetype", "graphite2", "icu4c*", "libpng"):
                    candidates.extend(path / "lib/pkgconfig" for path in (prefix / "opt").glob(package))
            # These probes only read installation/SDK locations.
            import shutil

            if shutil.which("brew", path=env.get("PATH")) and shutil.which("xcrun", path=env.get("PATH")):
                repository = run_command("brew", ["--repository"], env=env, timeout=30).stdout.strip()
                sdk = run_command("xcrun", ["--sdk", "macosx", "--show-sdk-version"], env=env, timeout=30).stdout.strip()
                if not repository or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", sdk) is None:
                    raise ValueError("malformed Homebrew repository or macOS SDK discovery output")
                candidates.append(Path(repository) / "Library/Homebrew/os/mac/pkgconfig" / sdk.split(".")[0])
        for candidate in sorted(candidates):
            if candidate.is_dir() and str(candidate) not in directories:
                directories.append(str(candidate))
        if directories:
            env["PKG_CONFIG_PATH"] = os.pathsep.join(directories)
        env["TECTONIC_DEP_BACKEND"] = "pkg-config"
        packages = ["freetype2", "graphite2", "icu-uc", "libpng", "zlib"]
        if system == "Linux":
            packages += ["fontconfig", "openssl"]
        missing = []
        for package in packages:
            result = run_command(pkg_config, ["--exists", package], env=env, timeout=30, check=False)
            if result.returncode:
                missing.append(package)
        if missing:
            raise ValueError(f"pkg-config could not resolve: {', '.join(missing)}; provision native development files or declare PKG_CONFIG_PATH")
        result = {name: env[name] for name in ("TECTONIC_DEP_BACKEND", "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR", "PKG_CONFIG_SYSROOT_DIR") if env.get(name)}
    if any(any(character in value for character in "\0\r\n") for value in result.values()):
        raise ValueError("native environment values must not contain NUL/CR/LF")
    return result
