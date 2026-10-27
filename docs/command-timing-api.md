# Exploratory external-command timing

The release containing #134 adds `performance command-time` and
`research_repo_tools.command_timing`. Pin the verified published version before
adoption. This format is separate from Criterion, schema-4 acquisition,
complete-run/v3, release eligibility, and scientific comparability evidence.
It does not feed their promotion or publication paths. Workload selection,
throughput definitions, correctness assertions, and scientific interpretation
remain consumer policy. No consumer workloads run during package validation.
See the [README example](../README.md#exploratory-command-timing).

## Declaration

Trusted TOML requires integer `schema = 1`, nonempty `sources`, and 1–256
`[[cases]]` tables. Unknown keys and duplicate IDs fail before execution.

| Key | Contract and default |
| --- | --- |
| `cases` | Each table requires `id` and literal `command` argv; no other keys |
| `environment` | String overlay on the inherited environment; empty; names cannot contain `=` or NUL, and must be distinct ignoring case |
| `preflight` | Ordered list of up to 64 literal command argv arrays; empty; builds belong here |
| `probes` | Up to 64 named literal tool/version commands; empty; both output byte streams are retained as base64 |
| `repeats` | Integer 1–1000 samples per case; 5 |
| `schema` | Integer 1 |
| `sources` | Nonempty unique root-relative file paths or glob patterns; every pattern must match regular files |
| `timeout` | Finite seconds greater than zero and at most 86400 per child command, including preflights and probes; 300 |
| `warmups` | Integer 0–100 per case; 1 |

Case and probe IDs are 1–128 ASCII letters, digits, dots, underscores or hyphens,
starting with a letter or digit. Argv includes the executable and permits empty
arguments, spaces and Unicode. NUL is rejected. There is no interpolation,
expression language, shell redirection, workload expansion or throughput formula.
The shared [process discovery rules](api.md#python-process-api) apply, including
native Windows batch-file limitations. Prefer native executables for literal argv.
Source globs use the producing platform's native case rules, recorded in
`runner.platform`. Offline readers apply those recorded rules on every host.
Source paths use the shared contained-path contract: no traversal, links,
junctions, special files or portable aliases. The declaration itself is always
included in the source inventory. Declare all inputs that matter to the workload;
unlisted files, external data, dependencies and loaded libraries are not identified.

## Execution and timer

All children run in the selected root. Preflights run once in declaration order,
then probes run by name. Cases run by ID in lexicographic order, each with all
warmups followed by all samples. Case executables resolve after preflights, so
builds may create them. Every run is fresh; no workload-generated timing files
are read. One fixed effective environment is used throughout acquisition;
Windows overlays apply environment names without regard to case.

Elapsed time is the difference between two `time.perf_counter_ns()` observations
around the shared live process runner. The monotonic timer measures elapsed wall
time, including process launch, waiting, and runner overhead; it excludes source
hashing, version probes, and preflight from case samples. It is not CPU time,
internal algorithm time, memory use, a confidence interval, or a significance
result. OS scheduling, subprocess deadline polling, program buffering and output
sinks affect the observations, especially for short commands. No overhead is
subtracted and no samples are filtered or aggregated away.

Stdin, stdout and stderr are inherited for workloads and preflights. Probes get
empty stdin and captured byte output. Child output is visible before the summary;
`--output` contains JSON only. Each command has its own deadline; the complete
run has no additional aggregate deadline. A timeout terminates and reaps the
direct child; consumers own descendant isolation. Commands are trusted and may
have their own side effects. A failing preflight, probe, warmup or sample aborts
acquisition with its original command error or timeout. No partial report is
returned or published. Successful observations preceding failure are not an
accepted timing report.

## Retained report

`COMMAND_TIMING_SCHEMA` is
`research-repo-tools/exploratory-command-timing/v1`. JSON uses sorted keys,
UTF-8, LF, and a final newline. The layout and sorted case ordering are
stable; elapsed values, UUID run ID, completion timestamp, root and host
observations vary between acquisitions.

The report retains:

- `purpose = "exploratory-only"`, `unit = "ns"`, run ID and UTC completion time.
- Complete effective `policy` and fixed `execution` policy, including order,
  environment overlay, deadline, inherited streams, shell and cleanup semantics.
- Every preflight, warmup and sample as a separate `observations` row with
  command identity, stage, zero-based index, integer `elapsed_ns`, `returncode`
  and `status`. Accepted reports require every row to succeed with status 0.
- `configuration`, selected root and sorted `source` inventory, with exact byte
  SHA-256 digests and sizes, including dirty/new files selected by the declaration.
  No Git revision or whole-repository identity is inferred.
- `commands` keyed by `preflight/INDEX`, `probe/NAME` and `case/ID`, retaining
  literal argv, resolved native executable path, exact executable digest and size.
- Successful configured `tools` probe stdout/stderr bytes as base64 and statuses;
  runner package/Python versions, native platform and interpreter identity; timer implementation,
  resolution and monotonic/adjustable observations; shared versioned host metadata.
  Missing host observations remain null. Host RAM is not workload memory usage.
- SHA-256 of the effective environment; inherited values are not copied into the
  report. Declared overlay values, paths and argv are retained verbatim, so keep
  secrets out of declarations and command arguments.

The summary lists case ID, sample count, minimum, median and maximum seconds.
Only samples contribute; every warmup and preflight remains in JSON. It makes
no throughput or cross-run comparison claim and can be regenerated offline.

Reports require complete ordered observations, matching declared commands, known
units and execution policy, valid identities and finite positive observations.
Duplicate JSON keys, nonfinite numbers, unsupported schemas, failed statuses,
missing/extra cases and shortened samples are rejected. Offline validation checks
structure, not authentic execution or whether a report still describes today's
sources. A retained report describes its captured inputs; there is no automatic
freshness window or scientific promotion.

## Publication and freshness

`publish_command_timing` rejects an existing output before running commands and
requires it to be distinct from selected inputs, including portable aliases.
It never reuses a previous result as a new run. The output remains absent on
failed acquisition. Inputs, glob inventories, declaration bytes and executable
identities are checked again after measurement and immediately before file
replacement, including after staging. A new competing output is preserved.
Exclusive writer control is required: these checks detect ordinary races and
do not provide an atomic filesystem snapshot or protection against adversarial
change-and-restore. The shared staged-file transaction handles publication
failures and reports incomplete rollback. Use a new output path for every run.

## Public Python API

| Import from `research_repo_tools.command_timing` | Contract |
| --- | --- |
| `COMMAND_TIMING_SCHEMA` | Versioned exploratory schema identifier above |
| `measure_commands(root: Path, configuration: str) -> bytes` | Parse trusted root-relative TOML, acquire and validate all observations, return JSON bytes; writes no report |
| `publish_command_timing(root: Path, configuration: str, output: str) -> bytes` | Acquire fresh observations and publish JSON to an absent root-relative output; return identical bytes |
| `summarize_commands(payload: bytes) -> str` | Strict offline validation followed by the deterministic tab-separated summary |
| `validate_command_timing(payload: bytes) -> None` | Strict offline structural validation without commands, host probes or source access |

Malformed declarations/reports, changed inputs and invalid observations raise
`ValueError`; missing executables raise `ExecutableNotFoundError`; command
failures/timeouts and OS errors retain the shared process exceptions. No package
or consumer repository is built implicitly outside declared preflights.

CDT adoption belongs separately to causal-triangulations #303 after verified
v0.2.4 publication under research-repo-tools #146. Preserve its current script,
workload arguments and interpretation until its published replacement is pinned
and integration-verified. Stderr assertion adoption is separately owned by #301.
