"""Fresh release repositories and byte snapshots shared by release tests."""

from pathlib import Path

_VERSION = "1.2.3"
_DOI = "10.5281/zenodo.12345"
_RELEASE_DATE = "2026-08-04"
_CARGO_TOML = f'[package]\nname = "consumer"\nversion = "{_VERSION}"\nrepository = "https://github.com/example/consumer"\n'


def write_project(
    root: Path, *, metadata_version: str = _VERSION, readme: str | None = None, citation_doi: str = _DOI, citation_date: str = _RELEASE_DATE
) -> None:
    """Write a minimal repository for release-check tests."""
    readme_text = (
        readme
        if readme is not None
        else f'[![DOI](https://badgen.net/badge/DOI/10.5281%2Fzenodo.12345/blue)](https://doi.org/{_DOI})\nconsumer = "{_VERSION}"\ncargo add consumer@{_VERSION}\n[tagged](https://github.com/example/consumer/blob/v{_VERSION}/README.md)\n'
    )
    files = {
        "Cargo.toml": _CARGO_TOML,
        "Cargo.lock": f'version = 4\n\n[[package]]\nname = "consumer"\nversion = "{metadata_version}"\n',
        "pyproject.toml": f'[project]\nname = "consumer-tooling"\nversion = "{metadata_version}"\n',
        "uv.lock": f'version = 1\n\n[[package]]\nname = "consumer-tooling"\nversion = "{metadata_version}"\nsource = {{ editable = "." }}\n',
        "CITATION.cff": f"cff-version: 1.2.0\nversion: {metadata_version}\ndoi: {citation_doi}\ndate-released: {citation_date}\n",
        "CHANGELOG.md": f"# Changelog\n\n## [{_VERSION}] - {_RELEASE_DATE}\n\n- Release\n\n[{_VERSION}]: https://github.com/example/consumer/compare/v1.2.2...v{_VERSION}\n",
        "README.md": readme_text,
        "REFERENCES.md": f"- DOI: <https://doi.org/{_DOI}>\n",
    }
    for filename, content in files.items():
        (root / filename).write_text(content, encoding="utf-8", newline="\n")


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
