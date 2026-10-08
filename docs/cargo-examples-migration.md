# Migrating static Cargo example validation

Keep `validation run` for general command plans. Migrate an example-only static
inventory to `validation cargo-examples` after pinning an exact published release
that includes the [Cargo runner contract](cargo-examples-api.md). A merged
implementation or closed upstream issue alone is not a registry release.

The old schema-1 validation plan has `prerequisites` and `[[checks]]` containing
build commands, explicit binary paths, timeouts and `expect` markers. The new
schema-1 example plan is a separate contract selected by a separate CLI action;
passing an old plan to the new action is an error, not implicit conversion.

| Static plan element | Consumer migration |
| --- | --- |
| Cargo prerequisite and explicit build-default check | Provide Cargo/toolchain beforehand; the runner discovers targets and owns one ordinary-example build |
| Hard-coded ordinary binary paths | Omit them; metadata and compiler artifacts supply target names and executable paths |
| Static target inventory | Omit `include` for runtime discovery of all binary examples; retain consumer inventory/coverage tests |
| Build-diagnostics check with features | Set `features` under that target's `examples` table, including when its default binary is a stub |
| Per-check execution timeout | Preserve it as root `timeout` or a per-example override |
| Build timeout | Preserve it as `build-timeout`, applied to each build |
| Scientific stdout expectations | Copy unchanged into the corresponding target's `expect`; upstream does not choose or rewrite them |
| Arguments or intentional omissions | Preserve literal `args` and explicit consumer `include`/`exclude` policy |

Use the [README example](../README.md#cargo-examples) to wire a thin Just recipe.
The runner's order is sorted ordinary targets followed by sorted feature
overrides. A static plan depending on custom interleaving or arbitrary commands
should retain `validation run` for those steps rather than assume its list order
carries across.

Previously, `validation run` captured each command and printed output when it
completed. The example runner inherits output immediately when there are no
markers. Per-target `expect` enables live stdout forwarding with exact matching;
stdin/stderr stay inherited. Read the documented buffering, temporary-disk,
newline and direct-child timeout limits before adopting it. Wrapping the complete
runner in an outer captured `validation run` check buffers that outer command and
therefore hides its live output until completion; place per-example markers in
the example configuration to preserve live forwarding.

Keep focused consumer tests proving intended Cargo coverage, default-feature
behavior, real feature-specific execution, budgets, and scientific expectations.
Shared discovery, process failures, malformed configuration and distribution
regressions live upstream. Delaunay's current static plan informed this contract;
its scientific content and separate adoption issue `acgetchell/delaunay#626`
remain consumer work. Cross-repository rollout is tracked separately in #69.
