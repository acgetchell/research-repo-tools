"""Optional notebook structure, environment, and fresh-kernel execution contract."""

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from research_repo_tools import files, toolchain, toolchain_config
from research_repo_tools.config import Config
from research_repo_tools.process import run_safe_command

if TYPE_CHECKING:
    from nbformat import NotebookNode

KERNEL = "research-repo-tools"
CELL_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def dependency(name: str):
    try:
        return import_module(name)
    except ImportError as error:
        raise RuntimeError(
            f"Notebook dependency {name!r} is unavailable; declare research-repo-tools[notebooks] in the notebook dependency group and run notebooks sync."
        ) from error


@dataclass(frozen=True)
class Notebook:
    path: Path
    original: bytes
    node: NotebookNode


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_value: str):
    raise ValueError("non-finite JSON number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        _constant(value)
    return result


def _check_unicode(raw: object) -> None:
    pending = [raw]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if re.search(r"[\ud800-\udfff]", value):
                raise ValueError("unpaired Unicode surrogate in notebook JSON")
        elif isinstance(value, dict):
            pending.extend(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def read_raw(path: Path) -> tuple[bytes, dict]:
    """Read unambiguous JSON without conversion, normalization, or ID generation."""
    original = path.read_bytes()
    try:
        raw = json.loads(original.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant, parse_float=_finite_float)
        _check_unicode(raw)
        if not isinstance(raw, dict) or type(raw.get("nbformat")) is not int or raw["nbformat"] != 4:
            raise ValueError("expected nbformat 4")
        if not isinstance(raw.get("cells"), list):
            raise ValueError("cells must be an array")
        return original, raw
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{path}: {error}") from error


def load(path: Path) -> Notebook:
    """Validate without nbformat's implicit ID repair or version conversion."""
    nbformat = dependency("nbformat")
    original, raw = read_raw(path)
    try:
        if type(raw.get("nbformat_minor")) is not int or raw["nbformat_minor"] != 5:
            raise ValueError("expected nbformat 4.5 with stable cell IDs")
        cells = raw.get("cells")
        if not isinstance(cells, list):
            raise ValueError("cells must be an array")
        seen: set[str] = set()
        for index, cell in enumerate(cells, 1):
            if not isinstance(cell, dict) or not isinstance(cell_id := cell.get("id"), str) or CELL_ID.fullmatch(cell_id) is None:
                raise ValueError(f"cell {index}: expected an existing 1-64 character ASCII cell ID")
            if cell_id in seen:
                raise ValueError(f"cell {index}: duplicate cell ID {cell_id!r}")
            seen.add(cell_id)
        # The schema validator, unlike reads()/validate(), never normalizes IDs.
        validator = nbformat.validator.get_validator(version=4, version_minor=5)
        if validator is None:
            raise RuntimeError("nbformat 4.5 validator is unavailable")
        try:
            validator.validate(raw)
        except nbformat.ValidationError as error:
            raise ValueError(f"invalid notebook structure: {error.message}") from error
        # Stored notebooks may encode multiline sources/output text as lists.
        # Decode those representations only after strict validation has ruled
        # out ID repair, schema conversion, and malformed source state.
        return Notebook(path.resolve(), original, nbformat.reads(original.decode("utf-8"), as_version=4))
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{path}: {error}") from error


def selected_paths(paths: list[Path]) -> list[Path]:
    if not paths:
        raise ValueError("select at least one notebook explicitly")
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(paths):
        raise ValueError("selected notebook paths must be distinct")
    if any(path.suffix != ".ipynb" or not path.is_file() for path in resolved):
        raise ValueError("selected notebooks must be existing .ipynb files")
    return resolved


def selected(paths: list[Path]) -> list[Notebook]:
    return [load(path) for path in selected_paths(paths)]


def generated_state(node: NotebookNode) -> bool:
    return "widgets" in node.metadata or any(
        cell.cell_type == "code" and (cell.outputs or cell.execution_count is not None or "execution" in cell.metadata) for cell in node.cells
    )


def clear_node(node: NotebookNode) -> None:
    node.metadata.pop("widgets", None)
    for cell in node.cells:
        if cell.cell_type == "code":
            cell.outputs = []
            cell.execution_count = None
            cell.metadata.pop("execution", None)


def serialize(node: NotebookNode) -> bytes:
    return (json.dumps(node, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def check(paths: list[Path], *, outputs: Literal["clear", "preserve"] = "clear") -> None:
    notebooks = selected(paths)
    for notebook in notebooks:
        if outputs == "clear" and generated_state(notebook.node):
            raise ValueError(f"{notebook.path}: generated outputs, counts, timing, or widget state must be cleared")
        print(f"OK checked {notebook.path}")


def clear(paths: list[Path]) -> None:
    notebooks = selected(paths)
    updates = {}
    for notebook in notebooks:
        if generated_state(notebook.node):
            clear_node(notebook.node)
            updates[notebook.path] = serialize(notebook.node)
    files.replace_many(updates)
    for notebook in notebooks:
        print(f"OK {'cleared' if notebook.path in updates else 'already clean'} {notebook.path}")


def project_environment(root: Path) -> Path:
    path = Path(os.environ.get("UV_PROJECT_ENVIRONMENT") or ".venv")
    return (root / path).resolve()


def runtime(settings: Config) -> toolchain.Runtime:
    result = toolchain.Runtime(toolchain_config.load(settings))
    uv = result.uv_status()
    if not uv.ok:
        raise ValueError(f"uv {result.plan.uv} must be installed for notebooks; found {uv.actual}")
    document = tomllib.loads((settings.root / "pyproject.toml").read_text(encoding="utf-8"))
    groups = document.get("dependency-groups", {})
    if not isinstance(groups, dict) or settings.notebooks.group not in groups:
        raise ValueError(f"declare the notebook dependency group {settings.notebooks.group!r} in pyproject.toml")
    if not (settings.root / "uv.lock").is_file():
        raise ValueError("notebook commands require the consumer's uv.lock")
    return result


def sync(settings: Config) -> None:
    managed = runtime(settings)
    environment = project_environment(settings.root)
    if managed.plan.rust is not None and not all(status.ok for status in managed.rust_statuses()):
        raise ValueError("managed Rust is incomplete; run setup before synchronizing notebook dependencies")
    env = managed.project_environment()
    uv = managed.uv_status().path
    run_safe_command(
        uv,
        ["sync", "--locked", "--managed-python", "--group", "dev", "--group", settings.notebooks.group],
        cwd=settings.root,
        env=env,
        timeout=3600,
        capture_output=False,
    )
    python = toolchain_config.executable(environment / ("Scripts" if os.name == "nt" else "bin"), "python")
    run_safe_command(
        str(python),
        ["-m", "ipykernel", "install", "--prefix", str(environment), "--name", KERNEL, "--display-name", "Research project (locked Python)"],
        cwd=settings.root,
        env=env,
        timeout=60,
        capture_output=False,
    )


def _execute(node: NotebookNode, *, cwd: Path, timeout: int, env: dict[str, str], progress: list[int]) -> None:
    nbclient = dependency("nbclient")
    kernelspec = dependency("jupyter_client.kernelspec")
    manager = dependency("jupyter_client.manager")
    dependency("ipykernel")

    class ProjectKernel(kernelspec.KernelSpecManager):
        def get_kernel_spec(self, kernel_name):
            return kernelspec.KernelSpec(
                argv=[sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                display_name="Research project (locked Python)",
                language="python",
            )

    def starting(cell, cell_index, **kwargs):
        progress[:] = [cell_index]

    # Supply a kernel manager with an explicit interpreter, bypassing ambient
    # kernelspec lookup and notebook metadata. Never register a global kernel.
    km = manager.AsyncKernelManager(
        kernel_name=KERNEL,
        kernel_spec_manager=ProjectKernel(),
        connection_file=str(Path(env["JUPYTER_RUNTIME_DIR"]) / "kernel.json"),
    )
    skip_tag = "research-repo-tools-never-skip"
    while any(skip_tag in cell.metadata.get("tags", []) for cell in node.cells):
        skip_tag += "-"
    client = nbclient.NotebookClient(
        node,
        km=km,
        timeout=timeout,
        startup_timeout=60,
        allow_errors=False,
        force_raise_errors=True,
        skip_cells_with_tag=skip_tag,
        record_timing=False,
        store_widget_state=False,
        on_cell_start=starting,
    )
    # A caller-supplied manager requires explicit cleanup ownership.
    client.execute(cwd=str(cwd), env=env, cleanup_kc=True)


def execute(settings: Config, paths: list[Path], *, cwd: str | None = None, output_dir: str | None = None, timeout: int | None = None) -> int:
    options = settings.notebooks
    limit = options.timeout if timeout is None else timeout
    if type(limit) is not int or limit <= 0:
        raise ValueError("notebook timeout must be a positive integer")
    working = settings.path(cwd or options.cwd).resolve()
    if not working.is_dir():
        raise ValueError(f"notebook working directory does not exist: {working}")
    destination = settings.path(output_dir or options.output_dir).resolve()
    notebooks = selected(paths)
    root = settings.root.resolve()
    for notebook in notebooks:
        if not notebook.path.is_relative_to(root):
            raise ValueError(f"notebook must be inside the consumer root: {notebook.path}")
        if destination == notebook.path.parent or destination.is_relative_to(notebook.path.parent):
            raise ValueError(f"notebook output directory must be outside source directory {notebook.path.parent}")
    targets = [destination / notebook.path.relative_to(root) for notebook in notebooks]
    reports = [target.with_suffix(".report.json") for target in targets]
    # Resolve existing symlink parents before execution; artifacts cannot alias
    # any source, even through another selected notebook's output path.
    source_paths = {notebook.path for notebook in notebooks}
    for target in [*targets, *reports]:
        if not target.resolve().is_relative_to(destination) or target.resolve() in source_paths or target.is_symlink():
            raise ValueError(f"unsafe notebook artifact path: {target}")
        if target.exists() and not target.is_file():
            raise ValueError(f"notebook artifact is not a regular file: {target}")
    managed = runtime(settings)
    if Path(sys.prefix).resolve() != project_environment(root):
        raise ValueError("execute notebooks through the consumer's locked project environment; run notebook-sync first")
    if not managed._python_probe(sys.executable).ok:
        raise ValueError("the running notebook interpreter does not satisfy the declared Python version")
    for name in ("nbclient", "ipykernel"):
        dependency(name)
    packages = {name: version(name) for name in ("research-repo-tools", "nbclient", "nbformat", "ipykernel")}
    lock_digest = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    for notebook, target, report in zip(notebooks, targets, reports, strict=True):
        clear_node(notebook.node)
        progress: list[int] = []
        failure: Exception | None = None
        with tempfile.TemporaryDirectory(prefix="research-notebook-") as temporary:
            env = managed.environment()
            for key in ("IPYTHONDIR", "MPLCONFIGDIR", "JUPYTER_CONFIG_DIR", "JUPYTER_DATA_DIR", "JUPYTER_RUNTIME_DIR"):
                directory = Path(temporary) / key.lower()
                directory.mkdir()
                env[key] = str(directory)
            env["MPLBACKEND"] = "Agg"
            try:
                _execute(notebook.node, cwd=working, timeout=limit, env=env, progress=progress)
            except Exception as error:
                # Third-party execution/startup failures all produce a failed
                # report and nonzero status. Interrupts are deliberately excluded.
                failure = error
        cell_index = progress[0] if progress else None
        result = {
            "schema": 1,
            "source": notebook.path.relative_to(root).as_posix(),
            "source_sha256": hashlib.sha256(notebook.original).hexdigest(),
            "lock_sha256": lock_digest,
            "python": {"executable": sys.executable, "version": sys.version.split()[0]},
            "packages": packages,
            "cwd": str(working),
            "timeout": limit,
            "status": "failed" if failure else "passed",
            "failed_cell": None if failure is None or cell_index is None else {"index": cell_index + 1, "id": notebook.node.cells[cell_index].id},
            "error": None if failure is None else {"type": type(failure).__name__, "message": str(failure)},
        }
        files.replace_many({target: serialize(notebook.node), report: (json.dumps(result, indent=2) + "\n").encode("utf-8")})
        if failure is not None:
            print(f"{notebook.path}: cell {result['failed_cell']}: {type(failure).__name__}: {failure}\nExecution report: {report}", file=sys.stderr)
            return 1
        print(f"OK executed {notebook.path} -> {target}; report {report}")
    return 0
