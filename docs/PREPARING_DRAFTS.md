# Preparing an unpublished release draft

Draft preparation and authenticated receipt retrieval are available from v0.2.7.
Enable them only after verifying the published supplying version and its installed
consumer integration. See
[release availability](../README.md#release-availability); source availability
does not establish that the example pin is published.

The [public contract](release-evidence-api.md#draft-preparation) composes retained
signed evidence with existing captured release targets. Consumer policy owns
package identity, required checks, benchmark selection, registry and triggers.
Preparation reads inert records and creates only an unpublished stable draft.

## Read-only verification and an isolated writer

The following consumer workflow example has a source-observing read-only job
and a writer with no checkout. Adapt the evidence policy and protected workflow
identity through normal review. Use an exact verified supplying package pin in
both jobs. The job containing write credentials must run trusted workflow code;
do not load a writer script, executable module, uv project or dependency lock
from the handoff or tagged checkout.

The read-only job independently peels the local tag, requires main ancestry,
observes the clean source/package, verifies the retained selection, and generates
notes using the installed runtime. The handoff contains only policy, selection
and note bytes. The writer authenticates its immutable artifact ID and original
ZIP digest against trusted job outputs, then binds its provider run and
attempt-specific name before extraction. The native evidence selection inside
that handoff has its own exact main-push run/attempt, signer and artifact, which
the preparation API revalidates live.

```yaml
name: Prepare consumer draft
on:
  push:
    tags: ["v*"]
permissions:
  contents: read
  actions: read
concurrency:
  group: draft-${{ github.repository }}-${{ github.ref }}
  cancel-in-progress: false
jobs:
  verify:
    runs-on: ubuntu-24.04
    outputs:
      commit: ${{ steps.source.outputs.commit }}
      artifact: ${{ steps.handoff.outputs.artifact-id }}
      digest: ${{ steps.handoff.outputs.artifact-digest }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3
        with:
          version: "0.13.7"
          enable-cache: false
      - name: Assert source and verify original signed evidence
        id: source
        env:
          GH_TOKEN: ${{ github.token }}
          RELEASE_TAG: ${{ github.ref_name }}
        shell: bash
        run: |
          set -euo pipefail
          commit=$(git --no-pager rev-parse "$GITHUB_REF^{commit}")
          git --no-pager merge-base --is-ancestor "$commit" origin/main
          mkdir "$RUNNER_TEMP/handoff"
          cp tooling/release-evidence.toml "$RUNNER_TEMP/handoff/evidence.toml"
          uv run --no-config --no-project --isolated --no-cache --python 3.15 \
            --with research-repo-tools==0.2.7 python -I -c \
            'import sys; from research_repo_tools.cli import main; sys.exit(main())' \
            --root "$GITHUB_WORKSPACE" release evidence verify \
            "$RUNNER_TEMP/handoff/evidence.toml" --checkout "$GITHUB_WORKSPACE" \
            --commit "$commit" --version "${RELEASE_TAG#v}" \
            --output "$RUNNER_TEMP/handoff/selection.json"
          uv run --no-config --no-project --isolated --no-cache --python 3.15 \
            --with research-repo-tools==0.2.7 python -I -c \
            'import sys; from research_repo_tools.cli import main; sys.exit(main())' \
            --root "$GITHUB_WORKSPACE" changelog notes "$RELEASE_TAG" \
            > "$RUNNER_TEMP/handoff/notes.md"
          printf 'commit=%s\n' "$commit" >> "$GITHUB_OUTPUT"
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        id: handoff
        with:
          name: draft-handoff-${{ github.run_id }}-${{ github.run_attempt }}
          path: ${{ runner.temp }}/handoff/*
          if-no-files-found: error
          retention-days: 30
  prepare:
    needs: verify
    runs-on: ubuntu-24.04
    permissions:
      actions: read
      contents: write
    steps:
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3
        with:
          version: "0.13.7"
          enable-cache: false
      - name: Authenticate inert handoff and prepare unpublished draft
        env:
          GH_TOKEN: ${{ github.token }}
          RELEASE_TAG: ${{ github.ref_name }}
          RELEASE_COMMIT: ${{ needs.verify.outputs.commit }}
          HANDOFF_ID: ${{ needs.verify.outputs.artifact }}
          HANDOFF_SHA256: ${{ needs.verify.outputs.digest }}
        shell: bash
        run: |
          set -euo pipefail
          # Reruns must recover the original journal; never use a fresh path.
          test "$GITHUB_RUN_ATTEMPT" = 1
          cd "$RUNNER_TEMP"
          uv run --no-config --no-project --isolated --no-cache --python 3.15 \
            --with research-repo-tools==0.2.7 python -I - <<'PY'
          import os
          from functools import partial
          from pathlib import Path
          from research_repo_tools.files import replace_many
          from research_repo_tools.release_assets import serialize_release_target
          from research_repo_tools.release_drafts import prepare_release_draft
          from research_repo_tools.release_interfaces import load_evidence_policy
          from research_repo_tools.release_validation import api, download_artifact, read_pages

          root = Path.cwd()
          repository = os.environ["GITHUB_REPOSITORY"]
          run = int(os.environ["GITHUB_RUN_ID"])
          attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
          identifier = int(os.environ["HANDOFF_ID"])
          artifact = api(f"actions/artifacts/{identifier}", repository=repository)
          producer = artifact.get("workflow_run", {})
          if not (
              type(artifact.get("id")) is int and artifact["id"] == identifier
              and artifact.get("expired") is False
              and artifact.get("name") == f"draft-handoff-{run}-{attempt}"
              and artifact.get("digest") == "sha256:" + os.environ["HANDOFF_SHA256"]
              and type(producer.get("id")) is int and producer["id"] == run
              and producer.get("head_sha") == os.environ["GITHUB_SHA"]
          ):
              raise ValueError("handoff artifact/run/digest identity differs")
          current = api(f"actions/runs/{run}", repository=repository)
          workflow = current.get("workflow_id")
          if (type(current.get("id")) is not int or current["id"] != run
              or type(workflow) is not int or workflow <= 0
              or type(current.get("run_attempt")) is not int
              or current["run_attempt"] != attempt
              or attempt != 1
              or current.get("event") != "push"
              or current.get("head_branch") != os.environ["RELEASE_TAG"]
              or os.environ["GITHUB_REF"] != "refs/tags/" + os.environ["RELEASE_TAG"]
              or current.get("head_sha") != os.environ["GITHUB_SHA"]):
              raise ValueError("handoff producer attempt or event changed")
          # A new run is not a fresh intent for a previously prepared tag.
          # Unfiltered pagination avoids the filtered workflow-run result cap.
          history = read_pages(partial(api, repository=repository),
                               f"actions/workflows/{workflow}/runs", "workflow_runs")
          same_tag = []
          seen = set()
          for previous in history:
              if (type(previous.get("id")) is not int or previous["id"] <= 0
                  or type(previous.get("workflow_id")) is not int
                  or previous["workflow_id"] != workflow
                  or not isinstance(previous.get("event"), str)
                  or not isinstance(previous.get("head_branch"), str)):
                  raise ValueError("workflow history identity is incomplete")
              if previous["id"] in seen:
                  raise ValueError("workflow history contains duplicate runs")
              seen.add(previous["id"])
              if previous["event"] == "push" and previous["head_branch"] == os.environ["RELEASE_TAG"]:
                  same_tag.append(previous["id"])
          if same_tag != [run]:
              raise ValueError("tag has prior or unproven preparation intent; recover the original journal")
          handoff = root / "handoff"
          download_artifact(repository, artifact, handoff,
                            ("evidence.toml", "selection.json", "notes.md"))
          latest = api(f"actions/runs/{run}", repository=repository)
          if any(type(latest.get(key)) is not type(current.get(key)) or latest.get(key) != current.get(key)
                 for key in ("id", "workflow_id", "run_attempt", "event", "head_branch", "head_sha")):
              raise ValueError("handoff producer changed during download")
          target = prepare_release_draft(
              root, repository, os.environ["RELEASE_TAG"],
              os.environ["RELEASE_COMMIT"],
              policy=load_evidence_policy(handoff / "evidence.toml"),
              expected=(handoff / "selection.json").read_bytes(),
              title=os.environ["RELEASE_TAG"],
              notes=(handoff / "notes.md").read_bytes(),
              receipt=root / "draft-receipt.json", approve=True,
          )
          replace_many({root / "target.json": serialize_release_target(target)})
          PY
      - name: Retain journal even after an unconfirmed creation
        if: always()
        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: draft-receipt-${{ github.run_id }}-${{ github.run_attempt }}
          path: |
            ${{ runner.temp }}/draft-receipt.json
            ${{ runner.temp }}/target.json
          if-no-files-found: ignore
          retention-days: 30
```

Keep the read-only handoff's producer run/attempt and artifact ID/digest with the
preparation journal. The writer's final receipt artifact is a different handoff:
record its own immutable ID/digest and producer run/attempt for recovery and
attachment. A digest carried inside the same unauthenticated ZIP is insufficient.
The automatic retrieval command below can authenticate and freeze that original
selection. Explicit original IDs remain available for deliberate manual recovery.
Both routes verify the original ZIP digest and provider association before
extracting the expected inert files. Never select an artifact by latest name or
silently substitute a later attempt.

The example blocks automatic Actions reruns and new runs for tags already in
this workflow's retained history. Keep that workflow identity and history intact
for every tag that could be retriggered. GitHub history is not a permanent intent
ledger: deletion, retention expiry, a replacement workflow or another writer can
remove or bypass this guard. Consumers that allow those operations must use a
durable repository/tag intent ledger and require reconciliation whenever its
history is unavailable, before authorizing a fresh receipt.

A recovery workflow
must retrieve the original handoff and journal before calling preparation again;
it cannot regenerate notes, reselect evidence or start with an empty receipt.
It needs the same repository/tag serialization group as every other writer.
Upload-on-failure cannot guarantee durability after hard runner termination or
artifact upload failure. If the journal was not retained, stop automation and
reconcile provider/audit state manually before any further creation. Do not
treat a new workflow run or a missing receipt as permission to replace a draft.

## Retrieve original preparation output

The v0.2.7 `release assets retrieve` command supplies the authenticated captured
target needed by consumer Step 2. It does not run preparation, look up another
draft by tag, build packages, acquire measurements or authorize publication.
Keep this separate policy in `tooling/draft-receipt.toml`, with the actual
repository and workflow filename. For the workflow above:

```toml
schema = 1
repository = "OWNER/REPO"
workflow = ".github/workflows/prepare-draft.yml"
workflow-name = "Prepare consumer draft"
artifact-prefix = "draft-receipt"

[[jobs]]
name = "verify"
steps = ["Assert source and verify original signed evidence"]
runner-labels = ["ubuntu-24.04"]

[[jobs]]
name = "prepare"
steps = ["Authenticate inert handoff and prepare unpublished draft", "Retain journal even after an unconfirmed creation"]
runner-labels = ["ubuntu-24.04"]
```

Observe the full source commit independently, peeling an annotated tag to its
commit. Then run the consumer's existing `release-assets` wrapper:

```sh
just release-assets retrieve tooling/draft-receipt.toml \
  --tag v1.2.3 --commit FULL_OBSERVED_COMMIT \
  --output ../release-handoffs/v1.2.3
```

The destination must be outside the consumer root. First retrieval requires one
unambiguous retained push run for the tag, successful original attempt 1 and all
declared jobs/steps. It authenticates the immutable artifact's exact ID, provider
SHA-256 and two-file inventory, then stores `selection.json`, `target.json` and
`draft-receipt.json` together. The target records the original release ID; feed
that file to the existing `release assets verify` / `upload --target` commands.
Consumers still own acquisition selection, stage/publish wiring and approval.

Repeating the same command automatically revalidates `selection.json` and its
original run/attempt/artifact ID/digest. It never discovers a replacement after
capture. Missing, expired, failed, changed or incomplete records stop. Preserve
exclusive control of the output directory; local records are assertions, not
offline authorization tokens. The asset APIs still revalidate the release itself
immediately before their effects.

For a deliberately reviewed original run, `--run-id ORIGINAL_ID` bypasses automatic
history selection while retaining every source, workflow, original-attempt, job
and artifact check. It cannot replace a captured selection. To restore the same
selection into a new directory, pass `--expected /trusted/selection.json`.
Neither option permits a later attempt or unauthenticated receipt bytes.
Failed preparation with only a pending journal uses the explicit recovery flow
below; successful automatic retrieval requires both captured receipt files.

GitHub run metadata reports a short `head_branch` for both branch and tag pushes.
Retrieval therefore verifies the workflow bytes at the independently asserted
commit and requires a push trigger containing only `tags` or `tags-ignore`, with
no `branches` or `branches-ignore`. A workflow supporting both namespaces is not
accepted, even when a branch and tag happen to point at the same commit.
[GitHub's trigger rules](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onpushbranchestagsbranches-ignoretags-ignore)
define this distinction. The existing main-push evidence policy remains separate.

See the [Python retrieval contract](release-evidence-api.md#tag-push-receipt-retrieval)
for the typed selection modes and frozen identity fields.

## Retry and recovery

Preserve the original receipt on both success and failure. After a confirmed
creation, retry against its captured release ID. Missing, replaced, published,
prerelease, immutable, renamed or edited drafts reject. Existing assets are
never changed. Preparation cannot repair drift by editing or deleting a release.

An ambiguous POST outcome retains a pending journal. Inspect the actual release
and provider/audit history independently. If the reviewed release ID matches the
original assertions, rerun the same CLI command with
`--recover-release-id REVIEWED_ID` and the original receipt, evidence and notes.
Recovery performs live validation and captures that ID without a POST. If no
release exists, leave the pending intent unresolved; this API intentionally
provides no automatic reset or replacement. A maintainer must reconcile the
operation outside automation. Captured IDs cannot be replaced through recovery.

Non-cancelling serialization limits cooperative races; it cannot supply a GitHub
transaction. Concurrent external writers and tag changes after the last GET can
still race creation. In particular, GitHub may create a missing tag at
`target_commitish` if it disappears between the final GET and POST. Rechecks
detect observable drift and preserve the journal;
they cannot undo a release or guarantee an atomic remote snapshot.

## La-stack migration map

After the verified supplying package is pinned and installed integration passes,
replace the consumer's manual temporary-notes/`gh release create` block
with the tag-triggered read-only verification and no-checkout preparation above.
Retain these consumer-owned steps and boundaries:

| Existing responsibility | Resulting flow |
| --- | --- |
| Pre-tag `release-evidence` | Keep the exact final-main signed selection; preparation revalidates it without builds or a new test matrix |
| Generated release notes | Generate with read-only credentials; freeze original bytes in the authenticated handoff |
| Manual draft creation | Replace with `release assets prepare`; retain receipt and captured `ReleaseTarget` |
| Manually copied preparation run/artifact IDs | Use `release assets retrieve` once, then retain its frozen selection and captured target for every retry |
| Owner-selected `transfer.json` | Keep independent explicit selection and its original provenance; preparation never acquires measurements |
| Draft-first attachment | Attach only to the captured ID using the existing asset API and trusted target handoff |
| Immutable release assets | Preserve exact-byte retry checks and refuse conflicting uploads; never overwrite |
| Maintainer publication | Keep explicit review and publication approval after all attachments and gates |
| crates.io publication | Keep Cargo gates, credentials and approval-time source/evidence/tag revalidation in the consumer |

Signed source validation does not prove byte-identical Cargo archive promotion.
Scientific workloads, retained measurements, Cargo publication and account
configuration remain consumer-owned. Record the supplying package version and
installed integration results in the consumer's adoption issue; upstream source
validation alone does not establish downstream release readiness.
