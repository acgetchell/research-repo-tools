# Updating dependencies and toolchains

Run setup first, review local changes, and select the update scope below.
`just update` changes declared versions and environments, then audits the final
locks. Review the resulting manifest/lock diffs and run the consumer checks.
Use [installation and recovery](INSTALLING.md) for provisioning failures and
[the adoption map](migration.md) when changing the shared-package pin.

- [Shared Python adoption](#shared-python-adoption)
- [Shared Python tool versions](#shared-python-tool-versions)
- [Dependency and tool updates](#dependency-and-tool-updates)
- [Actions allowlists and opt-in pin updates](#actions-allowlists-and-opt-in-pin-updates)

## Shared Python adoption

Every managed consumer automatically inherits the exact installed release's
published `Requires-Python` minimum. Current releases require Python 3.15.
This language-feature guard applies to installable applications and dependency-only
projects. The selected development minor is also 3.15. No opt-in is required,
and development inheritance controls cannot disable the guard.

Optionally mirror the release's selected development interpreter as well:

```toml
[tool.research-repo-tools.toolchain]
inherit-python = true
```

The installed release's support minimum and selected development minor are
separate authorities. Adoption intersects `project.requires-python` with the
published minimum, preserving stricter lower bounds, upper bounds, and exclusions.
Incompatible ranges fail for review before resolution or publication. Installable
consumers also retain and reconcile uv constraints on the shared tooling groups.
With `inherit-python = true`, `.python-version` mirrors the selected development
minor. Otherwise a compatible local selector remains; an obsolete or missing
selector advances to the shared selection. A stricter range excluding that
selection needs a compatible local selector or an explicit compatibility decision.
Ruff and ty
infer targets from project metadata. Consumer lint rules, fixture exceptions,
notebook policy, and deliberate lower targets remain consumer decisions.

After the target version is published, use the packaged standalone bootstrap:

```sh
just templates python-bootstrap.py --output python-bootstrap.py
# Merge the matching published Justfile recipes into the consumer's Justfile.
just shared-python-plan 0.2.3
just shared-python-update 0.2.3
just python-check
```

These recipes run the exact target package outside the old project environment,
using the packaged standard-library-only `python-bootstrap.py` beside the Justfile.
Copy both templates when installing this contract. The helper runs on older
uv-supported Python, reads the exact target release's PyPI `Requires-Python`, then
requests a compatible managed interpreter for the isolated target package. The
executing package's installed metadata remains the support authority. An obsolete
selector, lock, environment or Python on PATH cannot control startup. Preview
resolves and installs a temporary candidate; it may download Python/packages
and populate caches, but leaves consumer files and `.venv` unchanged. Apply
updates all direct shared-package pins (including extras), application metadata,
`uv.lock`, the environment, and the configured notebook group's project kernel.
Resolution and candidate installation must succeed before publication.

For offline bootstrap or local wheel/sdist evaluation, the helper accepts
`--metadata-file PATH` containing saved exact-release PyPI JSON. Supply the local
artifact registry through uv's `UV_FIND_LINKS`; target name/version are verified,
and the installed distribution still enforces its own metadata before adoption.

A caught failure restores original files and the prior environment. Recovery
errors identify the retained backup; do not delete it before recovering. Close
processes using `.venv` on Windows before applying. This is not a crash-atomic
or concurrent-writer transaction. Include every required source in the tracked
or nonignored inventory; external local path dependencies need a separately
reviewed migration. `just python-check` detects mirror drift without repairing
it or downloading an interpreter. Ordinary Python/toolchain checks and setup
reject insufficient application metadata even with `inherit-python = false`
or no setting. Routine checks do not advance versions or rewrite declarations.

The metadata change is a public compatibility change: rebuild and validate the
consumer wheel and sdist after adoption, then publish those artifacts through
the consumer's reviewed release workflow. Their `Requires-Python` rejects older
interpreters before execution, including installation without tooling groups or
research-repo-tools. Run the ordinary guard in CI before consumer builds.
An implementation merge does not publish this capability; consumer upgrades
require an exact PyPI release containing it. See the [migration guide](migration.md).

## Shared Python tool versions

The optional `python-tools` extra is the single Ruff, ty, and pytest version
authority for each exact shared release. Read that release's optional dependency
metadata for its pins. Base installations do not include these development tools. Opt in independently of `inherit-python`:

```toml
[tool.research-repo-tools.toolchain]
inherit-python-tools = true
```

Keep your current exact shared-package pin in `tooling` and include that group
from `dev`. The same standalone adoption recipes prepare the extra, declarations,
lock and environment. This example targets the published quickstart pin:

```sh
just shared-python-plan 0.2.3
just shared-python-update 0.2.3
just tools-python-check
just python-check
```

Preview prints concrete manifest, selector (when inherited), and lock diffs after
resolving and installing a private candidate. Adoption adds `python-tools` to all
shared-package group requirements, retaining other extras such as `notebooks`.
It converts old simple exact Ruff/ty/pytest pins in dependency groups into
unversioned requirements, so the installed release owns their versions. Compatible
ranges can also be retired; conflicting ranges, conditional requirements, URLs,
extra requests, and uv source/constraint overrides fail before publication.
Public runtime or optional dependencies on these tools require a deliberate
consumer decision: adoption does not rewrite them. Other dependencies, native
lint/type settings, precise fixture exceptions, and notebook packages stay local.

Tool inheritance retains compatible `.python-version` and native Python targets;
the mandatory application minimum still applies. uv constrains the groups
containing the shared package to its runtime requirement while retaining local
restrictions. An incompatible selected interpreter or runtime range fails
explicitly. Both forms of inheritance use the
same candidate verification and recoverable apply process described above.

`just tools-python-check` checks declarations, locked versions, and the actual
executables selected by PATH. `just python-check`, `just python-typecheck`, and
managed tool checks enforce the profile too. They never synchronize or upgrade.
`just setup` synchronizes the existing lock. `just update-python-dependencies`
updates unrelated development pins and the lock while retaining the exact shared
release and inherited versions. Upgrade the profile only by adopting another exact
shared release. Tools outside Ruff/ty/pytest remain consumer-owned.

To opt out, disable `inherit-python-tools`, remove only `python-tools` from each
shared-package requirement, and choose consumer-owned tool requirements in the
appropriate groups. Keep any `notebooks` extra and `inherit-python` setting you
still need. Run `just update-python-dependencies`, then `just setup` and your
validation gates. Disabling the check alone does not remove the extra's pins.
Retain downstream orchestration until the published pin passes its focused checks.

## Dependency and tool updates

`just update` runs `update-tools` before `update-dependencies`. Every recipe stops
at its first failed command; completed steps remain applied for review and retry.
The aggregate is not a transaction across package managers. Dependency-only
recipes use the installed, declared toolchain and leave uv and Cargo tool pins
unchanged. Run setup first when adopting or changing tool declarations.
Update launchers synchronize only `tooling` and retain installed consumer
dependencies, so they can start before a native consumer is ready to rebuild.

`just update-python-dependencies` advances exact direct `dev` pins, upgrades the
entire lock resolution within the resulting manifest constraints, and explicitly
synchronizes `dev`, even with `default-groups = []`. The final sync runs with
checked managed tools so native Python builds can find Rust. Included groups,
including the pinned shared package in `tooling`, retain their declared constraints.

Starting with v0.2.4, candidate resolution retains named uv indexes, marker-split
package source mappings, explicit-index isolation, overrides, constraints, build
constraints and supported native resolver/build/authentication settings. Current
managed versions are lower bounds, including when an override selects a version.
Runtime pins and included-group pins remain consumer-owned. Named-index
credentials continue to use uv's environment/keyring configuration; they are not
converted into requirements or printed by the updater. Relative local index
URLs and `find-links` remain relative to the consumer project.

Pin discovery supports registry requirements and named-index sources with
optional platform markers. Absolute local wheel/sdist source files are also
retained for isolated artifact evaluation; their fixed pins are not upgrade
targets. Relative paths, source directories, workspace, Git, direct URL, group/extra-scoped
sources, scoped override tables, dynamic dependencies, legacy uv dev dependencies
and conflicting-group policies are rejected before writes. Use explicit uv
maintenance for those forms; the updater never falls back to PyPI to bypass them.

Every retained lock package, including transitive dependencies and inactive
platform branches, is checked for decreases before synchronization. Changed
marker partitions reject when an upgrade cannot be proved; intentional downgrades
or branch restructuring need separate maintainer review through uv. All selected
Python manifests and locks roll back on candidate, apply, lock or sync failure.
If synchronization had started, the updater restores existing environments from
their original locks and removes newly created environments. Recovery failures
remain failures with actionable diagnostics and retained file backups where
needed. This is caught-failure recovery, not crash atomicity or concurrent-writer
coordination; avoid simultaneous edits or environment use during updates.

Declare additional independent uv projects when a tool needs its own environment:

```toml
[tool.research-repo-tools.deps]
python-projects = ["tooling/scanner"]
```

The shared `deps update-python --all-projects` command upgrades each declared
project's lock, validates all locks, then synchronizes each project's own `.venv`.
Additional projects keep their manifest requirements, including exact tool pins,
and use their own Python version and default dependency groups. Give each one a
compatible `.python-version`; uv workspace members share their workspace lock
and must not be listed as independent projects. No directories are discovered
automatically. To adopt this workflow in an existing repository, upgrade its
shared-package pin, merge the packaged `update-python-dependencies` recipe, and
declare its extra projects. Installing the package does not rewrite copied recipes.

Semgrep's separate Python runtime is provisioned by the shared package. When
adopting this release, replace nested `tooling/semgrep` launches with direct
wrapper calls, remove the redundant Semgrep dependency/project declarations
and lock only if that project has no other consumers, and update lockfile audit
lists accordingly. Consumer recipes can remain thin:

```just
semgrep-check:
    uv run --locked --group dev research-repo-tools semgrep check-fixtures --rust-docs

semgrep-scan:
    uv run --locked --group dev research-repo-tools semgrep scan --include '*.rs' --include '*.md' --rust-docs

validate-native configuration *names:
    uv run --locked --no-active --managed-python --project . --group dev research-repo-tools toolchain run -- research-repo-tools validation run "$@"
```

Other independent tools can continue to use explicitly declared Python projects.
After **v0.2.4 is published and verified**, adopt its exact registry pin, reconcile
copied recipes and audit lock selection, and run focused consumer integration
checks before removing local source-index or audit workarounds. These changes in
the repository do not establish that the release is already available on PyPI.

For Rust projects, pin `cargo-edit` to an exact version in the managed Cargo tool
table (for example, `cargo-edit = "0.13.13"`) and keep the compiler pinned in
`rust-toolchain.toml`, then run `just setup` to install and verify the declared tool.
For both `cargo upgrade` and direct `cargo-upgrade` invocations, `toolchain run`
rejects missing or non-exact `cargo-edit` declarations even when an unmanaged copy
is on PATH. The default Cargo recipe upgrades
requirements with incompatible releases allowed, then updates `Cargo.lock`.
It skips Cargo when the root has no `Cargo.toml`, keeping Python-only consumers
supported. Additional resolution roots and coupled dependency exclusions belong
in the consumer's `update-cargo-dependencies` recipe. When merging the template,
preserve those decisions; the aggregate calls that recipe by name. For example:

```just
update-cargo-dependencies:
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- \
        cargo upgrade --incompatible allow --exclude coupled-a --exclude coupled-b
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- \
        cargo upgrade --manifest-path "fixtures/extra root/Cargo.toml" --incompatible allow
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo update
    uv run --locked --only-group tooling --inexact research-repo-tools toolchain run -- cargo update --manifest-path "fixtures/extra root/Cargo.toml"
```

Exclusions apply to requirement upgrades; lock resolution still follows all
declared constraints. Keep the required order of these commands and retain
consumer checks for coupled dependencies and additional manifests. Review both
manifests and lockfiles after updating. See [update adoption](migration.md#dependency-and-tool-update-adoption)
for superseded policies and consumer integration requirements.

### User-installed tool updates

A machine-configuration repository can use the same dependency commands while
owning its user-wide Cargo installations. Keep the shared package's exact pin in
the `tooling` group, included by `dev`, and map only the Just pins the repository
owns:

```toml
[tool.research-repo-tools.deps.tools]
nextest_version = "cargo-nextest"
uv_version = "uv"
```

Merge these thin recipes into the repository's existing update workflow:

```just
# Validate the selected uv executable's stable X.Y.Z version output.
check-uv:
    uv run --locked --only-group tooling --inexact research-repo-tools deps check-uv

# Upgrade user-installed Cargo packages before reconciling the owned Just pins.
update-cargo-tools:
    cargo install-update --all --locked
    uv run --locked --only-group tooling --inexact research-repo-tools deps update-tools

# Advance main dev pins, then upgrade and sync all declared Python projects.
update-python-dependencies:
    uv run --locked --only-group tooling --inexact research-repo-tools deps update-python --all-projects
```

`cargo install-update` requires the separately installed `cargo-update` package.
Its `--all` scope covers the user's Cargo installations; `--locked` retains each
tool's published dependency resolution. A failed Cargo upgrade stops before pin
reconciliation. Successfully upgraded tools remain installed and can be reconciled
on retry. `deps update-tools` reads `cargo install --list` and the selected uv
version, validates every mapped assignment, and updates only those Just pins.
It does not install tools; `--dry-run` previews the pin changes.

`deps check-uv` validates stable version syntax; it does not install uv or assert
equality with a Just pin. Python pin updates preserve intentional ranges, markers,
extras, and included-group pins. A universal resolution requiring multiple
versions for one direct exact pin fails before changing the manifest or lock.
Even when no direct pin changes, the recipe still upgrades the full lock and
synchronizes `dev`.

Shell bootstrap, Homebrew, stow, and machine-wide update ordering remain consumer
policy. The standard research-project template continues to use isolated managed
Cargo installations through `toolchain upgrade`.

#### Mixed installation owners on Linux and macOS

Declare Homebrew ownership explicitly for mapped user tools. Tools without an
owner entry retain the verified Cargo-inventory contract; a working PATH binary
does not establish ownership. The selected executable must resolve into the
formula prefix and agree with Homebrew's installed inventory before reconciliation:

```toml
[tool.research-repo-tools.deps.tools]
nextest_version = "cargo-nextest"
format_version = "dprint"

[tool.research-repo-tools.deps.tool-owners]
cargo-nextest = "cargo"
dprint = "homebrew"
```

For prebuilt dprint and rumdl, migrate their pins to the managed release-binary
table and remove the corresponding Cargo declaration or Just variable. The
managed store verifies upstream release URLs, SHA-256 digests, and executable
versions before publishing pins. Just remains supplied by the shared package's
exact `rust-just` dependency; remove competing Just pins when adopting this model.

```toml
[tool.research-repo-tools.toolchain.binaries]
dprint = "0.60.1"
rumdl = "0.2.78"
```

Both tools support x86_64/aarch64 macOS and glibc Linux, plus x86_64 Windows;
dprint also supports Windows ARM64. Missing release assets or checksums fail.
Use the explicit Cargo declaration for rumdl on Windows ARM64.

Keep a machine's Homebrew/Brewfile policy conditional in its own recipe. This
example composes the portable stages without invoking shell setup:

```just
update: update-platform update-tools update-dependencies

update-platform:
    if [ "$(uname -s)" = Darwin ]; then brew update && brew upgrade && brew bundle; else echo "Skipped Homebrew/Brewfile stage: macOS policy only"; fi

update-tools: update-uv update-cargo-tools

update-uv *args:
    uv run --no-config --no-sync --no-python-downloads research-repo-tools deps update-uv "$@"
```

Linux needs no Homebrew. Retain the existing dependency recipes, Cargo exclusions,
and additional manifests. User-wide Cargo upgrades remain an explicit consumer
choice. Updating never installs Homebrew or invokes shell/bootstrap setup.
`just update-uv --dry-run` identifies the selected installation owner and operation
and previews project-pin reconciliation without upgrading or writing. The native
owner determines the available upgrade within its retained constraints;
`toolchain upgrade --dry-run` previews exact managed Cargo/prebuilt targets, and
`deps update-tools --dry-run` previews verified user-tool pin reconciliation.
Unknown ownership and missing tools fail. After a package-manager failure,
completed installations remain; dependent recipes stop. Repair the reported cause
and retry. There is no transaction across managers.

## Actions allowlists and opt-in pin updates

The consumer's committed GitHub selected-actions payload is the sole allowlist.
Use the strict payload with `github_owned_allowed: false`, `verified_allowed:
false`, and `patterns_allowed` containing exact identities with either `@*` or
`@<40-hex-SHA>` ref constraints. For example:

```json
{
  "github_owned_allowed": false,
  "verified_allowed": false,
  "patterns_allowed": [
    "actions/checkout@*",
    "example/publish-auth@0123456789abcdef0123456789abcdef01234567"
  ]
}
```

Wildcards in identities, owner-wide exemptions, duplicate JSON fields, and other
policy shapes fail. Tags, branches, short SHAs, and other ref patterns are not
supported as policy constraints. Repository identity matching ignores case;
nested paths match exactly, including case. A repository entry does not also
approve its subpaths or reusable workflow paths. Full SHA hex is compared without
case distinctions. Exact-SHA entries reject any other commit, branch, or tag;
`@*` entries keep accepting any supported ref for their exact identity. Entries
are alternatives: an `@*` entry for the same identity also permits refs beyond
an exact-SHA entry. Retain only the reviewed commits for a restricted identity.

Merge the packaged `workflow-allowlist-check` recipe, pointing it at that existing
policy, then add it to the consumer's check/CI gate:

```just
workflow-allowlist-check:
    uv run --locked --group dev research-repo-tools actions allowlist --policy .github/settings/allowed-actions.json .github/workflows
```

```sh
just workflow-allowlist-check
```

The checker reads explicit files or YAML directories and inspects
`jobs.<id>.uses` calls, workflow steps, and `runs.steps[*].uses` in composite
action metadata (`runs.using: composite`). To include a consumer's composite
actions, append the existing `.github/actions` directory (or explicit metadata
files) to the inputs in both the allowlist and update recipes. Inputs must be
workflows or composite metadata; JavaScript/Docker action metadata and documents
mixing workflow `jobs` with action `runs` fail. Quoted/folded scalars and aliases
are supported; alias findings point to the anchor. Duplicate/merge mappings and
recursive aliases fail with file/line diagnostics. Local `./` actions,
same-repository `$/` references, and `docker://` containers are outside this external policy. Keep actionlint for
workflow schema and zizmor for full-SHA pinning/security; no repository settings
are changed. Consumers can delete their generic YAML allowlist checker after
pinning a published version with this capability and validating their own policy.

Action updates require separate, explicit opt-in targets in
`.github/action-updates.toml` (also available as the `action-updates.toml` template):

```toml
[actions]
"actions/checkout" = "latest"
"owner/repo/subpath" = "v2.1.0"
```

`latest` selects GitHub's published stable release and permits major upgrades.
An explicit stable version tag restricts the target. GitHub CLI (`gh`) is required
for read-only release/tag lookup, including private repositories. Annotated tags
are peeled to full commit SHAs. Existing selected references must already use
full SHAs and ordinary version comments, such as `# v1.2.3`; the updater retains
subpaths, quote style, trailing comment text, other source bytes, and line endings
in both workflows and composites. Selected references must be ordinary single-line
scalars; folded references remain supported for allowlist checks only.
Unsupported scalar forms, aliases when rewriting files, missing targets, or lookup
failures abort before publication. References outside the selected identities
remain unchanged and appear as skipped in the report.

Pin updates do not modify the selected-actions JSON. When an identity has an
exact-SHA approval, review and update that approval deliberately alongside an
authorized pin change, then run the allowlist gate. A mismatch reports the
permitted commits without suggesting a wildcard exemption.

Merge the packaged `update-actions` recipe and review the report before applying:

```sh
just update-actions --dry-run
just update-actions --check
just update-actions
```

Preview/check reports include action identity, location, old/new full SHAs, and
old/new version comments. `--check` returns nonzero if updates are needed;
`--dry-run` returns zero for a valid preview. Unchanged targets are no-ops.
To include this stage in deliberate maintenance, opt in by changing the aggregate
to `update: update-tools update-dependencies update-actions`. Actions compatibility
then checks the final tool and Python dependency pins. Keep Dependabot's
scheduled PR review policy in the consumer; this command neither enables nor
disables it. Review overlapping proposals before merging.

Prefer the shared direct zizmor scanner, with one scanner pin. A retained
`zizmorcore/zizmor-action` fails maintenance unless its upstream support inventory
is explicitly checked, including when that wrapper is outside the update targets:

```toml
[compatibility."zizmorcore/zizmor-action"]
path = "support/versions"
tool = "zizmor"
```

The tool version comes from its existing exact Cargo/prebuilt, Python-group, or
mapped Just declaration. Conflicting authorities fail. The inventory at the
candidate or retained SHA must contain that version in the first column of a
plain version list (optional opaque columns, comments/blank lines and `latest`
are allowed; duplicate or unsupported entries fail). This avoids an embedded compatibility
table. Other wrappers may opt into the same contract. This detects absent version
support, not every possible runtime incompatibility; retain focused consumer CI.
