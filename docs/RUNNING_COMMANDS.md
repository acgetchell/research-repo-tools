# Consumer command reference

Use `just help` or bare `just` for the commands in your installed template.
The list below describes current source; [availability](../README.md#release-availability)
and the locked consumer version determine which interfaces can be used.

- [Just recipes](#just-recipes)
- [CLI families](#cli-families)

## Just recipes

For routine workflows, merge the [consumer justfile template](../src/research_repo_tools/templates/justfile) into
the consuming repository's justfile. These thin recipes invoke its locked package
version. After initialization, use `just` directly.
Run `just help` or bare `just` to list commands and their
arguments in lexicographic order.

| Consumer recipe | Purpose |
| --- | --- |
| `just actions-check` | Check declared workflow/composite pin consistency and explicit policy tags offline |
| `just audit` | Audit every maintained lockfile explicitly selected by the consumer |
| `just cargo-docs PACKAGE [ARGS...]` | Validate public library docs from a packaged crate on declared stable Rust |
| `just changelog` | Generate, normalize, and archive completed minor series |
| `just changelog-archive` | Archive existing notes without regenerating history |
| `just changelog-check` | Validate the whole root changelog and all archives without writing |
| `just changelog-preview [ARGS...]` | Validate the generated root and archives without writing; print the root candidate |
| `just changelog-rebuild [--apply]` | Show the complete rebuilt changelog and archive diff or apply it |
| `just changelog-release TAG [DATE]` | Generate a prospective release from prepared citation metadata or an explicit date |
| `just check` | Run Python, Semgrep fixture, and zizmor validation; extend with consumer domain gates |
| `just ci` | Run the same canonical consumer gate as local validation |
| `just clean [ARGS...]` | Preview obsolete tool installs and retained coverage; `--apply` removes them; repeat `--keep-root PATH` to retain other consumers' pins |
| `just clean-coverage [--dry-run \| --apply]` | Preview/remove retained coverage shards and runs while preserving current reports |
| `just command-time CONFIG OUTPUT` | Acquire exploratory external-command timings into a new JSON report and print a summary |
| `just dependency-source NAME [--apply]` | Preview or apply a verified Python/Cargo source and lock transition with recoverable multi-file publication |
| `just files COMMAND...` | Check raw line limits, list selected files or run a command over them |
| `just help` | List recipes and arguments in lexicographic order |
| `just notebook-advise FILE... [--strict]` | Report configured review warnings, optionally failing on them |
| `just notebook-check FILE...` | Validate notebook structure, cell IDs, and output policy |
| `just notebook-clear FILE...` | Deliberately clear generated notebook state |
| `just notebook-execute FILE...` | Execute selected notebooks and write results and reports |
| `just notebook-inspect FILE... [--json] [--no-preview]` | Inventory cells and repair problems without generating IDs or loading Jupyter |
| `just notebook-launch [NOTEBOOK] [--browser \| --no-browser] [--scratch-dir PATH]` | Launch locked JupyterLab, optionally opening one notebook, with private session caches |
| `just notebook-lint FILE...` | Check structure, output policy, Python syntax, Ruff rules/formatting, and ty types |
| `just notebook-reset [PATH...] [--revision REF] [--apply]` | Preview source restoration and declared cleanup; explicitly apply with `--apply` |
| `just notebook-sync` | Synchronize locked notebook dependencies and the project kernel |
| `just papers COMMAND...` | Read explicit source dates, check PDFs or normalize their metadata |
| `just performance COMMAND...` | Compare Criterion samples, handle assets, and verify, render, or publish retained evidence |
| `just performance-prepare CONFIG [ARGS...]` | Save independent release assertions in ignored scratch storage |
| `just performance-readme CONFIG [ARGS...]` | Publish README using retained evidence and prepared or explicit assertions |
| `just performance-release CONFIG [ARGS...]` | Measure the prepared pair and retain/promote its report |
| `just python-check` | Check lint, formatting, and types for all tracked and nonignored Python, including fixtures |
| `just python-fix` | Apply configured Ruff fixes and formatting to the same complete inventory |
| `just python-typecheck` | Run native ty without modifying source |
| `just release-assets COMMAND...` | Prepare drafts, retrieve original tag-push receipts, capture/revalidate targets or explicitly upload attachments |
| `just release-cargo-fetch [--manifest-path PATH]` | Prepare locked dependencies in the managed Cargo cache used for offline release checks. Set `release_cargo := "true"` if Cargo is declared only in a subdirectory. |
| `just release-check TAG` | Check final release metadata and generated notes |
| `just release-dependencies` | Verify selected upstream published pins, lock and executing runtime bytes |
| `just release-dependency-builds` | Rebuild configured Python sources and authenticate their local wheel outputs |
| `just release-evidence COMMAND...` | Create/verify exact-commit evidence, check published Python bytes, and save handoffs |
| `just release-first TAG DATE` | Prepare a first release after checking published stable history |
| `just release-notes TAG` | Print release notes from the root changelog or an archive |
| `just release-publish TAG [ARGS...]` | Approve a validated draft GitHub Release and trigger its registry workflow |
| `just release-registry TAG --expect absent\|present` | Check exact version visibility using the configured package and registry |
| `just release-reuse COMMIT ARGS...` | Save explicitly selected, verified PR source-check evidence for a squash release |
| `just release-runtime COMMAND...` | Resolve, record, install or verify a dedicated isolated runtime |
| `just release-verify TAG [ARGS...]` | Verify the exact stable registry version and GitHub Release/assets |
| `just release-version-check` | Check synchronized metadata and complete dated release notes |
| `just review [base]` | Review branch and local changes; default to verified `origin/main` |
| `just review-deny PENDING` | Consume a saved quote without invoking CodeRabbit |
| `just review-resume PENDING ARGS...` | Attempt one explicitly approved review with an unenforced quote |
| `just review-uncommitted` | Review staged, unstaged, and non-ignored untracked changes |
| `just security` | Audit selected lockfiles, then scan for secrets |
| `just security-check-exceptions CANDIDATE [ARGS...]` | Compare a proposed Gitleaks policy with the current or selected baseline |
| `just security-findings [ARGS...]` | Read the redacted Gitleaks history and working-tree JSON reports |
| `just security-osv LOCKFILE...` | Audit explicitly selected uv.lock/Cargo.lock files |
| `just security-secrets [ARGS...]` | Scan full reachable history and current tracked/nonignored files |
| `just semgrep-check` | Validate the consumer's Semgrep rules and fixtures |
| `just semgrep-setup` | Verify the locked Semgrep scanner in the shared Python environment |
| `just setup` | Install and verify declared tools, then synchronize the Python environment |
| `just shared-python-plan VERSION` | Preview mandatory Python-minimum and optional tool-profile adoption outside the old environment |
| `just shared-python-update VERSION` | Apply the migration and recreate the locked environment and notebook kernel |
| `just tag TAG [--commit SHA] [--force]` | Verify the target and release prerequisites, then report the created local tag and annotation |
| `just tag-preview TAG [--commit SHA] [--force]` | Check the reviewed commit and preview the annotation, including for an unpublished replacement |
| `just tectonic COMMAND...` | Discover or export existing native dependency environments |
| `just templates ARGS...` | Export a packaged configuration or workflow template |
| `just tooling-run ARGS...` | Verify a released version or GitHub commit and run isolated tooling |
| `just tools-check [--tool NAME]... [--json]` | Check installed tools and versions without installing them |
| `just tools-export [--tool NAME]... [--file PATH]` | Verify tools and append their environment to `GITHUB_ENV` or an explicit file |
| `just tools-python-check` | Check inherited Python tool declarations, lock, and executable versions without changes |
| `just tools-run [--tool NAME]... -- COMMAND...` | Run a command with verified managed tools |
| `just tools-sync [--tool NAME]... [--dry-run]` | Install and verify the catalog or selected tools without synchronizing Python groups |
| `just tools-sync-binaries` | Install and verify pinned release binaries without package synchronization or dependency builds |
| `just update` | Upgrade tools, then dependencies and environments, and audit the final locks |
| `just update-actions [ARGS...]` | Opt-in Actions pin update; use `--dry-run` or `--check` to preview the selected scope |
| `just update-cargo-dependencies` | Upgrade root Cargo requirements (including incompatible releases) and lock resolution; skip projects without a root Cargo.toml |
| `just update-cargo-tools` | Upgrade declared Cargo tools and release binaries; publish verified TOML pins |
| `just update-dependencies` | Run Cargo and Python dependency updates, then audit the final selected locks |
| `just update-python-dependencies` | Update dev pins, locks and environments. Set `supplier_update` to a named source to enable [supplier adoption](UPDATING_DEPENDENCIES.md#automatically-adopting-a-published-supplier). |
| `just update-tools` | Upgrade uv and managed tools without repeating shell configuration |
| `just update-uv` | Upgrade uv through its installation owner and reconcile its pin |
| `just update-version TAG [ARGS...]` | Prepare release metadata; optionally override predecessor or date |
| `just validate CONFIGURATION [NAME...]` | Check configured example outputs |
| `just workflow-allowlist-check` | Check workflow and composite action references against strict consumer selected-actions settings |
| `just zizmor-check [ARGS...]` | Run local workflow audits; accept `--offline`, `--require-online`, and `--format sarif` |

To preview a prospective release, run
`just changelog-preview --tag v1.2.3 --date YYYY-MM-DD`.

## CLI families

The CLI groups below also cover capabilities used from custom recipes. Spell
long options in full, including `--apply` and `--approve`. Place global
root/configuration options before the group. See
[configuration and exit status](configuration-api.md) for the shared contract.

| Capability | Commands | Contract |
| --- | --- | --- |
| Actions | `actions allowlist`, `check`, `update` | [Strict consumer allowlists, offline pin consistency, and opt-in updates](UPDATING_DEPENDENCIES.md#actions-allowlists-and-opt-in-pin-updates) |
| Changelog | `changelog archive`, `check`, `generate`, `normalize`, `notes`, `rebuild`, `tag` | Root `CHANGELOG.md`; completed minor series in `docs/archives/changelog/` |
| CI environment | `ci export` | Validate all single-line values before appending a GitHub environment command file |
| Cleanup | `clean` | Compose tool-installation and coverage cleanup; preview by default; `--keep-root` retains other consumers' tool pins |
| Coverage | `coverage clean`, `report` | Preview/remove retained Python coverage artifacts; summarize Cobertura with deduplicated source lines |
| Dependencies | `deps check-uv`, `source`, `update-python`, `update-tools`, `update-uv` | Verified source changes and exact tool pins. Use `update-python --adopt-supplier SOURCE` for automatic supplier updates. |
| Documentation | `docs check-lines` | Existing explicit-path Markdown policy: 160 characters with table exemptions |
| File selection | `files check-lines`, `list`, `run` | Configurable raw line gate, tracked/nonignored inputs, exclusions and portable argument batching |
| Notebooks | `notebooks advise`, `check`, `clear`, `execute`, `group`, `inspect`, `launch`, `lint`, `reset`, `sync` | Read-only review, locked JupyterLab, native Ruff/ty checks, execution reports, and explicit restoration |
| Papers | `papers check`, `normalize`, `source-date` | Explicit UTC dates, optional PDF text/geometry checks and validated metadata normalization |
| Performance | `performance assemble-suites`, `assets`, `baseline`, `capture`, `command-time`, `compare`, `convert`, `discover`, `export`, `extract`, `fetch`, `first-release`, `host`, `measure`, `pack-suite`, `prepare`, `profile`, `promote`, `publish`, `readme`, `release`, `release-draft`, `release-upload`, `render`, `verify`, `verify-saved` | Complete configured measurement, retained evidence, release assets and publication |
| Python | `python check`, `fix`, `typecheck` | Complete tracked/nonignored inventory; native Ruff/ty policy; explicit fixes only |
| Releases | `release assets`, `check`, `dependencies`, `dependency-builds`, `evidence`, `gate`, `publish`, `registry`, `reuse`, `runtime`, `update`, `verify`, `visibility` | Metadata preparation, published-only dependency gates, isolated runtime policies and exact registry/GitHub verification. `visibility` requires v0.3.0 or a supplying commit. |
| Review | `review branch`, `review deny`, `review resume`, `review uncommitted` | Opt-in review, typed quote-and-stop and one-use explicit paid resume |
| SARIF | `sarif split` | Consumer-selected drivers/namespaces, corrected rule indices and complete output generations |
| Security | `security check-exceptions`, `findings`, `osv`, `secrets` | Scan dependencies and secrets, inspect redacted findings, and compare a proposed Gitleaks policy with the baseline. [Exception review](RUNNING_CHECKS.md#reviewing-gitleaks-exceptions) |
| Semgrep | `semgrep check-fixtures`, `check-reports`, `scan`, `setup` | Validate consumer rules, explicit inventory, reports and fixture expectations |
| Setup | `setup` | Require uv; install user Just and declared tools; sync the locked environment |
| Tectonic dependencies | `tectonic discover`, `export` | Read-only pkg-config/vcpkg discovery; consumer-owned provisioning |
| Templates | `templates NAME` | Shared changelog, release guide/OIDC workflow, just, TOML, and rumdl resources |
| Toolchain | `toolchain adopt`, `check`, `clean`, `export`, `python-check`, `python-tools-check`, `run`, `sync`, `sync-binaries`, `upgrade` | Exact declarations; managed installations and cleanup; verified execution and checked CI export |
| Validation | `validation cargo-docs`, `cargo-examples`, `cargo-metadata`, `cargo-package`, `require`, `run` | Cargo documentation, example discovery, metadata checks, clean dry runs and configured assertions |
| Workflow security | `zizmor check` | One declared scanner/persona, token discovery, explicit offline or required-online audits |

`validation cargo-package --package NAME --output DIRECTORY` runs locked crates.io publish dry runs with default and all features.
Each uses a fresh Cargo target directory. The command compares Cargo's packaged and dry-run registry bytes across both runs, then retains the
verified crate. Omit `--output` to validate without retaining a copy.
