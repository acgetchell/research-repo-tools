# Notebook Python APIs

Part of the [supported interface contract](api.md). Use the [task guides](../README.md#capability-index)
for setup and ordinary workflows.

- [Python notebook policy and workflow API](#python-notebook-policy-and-workflow-api)
- [Python notebook integration-test API](#python-notebook-integration-test-api)

## Python notebook policy and workflow API

`research_repo_tools.config.load` and `parse` accept
`notebooks.id-pattern`, `notebooks.lab`, and `notebooks.reset` as described in
[the notebook contract](RUNNING_NOTEBOOKS.md). Use configuration parsing to
construct settings for these public interfaces:

- `research_repo_tools.notebook_lint.lint(settings, paths, *, timeout=30,
  id_pattern=None) -> int`: optional full-match pattern override; `None` uses
  configuration. Paths are explicit `Path` objects; the notebook extra and
  consumer's locked Ruff/ty environment are required. It returns zero for a
  clean selection and one for findings. Configuration/structure failures raise
  `ValueError`; missing notebook dependencies raise `RuntimeError`.
- `research_repo_tools.notebook_workflows.launch(settings, *, notebook=None,
  browser=None, scratch_dir=None) -> int`: `None` selects configuration for browser
  and scratch; omitted notebook retains the Lab landing page. The optional
  v0.2.4 `notebook` argument is a literal `str` or `Path`, relative to the consumer
  root or absolute within it, naming an existing `.ipynb` inside `notebooks.cwd`.
  Traversal, symlinks/junctions, hard-link aliases, outside paths, and option-shaped arguments raise
  `ValueError` before synchronization. Selection is encoded as a JupyterLab tree
  URL; there is no default-notebook configuration or arbitrary argv forwarding.
  It requires uv and
  the consumer's declared, locked notebook environment with JupyterLab and
  project kernel. Exit status propagates; interruption returns 130. Caches
  are private beneath scratch and the caller's environment remains unchanged.
  A `UV_PROJECT_ENVIRONMENT` override must remain strictly beneath the consumer
  root without symlink or junction components.
- `research_repo_tools.notebook_workflows.reset(settings, paths=(), *,
  revision=None, apply=False) -> NotebookResetPlan`: `paths` is a sequence of
  literal `Path` files/directories, relative to the consumer root or absolute
  within it. Empty paths use configured sources. `revision=None` selects the
  index; a string explicitly selects a verified Git tree. Preview performs
  only reads. `apply=True` explicitly opts into working-file restoration and
  declared cleanup. The base package suffices; notebook dependencies are not
  loaded. Git/OS discovery errors propagate during preflight; unsafe maps raise
  `ValueError`. Restore/cleanup failures raise `RuntimeError` with phase and
  partial-progress diagnostics. Interrupts propagate.

`NotebookResetPlan` is frozen and exposes `sources` and `cleanup` as tuples of
absolute `Path` objects, and `revision` as a pinned tree ID or `None` for the
index. It describes the requested operation, not a mutable execution handle or
proof of successful application. The CLI and these functions share validation,
preview output, and failure behavior. See the [task guide](RUNNING_NOTEBOOKS.md)
for consumer commands and the exact deletion map.

## Python notebook integration-test API

`research_repo_tools.notebook_testing` exports
`isolated_project`, `NotebookProject`, and `NotebookExecution`. Importing the
module needs only the base package; execution needs the notebook extra. There
is no pytest dependency. Use the factory/context manager; result constructors
and private attributes are not extension points.

`isolated_project(source_root, paths, *, parent, environment)` yields a
`NotebookProject` in a new temporary directory under the existing `parent`.
Arguments are `Path` objects; `paths` is an explicit sequence of files relative
to `source_root`, or absolute files inside it. Directories, traversal, links
(including Windows junctions), `.git`, `.venv`, and `rust-toolchain.toml` are
rejected. The manifest, `.python-version`, and `uv.lock` are required and copied
automatically, byte-for-byte. Other files are copied only when listed. `parent`
must be outside the source project and borrowed environment. Copy/parse failures
clean up the new workspace and preserve the originals.

`environment` explicitly selects the already synchronized Python environment
running the tests: normally `Path(sys.prefix)`. A different environment is an
error; launch the tests with its interpreter instead. Execution verifies the
declared uv and Python versions and notebook group, and hashes the copied lock.
It does not re-resolve the lock or verify every installed package against it.
No environment is created, synchronized, upgraded or deleted, and no kernel is
registered. Notebook options and Python inheritance are retained; managed Cargo
and binary declarations are disabled in memory, so copying the consumer manifest
does not require a Rust toolchain or TOML text surgery.

`NotebookProject` exposes `root` for consumer input preparation, `artifacts` for
execution outputs, and `environment`. Its method
`execute(notebook, *, cwd=None, timeout=None, env=None) -> NotebookExecution`
uses the existing notebook engine and a fresh kernel for each call:

- `notebook` and `cwd` are `Path` objects relative to `root`, or absolute paths
  inside it. Links and escapes are rejected. Omitted `cwd` and `timeout` use
  notebook configuration; timeout must be a positive integer in seconds.
- `env` overlays kernel variables; `None` values remove them. The calling process
  environment is never changed. The selected interpreter/environment and private
  Jupyter/IPython/Matplotlib directories remain authoritative; `PYTHONHOME` and
  `PYTHONPATH` are removed and bytecode writes disabled for the kernel.
- Executed notebooks, schema-1 reports, and temporary kernel state stay in the
  fixture workspace. Configured `output-dir` is replaced by `artifacts`.
  Repeated execution of the same notebook replaces its previous artifacts.
- `NotebookExecution` exposes `source` (the copied input), `notebook_path`,
  `report_path`, `report` (the existing JSON report as a dictionary), and
  `returncode` (`0` passed, `1` failed). Cell errors and timeouts return reports;
  malformed inputs, environment failures, and publication errors raise, using
  the existing notebook/file exception contracts. Interrupts propagate.
- Context exit removes only the newly created workspace, including on an
  exception. Artifact paths then expire and further execution raises `ValueError`.

Starting with v0.2.4, execution owns a selector event loop in a temporary worker,
including its shutdown. This supports PyZMQ socket readers on Windows without
deprecated asyncio policy changes. The local Jupyter provisioner starts kernels
through `subprocess.Popen`; it does not require selector-loop asyncio subprocess
support. Synchronous calls from a running caller loop are supported and preserve
that loop and its subprocess capabilities. Kernel cleanup completes on success,
cell errors, and timeouts; caller interruptions cancel execution and wait for
cleanup before propagating. No warnings are suppressed.

Notebook code is trusted executable code, not sandboxed. Consumer tests own any
files that code writes, its domain environment variables, scientific assertions,
and external side effects. Place intended test outputs beneath `project.root`.

The [worked example](#consumer-integration-tests) replaces generic
notebook/project copying, `UV_PROJECT_ENVIRONMENT` mutation, Cargo-table string
slicing, and sidecar-path reconstruction. MCMC adoption should retain its
trace-root, output-destination, ACF, ESS, timing, and split-R-hat assertions;
modify only fixture copies when injecting scientific assertion cells. Consumer
adoption follows publication and does not follow automatically from upstream tests.

## Consumer integration tests

The notebook fixture and Just inspection APIs require no pytest plugin. Keep
scientific assertions and repository-specific gate policy in consumer tests.
Validate the installed replacement before deleting consumer helpers.

Run notebook tests in the consumer's already synchronized locked environment,
with the notebook extra and analysis dependencies installed. A pytest test can
borrow that interpreter and copy only its selected inputs:

```python
import sys
from pathlib import Path

from research_repo_tools.notebook_testing import isolated_project

def test_analysis(tmp_path):
    with isolated_project(
        Path(__file__).resolve().parents[2],
        [Path("notebooks/analysis.ipynb"), Path("data/example.csv")],
        parent=tmp_path,
        environment=Path(sys.prefix),
    ) as project:
        # Prepare domain inputs and assertions inside project.root.
        result = project.execute(
            Path("notebooks/analysis.ipynb"),
            env={"ANALYSIS_OUTPUT_DIR": str(project.root / "figures")},
        )
        assert result.returncode == 0, result.report
        assert result.report["failed_cell"] is None
        assert result.notebook_path.is_file()
```

The fixture copies the manifest, lockfile and Python selector without rewriting
them, excludes native toolchain setup, and removes only its temporary workspace
on context exit. Returned paths live until that exit. Cell failures and timeouts
return a failed report. The helper does not synchronize dependencies or certify
that installed packages match the lock; run the consumer's normal setup first.

Inspect Just's native metadata and dry-run text using the installed package's
Just version:

```python
from pathlib import Path

from research_repo_tools.just_inspect import dry_run, inspect_justfile

root = Path.cwd()
recipes = inspect_justfile(root).recipes
dependencies = {item["recipe"] for item in recipes["ci"]["dependencies"]}
assert "test-rust" in dependencies  # This assertion is consumer policy.
preview = dry_run(root, "release-notes", ["v1.2.3"])
assert "changelog notes" in preview.stderr
```

Dry-run preserves native stdout/stderr and argument boundaries. It does not run
recipe bodies, but Just still evaluates configuration and expressions; use
trusted Justfiles. See the [integration-test API contract](api.md) for overrides,
errors, path boundaries, environment handling, and migration details.
