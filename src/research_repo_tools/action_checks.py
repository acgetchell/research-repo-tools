"""Check declared Actions pins for consistency without network access or writes."""

from pathlib import Path

from research_repo_tools.action_policy import TAG, load_policy
from research_repo_tools.workflow_uses import SHA, Use, references, version_comment, workflow_paths


def check(root: Path, policy: Path, inputs: list[str]) -> int:
    declaration = load_policy(policy)
    if not declaration.consistency:
        raise ValueError(f"{policy}: actions check requires a nonempty consistency.actions list")
    scope = ", ".join(str(root / value) for value in inputs)
    selected = set(declaration.consistency)
    present: set[str] = set()
    pins: dict[str, tuple[str, str, Use]] = {}
    diagnostics: list[str] = []
    # An empty directory still needs an identity-and-scope diagnostic.
    try:
        paths = workflow_paths(root, inputs)
    except ValueError as error:
        raise ValueError(f"{error}; checking {', '.join(sorted(selected))} in configured scope: {scope}") from error
    for path in paths:
        text = path.read_bytes().decode("utf-8-sig")
        try:
            uses = references(path, text)
        except ValueError as error:
            diagnostics.append(str(error))
            continue
        for use in uses:
            if use.action is None or use.action not in selected:
                continue
            present.add(use.action)
            if use.ref is None or SHA.fullmatch(use.ref) is None:
                diagnostics.append(f"{use.location}: {use.action}: expected a full commit SHA; found {use.ref!r}")
                continue
            try:
                tag = version_comment(use, text)[2]
                if TAG.fullmatch(tag) is None:
                    raise ValueError(f"{use.location}: {use.action}: invalid stable version comment {tag!r}")
            except ValueError as error:
                diagnostics.append(str(error))
                continue
            target = declaration.targets.get(use.action)
            if target is not None and target != "latest" and target != tag:
                diagnostics.append(f"{use.location}: {use.action}: version comment {tag!r} does not match explicit policy tag {target!r} in {policy}")
            sha = use.ref.lower()
            if use.action in pins:
                expected_sha, expected_tag, first = pins[use.action]
                if (sha, tag) != (expected_sha, expected_tag):
                    diagnostics.append(
                        f"{use.location}: inconsistent {use.action}@{sha} # {tag}; {first.location} has {use.action}@{expected_sha} # {expected_tag}"
                    )
            else:
                pins[use.action] = sha, tag, use
    for action in sorted(selected - present):
        diagnostics.append(f"{policy}: declared identity {action!r} is absent from configured scope: {scope}")
    if diagnostics:
        raise ValueError("\n".join(diagnostics))
    print(f"Actions pin consistency passed: {len(selected)} identities in {len(paths)} files; offline, no files changed.")
    return 0
