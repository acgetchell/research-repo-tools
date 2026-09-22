# Migrating consumer release policies

MCMC's coordinated extraction removes generic release discovery, candidate
staging, CLI-output capture, and publication wrappers. A published version
containing the structured release API lets a consumer remove that orchestration.
Pin that release before deleting the old helpers. The following is a small worked
fixture using MCMC's fixed concept DOI and representative release references; file
selection and counts must match the consumer's actual active documents.

## Declarative assertions and selected edits

This configuration requires the publication files, asserts the DOI independently
on each surface, updates one source link, and updates one explicit performance
command pair. It leaves measured report links and archived evidence unchanged.
No repository name or DOI is a package default.

```toml
[tool.research-repo-tools.release]
required-files = [
    "Cargo.lock", "pyproject.toml", "uv.lock", "CITATION.cff",
    "CHANGELOG.md", "README.md", "REFERENCES.md",
]
exclude = ["docs/evidence/**"]

[[tool.research-repo-tools.release.rules]]
path = "CITATION.cff"
pattern = '''^doi:[ \t]*['"]?(?P<value>[^'"\s]+)['"]?[ \t]*(?:#.*)?\r?$'''
value = "10.5281/zenodo.20033111"

[[tool.research-repo-tools.release.rules]]
path = "README.md"
pattern = '\[!\[DOI\]\([^)]*\)\]\(https://doi\.org/(?P<value>[^)]+)\)'
value = "10.5281/zenodo.20033111"

[[tool.research-repo-tools.release.rules]]
path = "REFERENCES.md"
pattern = '^- DOI: <https://doi\.org/(?P<value>[^>]+)>[ \t]*\r?$'
value = "10.5281/zenodo.20033111"

[[tool.research-repo-tools.release.rules]]
path = "README.md"
pattern = 'https://(?:github\.com/example/consumer/(?:blob|raw|tree)/|raw\.githubusercontent\.com/example/consumer/)(?P<value>main|[0-9a-f]{7,40}|v[0-9]+\.[0-9]+\.[0-9]+)/[^\s)\]>"?#]+'
source = "tag"
count = 1
exclude = '/docs/(?:PERFORMANCE\.md(?:$|[?#])|archive/performance/)'

[[tool.research-repo-tools.release.rules]]
path = "docs/RELEASING.md"
pattern = 'just performance-release[ \t]+(?P<value>v[0-9]+\.[0-9]+\.[0-9]+)[ \t]+v[0-9]+\.[0-9]+\.[0-9]+'
source = "tag"

[[tool.research-repo-tools.release.rules]]
path = "docs/RELEASING.md"
pattern = 'just performance-release[ \t]+v[0-9]+\.[0-9]+\.[0-9]+[ \t]+(?P<value>v[0-9]+\.[0-9]+\.[0-9]+)'
source = "previous-tag"
```

Replace `example/consumer` and `count` with the reviewed repository URL and link
inventory. Add a separate selector per active command document. Omit command-pair
rules where the consumer now documents inferred arguments or placeholders.
A missing example fails an explicit rule; optional regex branches do not bypass
cardinality. Changes to the active document inventory should update configuration
and its focused integration tests together.

Fixed DOI values are checked before edits and are never repaired by a version
bump. The same mismatch on all three surfaces still fails. Mutable stable versions
must already identify the prior or target release, while an explicitly matched
`main` or commit hash can be promoted. Historical release links remain pinned to
the measured release. The default archive exclusions still apply; `exclude` adds
consumer-selected evidence directories to the automatic Markdown exclusions.

## Thin consumer wrapper

For the MCMC migration, use `tag-policy="canonical-stable"`, explicit previous
release/date arguments, fixed DOI rules, and version-independent example commands.
Ordinary checks stay offline. A non-package Python environment (`tool.uv.package=false`)
beside Cargo is not a second releasable package and its environment version is not
synchronized. No callback is needed merely to advance an example baseline.

With only these declarative rules, use the existing shared release recipes and
delete both generic wrappers. If current evidence needs extra interpretation,
retain a small adapter. This example shows a synthetic evidence index whose
`release` field tracks the prepared version while its `measured` field stays
pinned. A real consumer supplies its own evidence parser and acceptance rules.

```python
import json
from pathlib import Path

from research_repo_tools.releases import (
    ReleaseAdapter, ReleaseContext, apply_release, check_release, plan_release,
)


def evidence_edits(candidate: Path, context: ReleaseContext) -> dict[str, bytes]:
    path = "evidence.json"
    evidence = json.loads((candidate / path).read_bytes())
    evidence["release"] = context.version
    return {path: (json.dumps(evidence, indent=2) + "\n").encode("utf-8")}


def evidence_problems(candidate: Path, context: ReleaseContext) -> tuple[str, ...]:
    evidence = json.loads((candidate / "evidence.json").read_bytes())
    if evidence.get("release") != context.version:
        return ("evidence index does not identify the prepared release",)
    if not evidence.get("measured"):
        return ("select current performance evidence before preparing a release",)
    return ()


adapter = ReleaseAdapter(
    input_files=("evidence.json",),
    prepare=evidence_edits,
    validate=evidence_problems,
)
root = Path(__file__).resolve().parents[1]
plan = plan_release(
    root, "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-20",
    adapter=adapter,
)
for edit in plan.edits:
    print(edit.path)  # Exact before/after bytes are available for a diff.
# A wrapper's dry-run branch returns here, after full candidate validation.
result = apply_release(plan)
```

The checker calls `check_release(root, adapter=adapter, previous_tag="v1.2.3")`
and inspects `result.ok` and `result.problems`. Add `final_changelog=True` through
the configured policy, or `--final-release` for the CLI, after generating the
current changelog heading. The updater's adapter can also return a prepared
changelog if that belongs in its complete candidate. Planning without final
validation permits the preceding heading, preserving the existing release order.

Both callbacks read a selected temporary tree. They do not create their own
staging tree, invoke the shared CLI, discover versions, or publish files.
The shared implementation validates the contributed edits and publishes every
changed file in one transaction. If custom validation fails, nothing is
published; if replacement fails, the shared rollback contract applies.

## Adoption boundary

Keep consumer tests for actual DOI declarations, required surfaces, selected
links and command counts, evidence interpretation, and the installed package pin.
The shared suite owns generic missing/ambiguous input, preview parity, failed
candidate validation, stale plans, exact-byte preservation, and transactional
rollback tests. It runs against both installed distribution formats on the
package's native platform jobs.

Do not remove benchmark generation, evidence selection, scientific validation,
or publication workflows. Validate the installed release with the real consumer
configuration before deleting the duplicate helpers and their exclusive tests.
Package publication and downstream adoption are separate changes.
