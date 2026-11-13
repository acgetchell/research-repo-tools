"""Presentation of completed verifier results; no selection or validation effects."""

from collections.abc import Mapping
from typing import Any

__all__ = ["render_distribution_summary", "render_release_summary"]


def render_release_summary(
    selection: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any] | None = None,
    acquisition_owner: str | None = None,
    cargo_reproduced: bool = False,
    published: bool = False,
) -> str:
    """Render only after the existing verifier and requested handoff writes succeed.

    This renderer does not authenticate arbitrary JSON. Callers must supply the
    just-returned verified selection, never treat rendering as verification.
    """
    policy, run, package = selection["policy"], selection["run"], selection["package"]
    guarantee = policy["guarantee"]
    descriptions = {
        "signed-source": "required source/job checks; package-byte equivalence is not established",
        "cargo-archive": "retained Cargo archive identity and required package checks; registry bytes are not established",
        "python-distributions": "exact retained wheel/sdist bytes and metadata from required validation jobs",
    }
    lines = [
        f"Release evidence verified: {package['name']} v{package['version']}",
        f"Commit: {run['head_sha']}",
        f"Native CI: passed; run {run['id']}, attempt {run['run_attempt']} ({run.get('name', run['path'])})",
        f"Producer: {policy['repository']} {policy['event']} {policy['ref']}",
        f"Package evidence: passed ({guarantee}); {descriptions[guarantee]}",
    ]
    if guarantee == "cargo-archive":
        lines.append("Cargo reproduction: passed; exact archive and tools matched" if cargo_reproduced else "Cargo reproduction: not requested")
    if guarantee == "python-distributions" and policy.get("distributions", {}).get("index_url", "legacy") is None:
        lines.append("Registry destination: undecided; not publishable without separate explicit approval")
    if "freshness" in selection:
        lines.append("Freshness: passed; selected run age and artifact retention checked")
    if published:
        lines.append("Published distributions: passed; registry bytes match retained evidence")
    if candidate is None:
        lines.append("Benchmark evidence: not configured for this verification")
    else:
        original = candidate["binding"]["candidate"]
        lines.extend(
            [
                f"Benchmark evidence: passed; acquisition {candidate['identity']}",
                f"Original measured source: {original['revision']} ({original['mode']}, dirty={str(original['dirty']).lower()})",
            ]
        )
        if acquisition_owner is not None:
            lines.append(f"Acquisition owner: {acquisition_owner}; original measured provenance retained")
        publication = candidate["binding"].get("publication")
        if publication is not None:
            lines.append(f"Committed generated evidence: passed; {len(publication['outputs'])} exact publisher outputs; history preserved")
        else:
            lines.append("Committed generated evidence: not configured")
    lines.append("Signature: passed; permitted producer and run/attempt verified" if policy["signing_job"] is not None else "Signature: not configured")
    artifact = selection["artifact"]
    lines.append(f"Artifact integrity: passed; immutable artifact {artifact['id']}, {artifact['digest']}")
    return "\n".join(lines) + "\n"


def render_distribution_summary(result: Mapping[str, Any]) -> str:
    """Adapt the maintainer distribution verifier's unchanged inspection result."""
    return render_release_summary(
        {
            "package": {"name": "research-repo-tools", "version": result["tag"].removeprefix("v")},
            "policy": {
                "guarantee": "python-distributions",
                "repository": "acgetchell/research-repo-tools",
                "event": "push",
                "ref": "refs/heads/main",
                "signing_job": "release-evidence",
            },
            "run": result["run"],
            "artifact": result["artifact"],
        }
    )
