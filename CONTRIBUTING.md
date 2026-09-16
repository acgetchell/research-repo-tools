# Contributing

Use Python 3.14+, uv, Ruff, ty, pytest, and just. The project manifest and lockfile
own the environment.

```sh
uv sync --locked
uv run --locked just check
```

Use `just check` while iterating, with targeted regressions when a behavioral
change needs verification. Run `just ci` once the work is ready for final review.
It runs checks and tests, builds wheel and sdist artifacts, and installs both
outside the checkout with uv. Installation checks exercise the console entry
point, imports, packaged templates, runtime dependencies, and the bundled `just` executable.

`just check` also runs the locked actionlint and zizmor workflow validators.
`just audit` performs the separate network-backed Python dependency audit.
GitHub repository settings and required checks are documented in
[GitHub setup](docs/github.md); their API payloads live in `.github/settings/`.

Maintain one implementation per common capability under `src/research_repo_tools/`.
Organize tests under `tests/changelog`, `dependencies`, `releases`, `semgrep`, and
`utilities`. Fixtures should be small representative inputs generated in temporary
directories. Preserve meaningful regression assertions against this package;
merge duplicates instead of maintaining historical implementations or repository
snapshots. Use actual child processes for byte transport and minimal disposable
repositories for Git behavior.

A new capability needs a shared purpose and a consistent contract. Repository
names must never select behavior. Standardize common defaults; leave scientific
algorithms, datasets, notebook content, benchmark cases, and custom workflows in
consumers. Do not add compatibility flags merely to retain old script differences.

Normal CLI failures should identify the relevant input without a traceback.
Imports must have no network or consumer-file side effects. Changes involving
multiple files must validate candidates before replacement and preserve originals
on caught failures; incomplete rollback must report recovery files.

Follow [AGENTS.md](AGENTS.md): agents must not run mutating Git commands in this
or consumer repositories. Leave staging, commits, tags, pushes, and branch changes
to the user. Tests exercise Git mutations only in disposable fixtures. Source
repositories remain read-only. Package publication requires an explicit request;
there is no publishing workflow here.

Record current validation, its limitations, and source attribution in `docs/`.
Keep one top-level LICENSE.
