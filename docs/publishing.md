# Publishing to PyPI

PyPI is the distribution source for consumers. GitHub hosts source, issues,
review, and CI. Consumers pin the published package in their development
dependencies and lock it with uv; sibling checkouts and local wheels are for
development only. A public GitHub repository is not required by PyPI.

**Preparation status (2026-09-16):** `0.1.0` is being prepared in
[issue #1](https://github.com/acgetchell/research-repo-tools/issues/1).
The workflow and instructions are present; PyPI ownership, the Trusted Publisher,
the protected GitHub environment, and the first upload still need verification
or setup. Building or merging this preparation does not publish a package.

## Release workflow

[publish.yml](../.github/workflows/publish.yml) runs on pushed `v*` tags in
`acgetchell/research-repo-tools`:

1. Require the tagged commit to be an ancestor of the protected `main` branch.
2. Require a canonical stable `vX.Y.Z` tag matching `project.version`, synchronized
   metadata, and dated, nonempty release notes using the shared package utilities.
3. Audit locked Python dependencies, then call the package-check workflow at the
   same source commit. It builds a wheel and sdist once with `uv build --no-sources`.
4. Run tests and static checks on Linux, macOS, and Windows, installing those same
   distributions outside the checkout. Run the git-cliff integration job too.
5. Wait for approval of the protected `pypi` environment. Download the validated
   immutable artifact by its ID from that workflow run and publish it with OIDC
   credentials and attestations. The publishing job does not rebuild the package.

Only the publishing job receives `id-token: write`; it has no source checkout or
package installation step. No PyPI API token is stored in GitHub. The standalone
publishing job follows the [PyPA action's Trusted Publishing guidance](https://github.com/pypa/gh-action-pypi-publish#trusted-publishing).
The reusable CI workflow itself has no publishing credentials.

Required PR check names remain unchanged. The platform checks explicitly fail
if their upstream build fails, so a skipped dependency cannot satisfy them.
Artifacts expire after seven days; complete release approval within that window.

## One-time setup, before the first tag

Complete these account settings after the preparation PR passes CodeRabbit and
required CI and is merged. Repository files describe the intended configuration;
they do not apply it automatically.

1. Reconcile the selected-Actions policy using
   [GitHub setup](github.md#apply-or-reconcile-github-settings). The desired
   `allowed-actions.json` adds `pypa/gh-action-pypi-publish@*`; full SHA pinning
   remains required. The workflow uses GitHub's exact-commit
   [self-repository syntax](https://github.blog/changelog/2026-07-30-reference-same-repository-actions-with-self-repository-syntax/)
   for its reusable CI call. The pinned actionlint version needs the narrowly
   documented exception in `.github/actionlint.yaml` until it supports that syntax.
2. In GitHub **Settings → Environments**, create `pypi`. Require approval by
   `acgetchell`, disable administrator bypass of environment protection, and
   select deployment tags matching `v*`, with no branch deployment rule.
   For a sole maintainer, allow self-review so the person pushing the tag can
   approve the deployment. Adding a second reviewer permits disabling self-review.
   Creating the name in YAML alone does not configure these protections.
3. Sign in to the intended PyPI owner account, verify its email and two-factor
   authentication, and check availability/ownership of `research-repo-tools`.
   If it does not exist, use **Publishing → Add a new pending publisher** with:

   | Field | Value |
   | --- | --- |
   | PyPI project | `research-repo-tools` |
   | GitHub owner | `acgetchell` |
   | Repository | `research-repo-tools` |
   | Workflow filename | `publish.yml` |
   | Environment | `pypi` |

   If the project already belongs to this account, add the same publisher in its
   Publishing settings. If someone else owns the name, resolve that before tagging.
   A [pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
   creates the project on the first successful upload; it does not reserve the name.

## Prepare the release PR

The supported surface is documented in [Supported interfaces](api.md).
`pyproject.toml` already declares `0.1.0`; runtime and CLI versions are derived
from its installed distribution metadata. Do not introduce another version constant.

After committing substantive changes, generate release notes from their history
using this package's implementation and git-cliff:

```sh
uv sync --locked
uv run --locked research-repo-tools changelog generate --tag v0.1.0 --date YYYY-MM-DD --dry-run
uv run --locked research-repo-tools changelog generate --tag v0.1.0 --date YYYY-MM-DD
uv run --locked just release-check v0.1.0
uv run --locked just ci
uv run --locked --group audit just audit
```

Replace `YYYY-MM-DD` with the intended UTC release date. Review and commit the
generated changelog; never hand-write its entries. An earlier generated draft
cannot include later or uncommitted changes. Refresh it after the implementation
commits and whenever the release date changes. `just ci` builds local artifacts
and installs them; `just check-dist` validates existing artifacts without rebuilding.

Submit the release changes through a PR. Resolve CodeRabbit findings and require
passing native CI before merging. Keep issue #1 open until publication and the
clean-consumer check below also succeed. Git operations are performed by the
maintainer under [AGENTS.md](../AGENTS.md).

For later releases, first run the shared `release update X.Y.Z` command with the
actual previous release and chosen date; see [Shared release behavior](release.md).
The first release needs no invented previous version.

## Publish after review and account setup

Use a clean checkout of the reviewed `main` commit with final generated notes.
The maintainer previews the shared tag operation, then creates and pushes it:

```sh
uv run --locked just release-check v0.1.0
uv run --locked research-repo-tools changelog tag v0.1.0 --dry-run
uv run --locked research-repo-tools changelog tag v0.1.0
git push origin v0.1.0
```

The shared tag command requires the heading's date to equal today's UTC date.
If it differs, regenerate and review the updated notes through a PR before
tagging. Pushing the tag starts release validation; approve the `pypi` deployment
only after checking its commit, version, and successful validation jobs.

If a run fails before uploading, fix the cause and retry after review. A failed
publishing job can reuse its existing validated artifact within the retention
window. If either file reached PyPI, inspect the project and run logs before
retrying: uploaded filenames cannot be replaced, and the workflow deliberately
does not skip existing files. A content correction needs a new version. Never
move a published release tag. See [PyPI's Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
for publisher-identity failures.

## Verify installation and adopt in consumers

After PyPI lists both distributions, run from a new directory outside this checkout:

```sh
uv init --bare --python 3.14
uv add --dev --index-url https://pypi.org/simple "research-repo-tools==0.1.0"
uv run --locked research-repo-tools --version
uv run --locked research-repo-tools --help
uv run --locked research-repo-tools templates cliff.toml --owner example --repository consumer
uv run --locked python - <<'PY'
from research_repo_tools import __version__
from research_repo_tools.cli import main

print(__version__)
raise SystemExit(main(["templates", "CHANGELOG.md"]))
PY
```

Confirm `uv.lock` resolves the package from PyPI and the commands work without a
source checkout, local wheel, or editable/path source. Also inspect the release's
file hashes and attestations on PyPI. Record the tag/run, published version, and
clean-install result in issue #1 before closing it.

In an existing consumer, use `uv add --dev "research-repo-tools==0.1.0"`, remove
any temporary local source override, and commit its manifest and lockfile after
validation. Thin just recipes or Python wrappers call the [supported API](api.md).
For upgrades, choose the next published version explicitly with the same command,
review its changelog, and run the consumer's own checks before merging.
