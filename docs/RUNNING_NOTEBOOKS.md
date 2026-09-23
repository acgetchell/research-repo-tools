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

Set `group` to another declared dependency group when needed. Notebook recipes
that need optional dependencies use the same configured group. Inspection uses
only the tooling group. The underlying `notebooks group` command
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

- `just notebook-advise FILE...`: report opt-in review policy, with `--strict`
  to fail on advisory warnings.
- `just notebook-check FILE...`: validate nbformat 4.5 structure, unique existing
  cell IDs, and the declared output policy without repairing files.
- `just notebook-clear FILE...`: deliberately remove outputs, counts, execution
  timing, and widget state; preserve cell IDs, source, attachments, and other metadata.
- `just notebook-execute FILE...`: run selected notebooks in fresh kernels using
  the configured working directory and the current locked project interpreter.
- `just notebook-inspect FILE...`: inventory existing cells and problems awaiting
  repair; `--json` selects the [structured schema](notebook-inspection.md), and
  `--no-preview` omits source text.
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

The inspection and advisory commands added after `0.1.4` replace generic summary
and review helpers once the consumer has tested the published release against its
workflow. Retain only consumer invocation and policy checks after adoption. The
base-package inspection command tolerates repairable nbformat 4 structure; strict
commands retain their nbformat 4.5 admission rules.

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

## Review advisories

`notebooks advise` is a separate read-only pass that composes with `notebooks lint`.
Neither command implicitly enables the other. Advice requires the notebook extra
and the same valid nbformat 4.5 structure and Python metadata as lint, but ignores
stored-output policy. Use inspection before repairing older or malformed files.
Advice does not certify syntax, formatting, types, or output cleanliness; keep
native lint in the workflow.

Configure `[tool.research-repo-tools.notebooks.advice]` as shown in the
[README](../README.md#notebooks). Unknown fields and invalid values fail before
analysis. Supported fields are:

| Field | Default | Behavior |
| --- | --- | --- |
| `descriptive-ids` | `true` | Warn about hexadecimal IDs of 8–64 characters, UUID-shaped IDs, decimal IDs, and `cell`, `code`, `markdown`, or `raw` followed by a number (optional hyphen/underscore) |
| `ruff-rules` | `[]` | Distinct Ruff codes or prefixes, supplied to native Ruff `--select`; consumer rule ignores, per-file ignores, and rule settings apply |
| `strict` | `false` | Fail on warnings; CLI `--strict` also enables this policy |
| `subprocess-timeout` | `false` | Warn about direct `subprocess.run`, `call`, `check_call`, or `check_output` calls with missing or literal `None` timeouts |

ID warnings are heuristics, not claims that an ID was generated or unstable.
They apply to all cell types, never change IDs, and remain separate from hard ID
validity or uniqueness errors. Stable descriptive IDs need no prescribed spelling.

Ruff 0.16.8 or newer is required only when `ruff-rules` is nonempty; ty is needed
by lint, not advice. Generic Python advice uses
[Ruff's native rules](https://docs.astral.sh/ruff/rules/), including `ANN` for
annotations, `BLE001` for broad exceptions, `TID251` for consumer-defined banned
imports, and the relevant `S`/`ASYNC` subprocess rules. Native Ruff reads the
original notebook and understands supported IPython syntax. Its missing or old
installation produces guidance. Fixes and caches are disabled, explicit selections
override exclusions, and `--timeout` is a positive per-checker bound (default 30s).

The supplemental timeout heuristic parses code cells with Python's AST without
execution. Cells that cannot be parsed, including IPython line/shell/assignment
and cell magics, receive an informational skip. It does not strip lines, so
multiline strings retain their meaning. It checks only calls spelled with the
literal `subprocess` qualifier, without resolving aliases, shadowing, wrappers,
`Popen` lifecycles, expanded keyword dictionaries, or runtime timeout values.
Review its warnings in context; an explicit expression is not proof of a finite
bound. Syntax validation remains native lint's responsibility.

Warnings and informational skips identify original one-based cell numbers and
existing IDs on stderr; stdout reports warning and skip counts. Warnings return
zero unless strict mode is enabled. Informational skips never fail strict mode.
Invalid structure, Python metadata, configuration, missing checkers, native syntax
errors, malformed checker results, checker failures, and timeouts always return
nonzero. Diagnostics can include source identifiers and rule messages; the
inspection `--no-preview` option does not redact advisory or native lint output.
