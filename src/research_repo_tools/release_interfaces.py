"""Declarative installed release evidence and asset interfaces.

The evidence verifier owns trust decisions. Consumer workflows retain signing,
Cargo gates, approval environments and token acquisition.
"""

import argparse
import json
import os
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal, cast

lazy from research_repo_tools.ci import export_environment
lazy from research_repo_tools.config import Config
lazy from research_repo_tools.evidence import _string
lazy from research_repo_tools.files import _validate_distinct_paths, replace_many
lazy from research_repo_tools.measurement import resolve_revision
lazy from research_repo_tools.publication import _path
lazy from research_repo_tools.publication_config import _table
lazy from research_repo_tools.release_assets import (
    _repository,
    parse_release_target,
    preflight_release_target,
    publish_release_asset,
    require_release_checkout,
    revalidate_release_target,
    serialize_release_target,
)
lazy from research_repo_tools.release_evidence import EvidencePolicy, create_evidence_manifest, verify_release_evidence
lazy from research_repo_tools.release_metadata import read_package_info
lazy from research_repo_tools.release_validation import ValidationPolicy, api, parse_release_json, redact_release_diagnostic

__all__ = ["AssetPolicy", "load_asset_policy", "load_evidence_policy", "write_release_outputs"]


def _document(path: Path, required: set[str], optional: set[str]) -> dict[str, object]:
    raw = _table(tomllib.loads(path.read_bytes().decode("utf-8")), "release policy", required | {"schema"}, optional)
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("release policy schema must be integer 1")
    return raw


def _strings(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} requires a nonempty array")
    return tuple(_string(item, context) for item in value)


def load_evidence_policy(path: Path) -> EvidencePolicy:
    """Load exact repository/workflow, job/step/platform and signer requirements."""
    raw = _document(
        path, {"repository", "package", "workflow", "workflow-name", "artifact-prefix", "guarantee", "jobs"}, {"branch", "path-in-vcs", "signing-job"}
    )
    jobs = raw["jobs"]
    if not isinstance(jobs, list):
        raise ValueError("evidence jobs requires an array of tables")
    steps, labels = [], []
    for item in jobs:
        job = _table(item, "evidence job", {"name", "steps"}, {"runner-labels"})
        name = _string(job["name"], "job name")
        steps.append((name, _strings(job["steps"], "job steps")))
        if "runner-labels" in job:
            labels.append((name, _strings(job["runner-labels"], "runner labels")))
    signing = None
    if "signing-job" in raw:
        signer = _table(raw["signing-job"], "signing-job", {"name", "steps"})
        signing = (_string(signer["name"], "signing job"), _strings(signer["steps"], "signing steps"))
    return EvidencePolicy(
        ValidationPolicy(
            _string(raw["repository"], "repository"),
            _string(raw["workflow"], "workflow"),
            _string(raw["workflow-name"], "workflow-name"),
            tuple(steps),
            _string(raw.get("branch", "main"), "branch"),
            tuple(labels),
        ),
        _string(raw["package"], "package"),
        _string(raw["artifact-prefix"], "artifact-prefix"),
        cast(Literal["signed-source", "cargo-archive"], _string(raw["guarantee"], "guarantee")),
        signing,
        cast(str, raw.get("path-in-vcs", "")),
    )


@dataclass(frozen=True, slots=True)
class AssetPolicy:
    repository: str
    expected_title: str | None = None

    def __post_init__(self) -> None:
        _repository(self.repository)
        if self.expected_title is not None and self.expected_title != "tag":
            raise ValueError("asset expected-title must be 'tag' when configured")


def load_asset_policy(path: Path) -> AssetPolicy:
    """Load standard exact-byte retry policy and optional tag-as-title assertion."""
    raw = _document(path, {"repository"}, {"expected-title"})
    return AssetPolicy(_string(raw["repository"], "repository"), _string(raw["expected-title"], "expected-title") if "expected-title" in raw else None)


def _regular(path: Path) -> None:
    if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)) or (path.exists() and not path.is_file()):
        raise ValueError(f"release handoff must be a regular file without links: {path}")


def write_release_outputs(
    destination: Path, payload: bytes, *, github_output: Path | None = None, values: Mapping[str, str] | None = None, verify: Callable[[], None] | None = None
) -> None:
    """Transactionally retain immutable handoff bytes and optional Actions output.

    The Actions command file must already exist. Its prior bytes and the
    handoff survive caught failures. Use a new handoff filename for a changed
    selection. Exclusive writer control is required; files are not crash-atomic.
    """
    _regular(destination)
    original = destination.read_bytes() if destination.exists() else None
    if original is not None and original != payload:
        raise ValueError("release handoff already contains different evidence; select a new output path")
    originals = {destination: original}
    outputs = {destination: payload}
    if github_output is not None:
        _validate_distinct_paths((destination, github_output))
        _regular(github_output)
        before = github_output.read_bytes()
        with github_output.open("rb+"):
            pass  # Require the existing command file itself to be writable.
        if before and not before.endswith(b"\n"):
            raise ValueError("existing Actions output must end with a newline")
        if not values:
            raise ValueError("Actions output requires verified identity values")
        with tempfile.TemporaryDirectory(prefix="research-release-output-") as temporary:
            command_file = Path(temporary) / "output"
            command_file.write_bytes(before)
            export_environment(command_file, tuple(values), environment=values)
            outputs[github_output] = command_file.read_bytes()
        originals[github_output] = before

    def unchanged() -> None:
        for path, prior in originals.items():
            _regular(path)
            if (path.read_bytes() if path.exists() else None) != prior:
                raise ValueError("release output changed while preparing handoff")
        if verify is not None:
            verify()

    replace_many(outputs, verify=unchanged)


def _checkout(checkout: Path, package: str, version: str, commit: str, *, path_in_vcs: str = "") -> None:
    if Path(__file__).resolve().is_relative_to(checkout.resolve()):
        raise ValueError("release verification requires a trusted installed runtime outside the observed checkout")
    require_release_checkout(checkout, commit)
    observed = read_package_info(checkout / path_in_vcs)
    if (observed.name, observed.version) != (package, version):
        raise ValueError("observed checkout package/version differs from release assertions")


def add_commands(release) -> None:
    """Install the sorted asset subcommands before release check."""
    assets = release.add_parser("assets", help="capture/revalidate release targets and upload verified inert assets").add_subparsers(
        dest="operation", required=True
    )
    for operation in ("preflight", "upload", "verify"):
        command = assets.add_parser(operation)
        command.add_argument("configuration")
        command.add_argument("--checkout", type=Path, required=True, help="observed clean source checkout, separate from trusted installed runtime")
        command.add_argument("--commit", required=True, help="independent full release commit")
        command.add_argument("--tag", required=True)
        if operation == "preflight":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--github-output", type=Path)
        else:
            command.add_argument("--target", type=Path, required=True, help="trusted captured preflight JSON; never recaptured during upload")
        if operation == "upload":
            command.add_argument("--asset", type=Path, required=True)
            command.add_argument("--approve", action="store_true", required=True)
            command.add_argument("--publish", action="store_true", help="publish draft only after verified attachment and final identity checks")


def add_evidence_commands(release) -> None:
    commands = release.add_parser("evidence", help="create or verify exact-commit release evidence and file-based approval handoffs").add_subparsers(
        dest="operation", required=True
    )
    for operation in ("create", "verify"):
        command = commands.add_parser(operation)
        command.add_argument("configuration")
        command.add_argument("--checkout", type=Path, required=True)
        command.add_argument("--commit", required=True)
        command.add_argument("--version", required=True, help="independent stable package version without v")
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--github-output", type=Path)
        command.add_argument("--tools", type=Path, help="JSON object containing independently observed cargo and rustc producer identities")
        if operation == "create":
            command.add_argument("--archive", type=Path)
            command.add_argument("--attempt", type=int, required=True)
            command.add_argument("--run-id", type=int, required=True)
        else:
            command.add_argument("--expected", type=Path, help="frozen approval selection to revalidate before acquiring credentials")
            command.add_argument("--reproduced-archive", type=Path)


def run_evidence(args: argparse.Namespace, settings: Config) -> None:
    path = _path(settings.root, args.configuration)
    original = path.read_bytes()
    policy = load_evidence_policy(path)
    checkout = args.checkout.resolve(strict=True)
    inputs = [
        path,
        *([args.tools] if args.tools else []),
        *([args.expected] if getattr(args, "expected", None) else []),
        *([args.archive] if getattr(args, "archive", None) else []),
        *([args.reproduced_archive] if getattr(args, "reproduced_archive", None) else []),
    ]
    _validate_distinct_paths((args.output, *([args.github_output] if args.github_output else []), *inputs))
    read = partial(api, repository=policy.validation.repository)

    def observe() -> None:
        if path.read_bytes() != original:
            raise ValueError("release evidence policy changed")
        _checkout(checkout, policy.package, args.version, args.commit, path_in_vcs=policy.path_in_vcs)

    observe()
    tools = parse_release_json(args.tools.read_bytes()) if args.tools else None
    if args.operation == "create":
        expected = {
            "GITHUB_REPOSITORY": policy.validation.repository,
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": f"refs/heads/{policy.validation.branch}",
            "GITHUB_SHA": args.commit,
            "GITHUB_RUN_ID": str(args.run_id),
            "GITHUB_RUN_ATTEMPT": str(args.attempt),
        }
        if any(os.environ.get(key) != value for key, value in expected.items()):
            raise ValueError("evidence creation requires the exact executing trusted push/run/attempt")
        payload = create_evidence_manifest(policy, args.version, args.commit, args.run_id, args.attempt, read, archive=args.archive, tools=tools)
    else:
        frozen = args.expected.read_bytes() if args.expected else None
        payload = verify_release_evidence(
            policy, args.version, args.commit, read, expected=frozen, reproduced_archive=args.reproduced_archive, reproduced_tools=tools
        )
    observe()
    values = {"identity": json.dumps(parse_release_json(payload), sort_keys=True, separators=(",", ":")), "commit": args.commit}
    write_release_outputs(args.output, payload, github_output=args.github_output, values=values, verify=observe)
    print("Saved verified release evidence.")


def run_assets(args: argparse.Namespace, settings: Config) -> None:
    policy = load_asset_policy(_path(settings.root, args.configuration))
    checkout = args.checkout.resolve(strict=True)
    if Path(__file__).resolve().is_relative_to(checkout):
        raise ValueError("release assets require a trusted installed runtime outside the observed checkout")
    if resolve_revision(checkout, args.tag) != args.commit:
        raise ValueError("local release tag differs from the independent commit assertion")
    require_release_checkout(checkout, args.commit)
    title = args.tag if policy.expected_title == "tag" else None
    if args.operation == "preflight":
        _validate_distinct_paths((args.output, _path(settings.root, args.configuration), *([args.github_output] if args.github_output else [])))
        target = preflight_release_target(settings.root, policy.repository, args.tag, args.commit, expected_title=title, checkout=checkout)

        def verify() -> None:
            revalidate_release_target(settings.root, target, checkout=checkout)

        write_release_outputs(
            args.output,
            serialize_release_target(target),
            github_output=args.github_output,
            values={"commit": target.commit, "tag": target.tag, "release_id": str(target.release_id)},
            verify=verify,
        )
        print("Saved verified release target.")
    else:
        target = parse_release_target(args.target.read_bytes())
        if (target.repository, target.tag, target.commit, target.expected_title) != (policy.repository, args.tag, args.commit, title):
            raise ValueError("release assertions/policy differ from captured target")
        revalidate_release_target(settings.root, target, checkout=checkout)
        if args.operation == "upload":
            publish_release_asset(settings.root, policy.repository, args.tag, args.asset, target=target, checkout=checkout, publish=args.publish)
        print("Verified release target and requested asset operation.")


def run(args: argparse.Namespace, settings: Config) -> int:
    try:
        (run_evidence if args.action == "evidence" else run_assets)(args, settings)
    except Exception as error:
        raise ValueError(redact_release_diagnostic(str(error))) from error
    return 0
