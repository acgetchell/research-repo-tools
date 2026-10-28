# Consumer command reference

Use `just help` or bare `just` for the commands in your installed template.
The list below describes current source; [availability](../README.md#release-availability)
and the locked consumer version determine which interfaces can be used.

- [Just recipes](#just-recipes)

## Just recipes

For routine workflows, merge the [consumer justfile template](../src/research_repo_tools/templates/justfile) into
the consuming repository's justfile. These thin recipes invoke its locked package
version. After initialization, use `just` directly.
Run `just help` or bare `just` to list commands and their
arguments in lexicographic order.

| Consumer recipe | Purpose |
| --- | --- |
| `just audit` | Audit every maintained lockfile explicitly selected by the consumer |
| `just changelog` | Generate, normalize, and archive completed minor series |
| `just changelog-archive` | Archive existing notes without regenerating history |
| `just changelog-check` | Validate the whole root changelog and all archives without writing |
| `just changelog-preview` | Validate the generated root and archives without writing; print the root candidate |
| `just changelog-release TAG [DATE]` | Generate a prospective release from prepared citation metadata or an explicit date |
| `just check` | Run Python, Semgrep fixture, and zizmor validation; extend with consumer domain gates |
| `just ci` | Run the same canonical consumer gate as local validation |
| `just clean [ARGS...]` | Preview obsolete package-owned installs; `--apply` removes them; repeat `--keep-root PATH` to retain other consumers' pins |
| `just command-time CONFIG OUTPUT` | Acquire exploratory external-command timings into a new JSON report and print a summary |
| `just files COMMAND...` | Check raw line limits, list selected files or run a command over them |
| `just help` | List recipes and arguments in lexicographic order, with arguments |
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
| `just release-assets COMMAND...` | Capture/revalidate release targets or explicitly upload verified attachments |
| `just release-check TAG` | Check final release metadata and generated notes |
| `just release-evidence COMMAND...` | Create or verify exact-commit CI evidence and save file-based handoffs |
| `just release-first TAG DATE` | Prepare a first release after checking published stable history |
| `just release-notes TAG` | Print release notes from the root changelog or an archive |
| `just release-publish TAG [ARGS...]` | Approve a validated draft GitHub Release and trigger its registry workflow |
| `just release-reuse COMMIT ARGS...` | Save explicitly selected, verified PR source-check evidence for a squash release |
| `just release-tag TAG` | Create a local annotated tag from validated release notes |
| `just release-tag-preview TAG` | Inspect the release annotation without changing Git state |
| `just release-update TAG PREVIOUS DATE` | Prepare metadata with an explicit predecessor and date |
| `just release-verify TAG [ARGS...]` | Verify the exact stable registry version and GitHub Release/assets |
| `just review [base]` | Review branch and local changes; default to verified `origin/main` |
| `just review-uncommitted` | Review staged, unstaged, and non-ignored untracked changes |
| `just security` | Audit selected lockfiles, then scan for secrets |
| `just security-osv LOCKFILE...` | Audit explicitly selected uv.lock/Cargo.lock files |
| `just security-secrets [ARGS...]` | Scan full reachable history and current tracked/nonignored files |
| `just semgrep-check` | Validate the consumer's Semgrep rules and fixtures |
| `just semgrep-setup` | Provision and verify the package-pinned Semgrep environment |
| `just setup` | Install and verify declared tools, then synchronize the Python environment |
| `just shared-python-plan VERSION` | Preview mandatory Python-minimum and optional tool-profile adoption outside the old environment |
| `just shared-python-update VERSION` | Apply the migration and recreate the locked environment and notebook kernel |
| `just tectonic COMMAND...` | Discover or export existing native dependency environments |
| `just templates ARGS...` | Export a packaged configuration or workflow template |
| `just tools-check` | Check installed tools and versions without installing them |
| `just tools-export` | Verify tools and append their environment to `GITHUB_ENV` |
| `just tools-python-check` | Check inherited Python tool declarations, lock, and executable versions without changes |
| `just tools-sync-binaries` | Install and verify pinned release binaries without package synchronization or dependency builds |
| `just update` | Upgrade tools, then dependencies and environments, and audit the final locks |
| `just update-actions [ARGS...]` | Opt-in Actions pin update; use `--dry-run` or `--check` to preview the selected scope |
| `just update-cargo-dependencies` | Upgrade root Cargo requirements (including incompatible releases) and lock resolution; skip projects without a root Cargo.toml |
| `just update-cargo-tools` | Upgrade declared Cargo tools and release binaries; publish verified TOML pins |
| `just update-dependencies` | Run Cargo and Python dependency updates, then audit the final selected locks |
| `just update-python-dependencies` | Update direct dev pins, upgrade the full Python lock, and synchronize dev |
| `just update-tools` | Upgrade uv and managed tools without repeating shell configuration |
| `just update-uv` | Upgrade uv through its installation owner and reconcile its pin |
| `just validate CONFIGURATION [NAME...]` | Check configured example outputs |
| `just workflow-allowlist-check` | Check workflow and composite action references against strict consumer selected-actions settings |
| `just zizmor-check [ARGS...]` | Run local workflow audits; accept `--offline`, `--require-online`, and `--format sarif` |

To preview a prospective release, run
`just changelog-preview --tag v1.2.3 --date YYYY-MM-DD`.

## CLI families

The CLI groups below also cover capabilities used from custom recipes. Place
global root/configuration options before the group; consult
[configuration and exit status](configuration-api.md) for the shared contract.

| Capability | Commands | Contract |
| --- | --- | --- |
| Actions | `actions allowlist`, `update` | [Strict consumer allowlists and opt-in commit pin updates](UPDATING_DEPENDENCIES.md#actions-allowlists-and-opt-in-pin-updates) |
| Changelog | `changelog archive`, `check`, `generate`, `normalize`, `notes`, `tag` | Root `CHANGELOG.md`; completed minor series in `docs/archives/changelog/` |
| CI environment | `ci export` | Validate all single-line values before appending a GitHub environment command file |
| Coverage | `coverage report` | Cobertura summaries with deduplicated source lines |
| Dependencies | `deps check-uv`, `update-python`, `update-tools`, `update-uv` | Exact development pins; canonical Cargo SemVer; stable uv pins |
| Documentation | `docs check-lines` | Existing explicit-path Markdown policy: 160 characters with table exemptions |
| File selection | `files check-lines`, `list`, `run` | Configurable raw line gate, tracked/nonignored inputs, exclusions and portable argument batching |
| Notebooks | `notebooks advise`, `check`, `clear`, `execute`, `group`, `inspect`, `launch`, `lint`, `reset`, `sync` | Read-only review, locked JupyterLab, native Ruff/ty checks, execution reports, and explicit restoration |
| Papers | `papers check`, `normalize`, `source-date` | Explicit UTC dates, optional PDF text/geometry checks and validated metadata normalization |
| Performance | `performance assemble-suites`, `assets`, `baseline`, `command-time`, `compare`, `convert`, `discover`, `export`, `extract`, `fetch`, `host`, `measure`, `pack-suite`, `prepare`, `profile`, `promote`, `publish`, `readme`, `release`, `release-draft`, `release-upload`, `render`, `verify`, `verify-saved` | Complete configured measurement, retained evidence, release assets and publication |
| Python | `python check`, `fix`, `typecheck` | Complete tracked/nonignored inventory; native Ruff/ty policy; explicit fixes only |
| Releases | `release assets`, `check`, `evidence`, `gate`, `publish`, `registry`, `reuse`, `update`, `verify` | Metadata preparation, reviewed publication gates and exact registry/GitHub verification |
| Review | `review branch`, `review uncommitted` | Opt-in CodeRabbit review with verified default base and streamed findings |
| SARIF | `sarif split` | Consumer-selected drivers/namespaces, corrected rule indices and complete output generations |
| Security | `security osv`, `secrets` | Managed OSV/Gitleaks, explicit inputs, full history, redacted native reports |
| Semgrep | `semgrep check-fixtures`, `scan`, `setup` | Validate consumer rules, explicit inventory, reports and fixture expectations |
| Setup | `setup` | Require uv; install user Just and declared tools; sync the locked environment |
| Tectonic dependencies | `tectonic discover`, `export` | Read-only pkg-config/vcpkg discovery; consumer-owned provisioning |
| Templates | `templates NAME` | Shared changelog, release guide/OIDC workflow, just, TOML, and rumdl resources |
| Toolchain | `toolchain adopt`, `check`, `clean`, `export`, `python-check`, `python-tools-check`, `run`, `sync`, `sync-binaries`, `upgrade` | Exact declarations; managed installations and cleanup; verified execution and checked CI export |
| Validation | `validation cargo-examples`, `cargo-metadata`, `require`, `run` | Runtime Cargo example discovery, bounded live execution, package preflight and configured assertions |
| Workflow security | `zizmor check` | One declared scanner/persona, token discovery, explicit offline or required-online audits |
