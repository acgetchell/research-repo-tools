"""Strict native Semgrep scans over the shared portable file inventory."""

import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from research_repo_tools import config, semgrep
from research_repo_tools.process import resolve_executable
from research_repo_tools.security import _clear_numbered_reports, _report_run, _sarif, security_inventory
from research_repo_tools.semgrep_docs import rust_blocks
from research_repo_tools.semgrep_findings import parse_results

__all__ = ["check_documentation_fixtures", "scan"]


def check_documentation_fixtures(settings: config.Config) -> int:
    """Adapt Markdown fences, delegating assertions to the shared fixture checker."""
    if settings.semgrep.fixtures is None:
        raise ValueError("semgrep.fixtures must be explicit")
    source = settings.path(settings.semgrep.fixtures)
    if not source.is_dir():
        raise ValueError("documentation fixtures require a directory")
    if settings.semgrep.counts:
        raise ValueError("documentation fixtures use source annotations; keep count-based fixtures in a separate fixture gate")
    with tempfile.TemporaryDirectory(prefix="research-semgrep-fixtures-") as directory:
        root = Path(directory)
        for path in semgrep.fixtures(source):
            if path.is_symlink():
                raise ValueError("fixture symlinks are unsupported")
            destination = root / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".md":
                blocks = rust_blocks(path)
                if not blocks:
                    raise ValueError(f"Markdown fixture contains no Rust fences: {path}")
                for index, block in enumerate(blocks):
                    destination.with_name(f"{path.name}.block-{index}.rs").write_text(block, encoding="utf-8", newline="\n")
            else:
                shutil.copyfile(path, destination)
        return semgrep.check(replace(settings, semgrep=replace(settings.semgrep, fixtures=str(root))))


def _locations(value, mapping):
    if isinstance(value, dict):
        return {key: _locations(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_locations(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def scan(
    settings: config.Config, *, include: tuple[str, ...], exclude: tuple[str, ...] = (), output: str = "target/security/semgrep", rust_docs: bool = False
) -> int:
    """Keep native rule IDs, findings and reports; missing coverage blocks.

    One target per native invocation bounds Windows argument lengths. Reports
    are numbered deterministically; consumer rules and exclusions stay local.
    """
    if not include or settings.semgrep.config is None:
        raise ValueError("Semgrep scan requires explicit include patterns and semgrep.config")
    names = security_inventory(settings.root, include=include, exclude=exclude)
    if not names:
        raise ValueError("no Semgrep inputs selected")
    binary = resolve_executable("semgrep", cwd=settings.root)
    code = 0
    with tempfile.TemporaryDirectory(prefix="research-semgrep-scan-") as directory:
        temporary = Path(directory)
        targets = []
        mapping = {}
        for name in names:
            path = settings.root / name
            if path.suffix != ".md" or not rust_docs:
                targets.append(path)
            if rust_docs and path.suffix in {".md", ".rs"}:
                for index, block in enumerate(rust_blocks(path)):
                    snippet = temporary / "inputs" / name / f"block-{index}.rs"
                    snippet.parent.mkdir(parents=True, exist_ok=True)
                    snippet.write_text(block, encoding="utf-8", newline="\n")
                    targets.append(snippet)
                    mapping[str(snippet)] = str(path)
                    mapping[quote(str(snippet), safe="/")] = quote(str(path), safe="/")
        if not targets:
            raise ValueError("selected documentation contains no Rust snippets")
        env = {
            **os.environ,
            "SEMGREP_SEND_METRICS": "off",
            "OTEL_SDK_DISABLED": "true",
            "SEMGREP_SETTINGS_FILE": str(temporary / "settings.yml"),
            "SEMGREP_VERSION_CACHE_PATH": str(temporary / "version-cache"),
            "SEMGREP_LOG_FILE": str(temporary / "semgrep.log"),
        }
        _clear_numbered_reports(settings.path(output))
        for index, target in enumerate(targets):
            for fmt in ("json", "sarif"):
                args = [
                    "scan",
                    "--config",
                    str(settings.path(settings.semgrep.config)),
                    "--metrics",
                    "off",
                    "--disable-version-check",
                    "--strict",
                    "--disable-nosem",
                    "--no-rewrite-rule-ids",
                    "--no-git-ignore",
                    "--max-target-bytes",
                    "0",
                    "--timeout",
                    "30",
                    "--error",
                    "--" + fmt,
                    "--output",
                    "{report}",
                    str(target),
                ]

                def validate(value, format_name):
                    if format_name == "json":
                        parsed = parse_results(json.dumps(value))
                        if (
                            not isinstance(value.get("errors"), list)
                            or not isinstance(paths := value.get("paths"), dict)
                            or not isinstance(scanned := paths.get("scanned"), list)
                        ):
                            raise ValueError("Semgrep report is missing errors or scanned-path inventory")
                        if target.resolve() not in {(settings.root / path).resolve() for path in scanned if isinstance(path, str)}:
                            raise ValueError("Semgrep skipped a required input")
                        found = bool(parsed.results)
                    else:
                        found = bool(_sarif(value))
                    mapped = _locations(value, mapping)
                    value.clear()
                    value.update(mapped)
                    return found

                status = _report_run(binary, args, settings.path(output) / f"{index}.{fmt}", fmt, root=settings.root, env=env, validate=validate)
                code = code or status
    return code
