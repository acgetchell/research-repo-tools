# Timing external commands

Use this workflow to investigate wall-clock command cost with a fixed plan.
It produces exploratory evidence; it does not qualify a native release benchmark
or make results from different hosts comparable. [Criterion workflows](RECORDING_PERFORMANCE.md)
retain their own sampling and release-eligibility requirements.

## Exploratory command timing

Use the `command-time` recipe from the matching installed Just template. Keep
workload arguments, source selection and interpretation in the consumer. This
example measures a native tool's version command, including process startup:

```toml
schema = 1
sources = ["Cargo.toml", "Cargo.lock"]
preflight = [["cargo", "--version"]]
warmups = 1
repeats = 5
timeout = 30

[probes]
cargo = ["cargo", "--version"]

[[cases]]
id = "cargo-version"
command = ["cargo", "--version"]
```

Save it as `tooling/command-timing.toml` and run it from the consumer root.
For an example with a build preflight, use `just templates command-timing.toml`.

```sh
just command-time tooling/command-timing.toml target/timing-first.json
```

Commands run with literal argv and per-command deadlines. The new JSON report
retains every preflight, warmup and sample, units, statuses, command/source/host/tool
identity and execution policy; the summary shows sample counts and minimum,
median and maximum wall seconds. Existing outputs are rejected before execution.
See the [timing contract](command-timing-api.md) for failure, freshness,
source-selection and measurement limits. These exploratory timings stay separate
from Criterion and scientific release/comparability evidence. Validate the
consumer's commands, inputs, and expected report before replacing an existing timing loop.
