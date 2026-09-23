"""Compact, non-normalizing inspection of nbformat 4 notebooks awaiting repair."""

import json
from pathlib import Path

from research_repo_tools.notebooks import CELL_ID, read_raw, selected_paths


def _source(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return "".join(value)
    return None


def _summary(path: Path, *, preview: bool) -> dict:
    _, raw = read_raw(path)
    problems = []
    minor = raw.get("nbformat_minor")
    if type(minor) is not int or minor != 5:
        problems.append("nbformat_minor must be 5 for strict commands")
    if not isinstance(raw.get("metadata"), dict):
        problems.append("metadata must be an object")
    cells = []
    seen: set[str] = set()
    for index, value in enumerate(raw["cells"], 1):
        issues = []
        cell = value if isinstance(value, dict) else {}
        if not isinstance(value, dict):
            issues.append("cell must be an object")
        cell_type = cell.get("cell_type")
        if cell_type not in ("code", "markdown", "raw"):
            issues.append("cell_type must be code, markdown, or raw")
        cell_id = cell.get("id")
        if "id" not in cell:
            identity = "missing"
            issues.append("id is missing; assign a stable ID")
        elif not isinstance(cell_id, str) or CELL_ID.fullmatch(cell_id) is None:
            identity = "invalid"
            issues.append("id must be 1-64 ASCII letters, digits, underscores, or hyphens")
        elif cell_id in seen:
            identity = "duplicate"
            issues.append("id duplicates an earlier cell; assign a unique ID")
        else:
            identity = "existing"
        if isinstance(cell_id, str):
            seen.add(cell_id)
        source = _source(cell.get("source"))
        if source is None:
            issues.append("source must be a string or an array of strings")
        if not isinstance(cell.get("metadata"), dict):
            issues.append("metadata must be an object")
        outputs = cell.get("outputs")
        count = cell.get("execution_count")
        if cell_type == "code":
            if not isinstance(outputs, list):
                issues.append("outputs must be an array")
            if "execution_count" not in cell or not (count is None or type(count) is int and count >= 0):
                issues.append("execution_count must be null or a nonnegative integer")
        entry = {
            "number": index,
            "cell_type": cell_type if isinstance(cell_type, str) else None,
            "id": cell_id if isinstance(cell_id, str) else None,
            "id_status": identity,
            "source_lines": len(source.splitlines()) if source is not None else None,
            "output_count": len(outputs) if cell_type == "code" and isinstance(outputs, list) else None,
            "execution_count": count if cell_type == "code" and type(count) is int and count >= 0 else None,
            "problems": issues,
        }
        if preview:
            entry["preview"] = " ".join(source.split())[:80] if source is not None else None
        cells.append(entry)
    return {"path": str(path), "nbformat": 4, "nbformat_minor": minor if type(minor) is int else None, "problems": problems, "cells": cells}


def inspect(paths: list[Path], *, preview: bool = True, as_json: bool = False) -> None:
    # Build the whole report before publishing so a bad later input cannot leave
    # a truncated JSON document or a misleading partial inventory on stdout.
    report = {"schema": 1, "notebooks": [_summary(path, preview=preview) for path in selected_paths(paths)]}
    if as_json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2))
        return
    for notebook in report["notebooks"]:
        print(f"{notebook['path']}: nbformat 4.{notebook['nbformat_minor']}; {len(notebook['cells'])} cells")
        for problem in notebook["problems"]:
            print(f"  problem: {problem}")
        for cell in notebook["cells"]:
            identity = repr(cell["id"][:64]) if cell["id"] is not None else "-"
            cell_type = repr(cell["cell_type"][:16]) if cell["cell_type"] is not None else "-"
            print(
                f"  cell {cell['number']} {cell_type} id={identity} ({cell['id_status']}) "
                f"lines={cell['source_lines']} outputs={cell['output_count']} execution={cell['execution_count']}"
            )
            if preview and cell["preview"]:
                print(f"    {ascii(cell['preview'])}")
            for problem in cell["problems"]:
                print(f"    problem: {problem}")
