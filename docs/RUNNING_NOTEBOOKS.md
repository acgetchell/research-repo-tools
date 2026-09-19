# Running notebooks

Notebook infrastructure is optional. Consumers own notebook selection, fast and
slow groups, input generation, scientific assertions, and tracked figures. The
shared commands never discover or execute additional notebooks implicitly.

## Environment

Declare `research-repo-tools[notebooks]` at the same exact version as the tooling
pin in a `notebook` dependency group, alongside the consumer's analysis packages.
Keep Python selection in `.python-version`, uv selection in `[tool.uv]`, and
dependencies in `uv.lock`. The optional extra supplies nbformat, nbclient, and
ipykernel; maintenance-only installations do not install them.

`notebooks sync` synchronizes the locked `dev` and configured notebook groups
with managed Python and registers a `research-repo-tools` kernel inside that
project environment. It does not install a user or system kernel. Existing Rust
tools must already be set up when the consumer needs native builds.

Set `group` to another declared dependency group when needed. Every notebook
recipe uses the same configured group. The underlying `notebooks group` command
prints its name without importing notebook dependencies or synchronizing tools;
the recipes use this lookup before selecting their locked environment.

```toml
[tool.research-repo-tools.notebooks]
group = "notebook"
cwd = "."
output-dir = "target/notebooks"
timeout = 600
outputs = "clear"
```

Paths resolve against the consumer root. `timeout` is a positive per-cell limit
in seconds; kernel startup is separately limited to 60 seconds. `outputs` is
`clear` or `preserve` and controls validation of source notebooks. `preserve`
permits deliberately tracked results; execution always clears prior results
in memory and leaves source files untouched.

## Commands and artifacts

The consumer template provides these recipes:

- `just notebook-check FILE...`: validate nbformat 4.5 structure, unique existing
  cell IDs, and the declared output policy without repairing files.
- `just notebook-clear FILE...`: deliberately remove outputs, counts, execution
  timing, and widget state; preserve cell IDs, source, attachments, and other metadata.
- `just notebook-execute FILE...`: run selected notebooks in fresh kernels using
  the configured working directory and the current locked project interpreter.
- `just notebook-lint FILE...`: check structure, output policy, Python syntax,
  Ruff rules, formatting, and ty types without executing or rewriting cells.
- `just notebook-sync`: synchronize notebook dependencies and the project kernel.

The underlying `notebooks execute` accepts `--cwd`, `--output-dir`, and `--timeout`
overrides. Sources must be inside the consumer root. Artifacts mirror their
root-relative paths beneath the output directory, which must be separate from
the source directories. All sources and destinations are validated before execution.
Each source gets an executed `.ipynb` and a `.report.json` sidecar, including on
cell failure or timeout. Reports record schema version, source SHA-256, Python
and package versions, working directory, timeout, result, and failing cell ID.
The lockfile hash identifies the declared dependencies. Reports describe execution
evidence, not scientific correctness or deterministic results.

Execution forces the selected interpreter even when notebook metadata names a
different kernel. It runs headlessly with private temporary Jupyter/IPython and
Matplotlib state, rejects cell errors even when tagged as expected errors, and
stops at the first failing notebook. Reports distinguish failure from success;
the CLI returns nonzero and prints the notebook and cell diagnostic. A failure
to publish artifacts is also an error. Files written by notebook code remain the
consumer's responsibility. Interrupted runs are not successful reports.

Output cleanup validates every selected notebook before publishing any change.
It uses rollback-capable file replacement; incomplete recovery reports backup
paths. Structure validation never generates or repairs cell IDs. Missing optional
dependencies produce installation guidance rather than a Python traceback.
Non-finite JSON numbers, including exponent overflow, are rejected during loading
before any selected notebook is changed or executed.

## Adoption

Keep existing helpers until the real consumer's selected workflow has been
validated against the shared commands. `notebooks check` covers structure and
output policy; `notebooks lint` also replaces the common Python lint/format/type
helper. Consumers retain their fast/slow selections, preparation recipes, and
scientific assertions.

## Python linting

Declare Ruff 0.16.8 or newer and ty 0.0.82 or newer in the consumer's development
dependencies and lock their versions. These are the tested minimum versions for
the shared native notebook contract. The notebook extra does not install or
upgrade either checker. The command runs their Python modules with the current
interpreter, so a different executable on PATH cannot substitute for a missing
project dependency. Run it through the consumer's locked environment.

All three checks run: Ruff syntax/lint, Ruff formatting, and ty types. Missing or
older checkers, invalid tool configuration, checker errors, and timeouts fail the
command. `notebooks lint --timeout SECONDS` sets a positive per-checker limit,
defaulting to 30 seconds. Findings identify the notebook, cell number and stable
ID, and the source line and column where available. Diagnostics go to stderr;
stdout reports notebooks that passed every gate.

Both tools read the original notebook directly. This preserves cross-cell
references and notebook-specific configuration without creating extracted Python
files. Ruff discovers configuration from each notebook's path; ty uses the
consumer project and current Python environment. Explicit selections override
file-discovery exclusions. Rule ignores and other consumer configuration still
apply. Ruff fixes, fix-only mode, and cache writes are disabled; formatting runs
in check mode, and ty warnings also fail the gate.

The native parsers understand IPython line/shell syntax, assignment magics,
top-level await, and Python bodies in supported cell magics such as `%%time`.
Non-Python cell-magic bodies such as `%%bash` are outside Python analysis; notebook
metadata declaring a non-Python language is rejected. Linting never runs a magic
or cell. Unlike historical text extraction, it preserves multiline strings and
maps diagnostics directly to cells. Configure notebook-specific rules against
`*.ipynb` paths rather than historical `*_notebook.py` filenames.
