"""Check the current wheel and sdist for PyPI without building or uploading."""

import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

from twine.exceptions import TwineException
from twine.package import PackageFile

from research_repo_tools.process import ExecutableNotFoundError, format_exception_diagnostics, run_safe_command
from research_repo_tools.release_validation import distribution_names

ROOT = Path(__file__).resolve().parents[1]


def check(root: Path) -> int:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dist = root / "dist"
    if not dist.is_dir():
        raise ValueError("dist/ is missing; run just build before just publish-check")
    version = metadata["project"]["version"]
    distributions = distribution_names(f"v{version}")
    # uv adds .gitignore to dist/. Only distribution files are publication inputs.
    artifacts = set(dist.glob("*.whl")) | set(dist.glob("*.tar.gz"))
    if {path.name for path in artifacts} != set(distributions):
        raise ValueError(f"expected only {', '.join(distributions)}; run just build to refresh dist/")
    for name in distributions:
        path = dist / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"distribution is not a regular file: {path.name}")
        package = PackageFile.from_filename(str(path), comment=None)
        if package.metadata["name"] != metadata["project"]["name"] or package.version != version:
            raise ValueError(f"distribution package name or version differs from pyproject.toml: {path.name}")
    return run_safe_command(
        sys.executable,
        ["-X", "utf8", "-m", "twine", "check", "--strict", *(str(dist / name) for name in distributions)],
        cwd=root,
        timeout=120,
        check=False,
        capture_output=False,
    ).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(check(ROOT))
    except (ExecutableNotFoundError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, TwineException, subprocess.SubprocessError) as error:
        print(f"Publication check failed: {format_exception_diagnostics(error)}", file=sys.stderr)
        raise SystemExit(1) from error
