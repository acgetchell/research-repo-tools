"""Declarative installed release evidence and asset interfaces.

The evidence verifier owns trust decisions. Consumer workflows retain signing,
Cargo gates, approval environments and token acquisition.
"""

import argparse
import json
import os
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal, cast

from packaging.utils import canonicalize_name

from research_repo_tools._console import console_text, write_stdout
from research_repo_tools.process import format_exception_diagnostics
lazy from research_repo_tools.ci import export_environment
lazy from research_repo_tools.config import Config
lazy from research_repo_tools.evidence import _string
lazy from research_repo_tools.files import _validate_distinct_paths, replace_many
lazy from research_repo_tools.measurement import resolve_revision
lazy from research_repo_tools.publication import _path
lazy from research_repo_tools.publication_config import _table
lazy from research_repo_tools.registry import verify_published_distributions
lazy from research_repo_tools.release_assets import (
    _read_target,
    _repository,
    parse_release_target,
    preflight_release_target,
    publish_release_asset,
    require_release_checkout,
    revalidate_release_target,
    serialize_release_target,
)
lazy from research_repo_tools.release_evidence import EvidencePolicy, PythonDistributionPolicy, create_evidence_manifest, verify_release_evidence
lazy from research_repo_tools.release_metadata import read_package_info
lazy from research_repo_tools.release_receipts import TagPushPolicy
lazy from research_repo_tools.release_validation import ValidationPolicy, api, parse_release_json, redact_release_diagnostic

__all__ = ["AssetPolicy", "load_asset_policy", "load_evidence_policy", "load_receipt_policy", "write_release_outputs"]


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
        path,
        {"repository", "package", "workflow", "workflow-name", "artifact-prefix", "guarantee", "jobs"},
        {"branch", "distributions", "package-kind", "path-in-vcs", "signing-job"},
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
    distributions = None
    if "distributions" in raw:
        options = _table(raw["distributions"], "distributions", {"files"}, {"destination", "index-url", "max-age-seconds", "max-distribution-bytes"})
        if "destination" in options and (options["destination"] != "undecided" or "index-url" in options):
            raise ValueError("distribution destination must be 'undecided' without index-url, or an explicit index-url")
        distributions = PythonDistributionPolicy(
            _strings(options["files"], "distribution files"),
            None if "destination" in options else _string(options.get("index-url", "https://pypi.org"), "PyPI index URL"),
            cast(int, options.get("max-age-seconds", 30 * 24 * 60 * 60)),
            cast(int, options.get("max-distribution-bytes", 256 * 1024 * 1024)),
        )
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
        cast(Literal["signed-source", "cargo-archive", "python-distributions"], _string(raw["guarantee"], "guarantee")),
        signing,
        cast(str, raw.get("path-in-vcs", "")),
        cast(Literal["auto", "cargo", "python"], raw.get("package-kind", "auto")),
        distributions,
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


def load_receipt_policy(path: Path) -> "TagPushPolicy":
    """Load a separate original tag-push receipt policy, never a branch policy."""
    from research_repo_tools.release_receipts import TagPushPolicy

    raw = _document(path, {"repository", "workflow", "workflow-name", "jobs"}, {"artifact-prefix"})
    if not isinstance(raw["jobs"], list):
        raise ValueError("receipt jobs requires an array of tables")
    steps, labels = [], []
    for item in raw["jobs"]:
        job = _table(item, "receipt job", {"name", "steps"}, {"runner-labels"})
        name = _string(job["name"], "job name")
        steps.append((name, _strings(job["steps"], "job steps")))
        if "runner-labels" in job:
            labels.append((name, _strings(job["runner-labels"], "runner labels")))
    return TagPushPolicy(
        _string(raw["repository"], "repository"),
        _string(raw["workflow"], "workflow"),
        _string(raw["workflow-name"], "workflow-name"),
        tuple(steps),
        _string(raw.get("artifact-prefix", "draft-receipt"), "artifact prefix"),
        tuple(labels),
    )


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


def _checkout(
    checkout: Path, package: str, version: str, commit: str, *, path_in_vcs: str = "", package_kind: Literal["auto", "cargo", "python"] = "auto"
) -> None:
    if Path(__file__).resolve().is_relative_to(checkout.resolve()):
        raise ValueError("release verification requires a trusted installed runtime outside the observed checkout")
    require_release_checkout(checkout, commit)
    observed = read_package_info(checkout / path_in_vcs, kind=package_kind)
    name = canonicalize_name(observed.name) if package_kind == "python" and observed.name is not None else observed.name
    expected = canonicalize_name(package) if package_kind == "python" else package
    if (name, observed.version) != (expected, version):
        raise ValueError("observed checkout package/version differs from release assertions")


def add_commands(release) -> None:
    """Install the sorted asset subcommands before release check."""
    assets = release.add_parser("assets", help="prepare drafts, capture/revalidate targets and upload verified inert assets").add_subparsers(
        dest="operation", required=True
    )
    for operation in ("plan", "preflight", "prepare", "prepare-authenticated", "retrieve", "stage", "upload", "verify"):
        command = assets.add_parser(operation)
        command.add_argument("configuration")
        if operation == "plan":
            command.description = "Verify a tag-push source and freeze its signed evidence and generated notes; no release mutation."
            command.add_argument("--commit", required=True)
            command.add_argument("--event", choices=("push",), required=True)
            command.add_argument("--github-output", type=Path)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--repository", required=True)
            command.add_argument("--tag", required=True)
            continue
        if operation == "prepare-authenticated":
            command.description = "Authenticate the original handoff and prepare its draft outside a checkout; preserve the complete state directory."
            command.add_argument("--approve", action="store_true", required=True)
            command.add_argument("--artifact-id", type=int, required=True)
            command.add_argument("--artifact-sha256", required=True)
            command.add_argument("--attempt", type=int, required=True)
            command.add_argument("--commit", required=True)
            command.add_argument("--executing-attempt", type=int, required=True)
            command.add_argument("--executing-run-id", type=int, required=True)
            command.add_argument("--output", type=Path, required=True, help="durable original state directory; restore it intact for recovery")
            command.add_argument("--recover-release-id", type=int)
            command.add_argument("--repository", required=True)
            command.add_argument("--run-id", type=int, required=True)
            command.add_argument("--tag", required=True)
            continue
        if operation == "stage":
            command.description = "Stage a frozen batch on one captured draft; repeat the same command and journal to recover matching assets."
            command.add_argument("--approve", action="store_true", required=True)
            command.add_argument("--asset", type=Path, action="append", required=True, help="original inert file; repeat for every batch member")
            command.add_argument("--checkout", type=Path, help="optional clean source checkout, separate from the trusted writer")
            command.add_argument("--commit", required=True)
            command.add_argument("--journal", type=Path, required=True, help="durable per-asset progress; retain on interruption")
            command.add_argument("--output", type=Path, required=True, help="complete immutable transfer, written only after final verification")
            command.add_argument("--progress", choices=("human", "json", "none"), default="human")
            command.add_argument("--tag", required=True)
            command.add_argument("--target", type=Path, required=True)
            continue
        if operation == "retrieve":
            command.description = "Retrieve the original authenticated tag-push receipt; existing outputs freeze every retry. No release mutation."
            command.add_argument("--commit", required=True, help="independent full peeled release commit")
            selection = command.add_mutually_exclusive_group()
            selection.add_argument("--expected", type=Path, help="original frozen selection, for restoration into a new output directory")
            command.add_argument("--output", type=Path, required=True, help="immutable receipt directory outside the consumer root")
            selection.add_argument("--run-id", type=int, help="independently reviewed original run ID for deliberate manual selection")
            command.add_argument("--tag", required=True, help="canonical stable tag; always interpreted as refs/tags/vX.Y.Z")
            continue
        if operation == "prepare":
            command.description = "Prepare an unpublished draft from frozen signed evidence without a checkout; preserve the receipt across every retry."
            command.add_argument("--approve", action="store_true", required=True, help="explicitly authorize draft creation")
            command.add_argument("--commit", required=True, help="independent full release commit")
            command.add_argument("--expected", type=Path, required=True, help="original authenticated evidence selection; never reselect")
            command.add_argument("--expected-title", required=True)
            command.add_argument("--notes", type=Path, required=True, help="generated UTF-8 note bytes from the read-only job")
            command.add_argument("--output", type=Path, required=True, help="captured release-target JSON for later attachment")
            command.add_argument("--receipt", type=Path, required=True, help="durable preparation journal; retain even on failure")
            command.add_argument("--recover-release-id", type=int, help="explicitly reviewed ID resolving an original pending receipt, without creation")
            command.add_argument("--repository", required=True, help="independent owner/name assertion")
            command.add_argument("--tag", required=True)
            continue
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
    _add_python_approval_command(commands, "approve-python")
    archive = commands.add_parser("archive", help="derive or verify the exact archive frozen by candidate acceptance").add_subparsers(
        dest="archive_operation", required=True
    )
    attach = archive.add_parser("attach", help="attach an accepted archive to the independently captured draft")
    attach.add_argument("--acceptance", type=Path, required=True)
    attach.add_argument("--approve", action="store_true", required=True)
    attach.add_argument("--archive", type=Path, required=True)
    attach.add_argument("--checkout", type=Path, help="optional independently observed final source checkout")
    attach.add_argument("--output", type=Path, required=True, help="immutable attachment identity receipt")
    attach.add_argument("--policy", type=Path, required=True)
    attach.add_argument("--target", type=Path, required=True)
    derive = archive.add_parser("derive", help="derive the accepted archive from retained evidence without measuring")
    derive.add_argument("--acceptance", required=True, help="frozen acceptance path relative to --root")
    derive.add_argument("--manifest", required=True, help="original evidence envelope path relative to --root")
    derive.add_argument("--output", required=True, help="archive destination relative to --root")
    derive.add_argument("--payload", required=True, help="original run payload path relative to --root")
    derive.add_argument("--policy", required=True, help="final candidate policy path relative to --root")
    inspect = archive.add_parser("inspect", help="inspect an accepted candidate or a historical exact-tag benchmark archive")
    inspect.add_argument("--acceptance", type=Path, help="original acceptance for a candidate archive")
    inspect.add_argument("--archive", type=Path, required=True)
    inspect.add_argument("--legacy-configuration", type=Path, help="explicit format declaration for a legacy baseline archive")
    inspect.add_argument("--policy", type=Path, help="final policy for a candidate archive")
    inspect.add_argument("--tag", help="expected tag for a historical baseline archive")
    verify = archive.add_parser("verify", help="verify transferred archive bytes against independent acceptance")
    verify.add_argument("--acceptance", type=Path, required=True, help="frozen acceptance from the trusted handoff")
    verify.add_argument("--archive", type=Path, required=True, help="transferred candidate archive")
    verify.add_argument("--policy", type=Path, required=True, help="final candidate policy from the trusted handoff")
    verify.add_argument("--target", type=Path, help="independently captured draft target")
    attached = archive.add_parser("verify-attachment", help="revalidate the original accepted draft attachment before approval")
    attached.add_argument("--acceptance", type=Path, required=True)
    attached.add_argument("--checkout", type=Path, help="optional independently observed final source checkout")
    attached.add_argument("--expected", type=Path, required=True, help="original trusted attachment receipt")
    attached.add_argument("--policy", type=Path, required=True)
    attached.add_argument("--target", type=Path, required=True)
    candidate = commands.add_parser("candidate", help="bind a prepared benchmark acquisition to final native evidence without measuring")
    candidate.add_argument("configuration", help="native release evidence policy")
    candidate.add_argument("--acceptance", type=Path, help="frozen Step 2 approval record to revalidate")
    candidate.add_argument("--acquisition-sha256", required=True, help="independent Step 1 assertion digest from the trusted acquisition owner")
    candidate.add_argument("--attachment", type=Path, help="original draft attachment receipt to revalidate with the acceptance")
    candidate.add_argument("--checkout", type=Path, required=True)
    candidate.add_argument("--commit", help="independent final commit; bind a pending owner policy and retain it with --policy-output")
    candidate.add_argument("--format", choices=("json", "status", "summary"), default="status")
    candidate.add_argument("--manifest", type=Path, required=True)
    candidate.add_argument("--native-output", type=Path, help="retain the frozen native selection with acceptance in one guarded update")
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--payload", type=Path, required=True)
    candidate.add_argument("--policy", type=Path, required=True, help="independently trusted acquisition assertions and final commit")
    candidate.add_argument("--policy-output", type=Path, help="retain the final policy with acceptance in one guarded update")
    candidate.add_argument("--progress", choices=("human", "json", "none"), default="none")
    candidate.add_argument("--publication-receipt", type=Path, help="original shared-publisher receipt captured before promotion")
    candidate.add_argument("--publication-sha256", help="independent Step 1 publication digest from the trusted owner")
    candidate.add_argument("--target", type=Path, help="independently captured draft target for attachment revalidation")
    for operation in ("create", "published", "published-python", "receive-acquisition", "verify", "verify-python-approval"):
        if operation in ("published-python", "verify-python-approval"):
            _add_python_approval_command(commands, operation)
            continue
        command = commands.add_parser(operation)
        if operation == "receive-acquisition":
            command.description = "Authenticate and retain one independently selected hosted benchmark acquisition."
            command.add_argument("--artifact-id", type=int, required=True)
            command.add_argument("--artifact-prefix", required=True)
            command.add_argument("--artifact-sha256", required=True)
            command.add_argument("--attempt", type=int, required=True)
            command.add_argument("--branch", required=True)
            command.add_argument("--commit", required=True)
            command.add_argument("--event", choices=("push", "workflow_dispatch"), required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--repository", required=True)
            command.add_argument("--run-id", type=int, required=True)
            command.add_argument("--workflow", required=True)
            continue
        command.add_argument("configuration")
        if operation == "create":
            command.add_argument("--archive", type=Path)
            command.add_argument("--attempt", type=int, required=True)
        command.add_argument("--checkout", type=Path, required=True)
        command.add_argument("--commit", required=True)
        command.add_argument("--distributions", type=Path, help="exact Python wheel/sdist directory; verify compares publication bytes with retained evidence")
        if operation != "create":
            command.add_argument(
                "--expected", type=Path, required=operation == "published", help="frozen approval selection to revalidate before acquiring credentials"
            )
        command.add_argument("--format", choices=("json", "status", "summary"), default="status", help="presentation only; handoff bytes stay unchanged")
        command.add_argument("--github-output", type=Path)
        command.add_argument("--output", type=Path, required=True)
        if operation != "create":
            command.add_argument("--reproduced-archive", type=Path)
        else:
            command.add_argument("--run-id", type=int, required=True)
        command.add_argument("--tools", type=Path, help="JSON object containing independently observed cargo and rustc producer identities")
        command.add_argument("--version", required=True, help="independent stable package version without v")


def run_evidence(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.dependency_provenance import check_release_dependencies

    if args.operation in ("approve-python", "published-python", "verify-python-approval"):
        _run_python_approval(args, settings)
        return
    if args.operation == "archive":
        _run_candidate_archive(args, settings)
        return
    if args.operation == "receive-acquisition":
        _receive_candidate_acquisition(args, settings)
        return
    if args.operation == "candidate":
        _run_candidate(args, settings)
        return
    check_release_dependencies(args.checkout, packages=settings.release.published_dependencies)
    path = _path(settings.root, args.configuration)
    original = path.read_bytes()
    policy = load_evidence_policy(path)
    if args.operation == "published" and policy.distributions is None:
        raise ValueError("published evidence verification requires Python distribution policy")
    if args.operation == "published" and policy.distributions is not None and policy.distributions.index_url is None:
        raise ValueError("Python evidence destination is undecided; use explicit publication approval and its registry verification command")
    checkout = args.checkout.resolve(strict=True)
    inputs = [
        path,
        *([args.tools] if args.tools else []),
        *([args.expected] if getattr(args, "expected", None) else []),
        *([args.archive] if getattr(args, "archive", None) else []),
        *([args.reproduced_archive] if getattr(args, "reproduced_archive", None) else []),
        *(
            [args.distributions / name for name in policy.distributions.filenames(policy.package, args.version)]
            if args.distributions and policy.distributions
            else []
        ),
    ]
    _validate_distinct_paths((args.output, *([args.github_output] if args.github_output else []), *inputs))
    read = partial(api, repository=policy.validation.repository)

    def observe() -> None:
        check_release_dependencies(checkout, packages=settings.release.published_dependencies)
        if path.read_bytes() != original:
            raise ValueError("release evidence policy changed")
        _checkout(checkout, policy.package, args.version, args.commit, path_in_vcs=policy.path_in_vcs, package_kind=policy.package_kind)

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
        payload = create_evidence_manifest(
            policy, args.version, args.commit, args.run_id, args.attempt, read, archive=args.archive, tools=tools, distributions=args.distributions
        )
    else:
        frozen = args.expected.read_bytes() if args.expected else None
        payload = verify_release_evidence(
            policy,
            args.version,
            args.commit,
            read,
            expected=frozen,
            reproduced_archive=args.reproduced_archive,
            reproduced_tools=tools,
            distributions=args.distributions,
        )
        if args.operation == "published":
            assert policy.distributions is not None
            assert policy.distributions.index_url is not None
            verify_published_distributions(policy.package, args.version, parse_release_json(payload)["files"], index_url=policy.distributions.index_url)
            # Network downloads may outlast gates/retention. Revalidate the same
            # selection once more before saving a published-byte receipt.
            verify_release_evidence(policy, args.version, args.commit, read, expected=payload, distributions=args.distributions)
    observe()
    values = {"identity": json.dumps(parse_release_json(payload), sort_keys=True, separators=(",", ":")), "commit": args.commit}
    write_release_outputs(args.output, payload, github_output=args.github_output, values=values, verify=observe)
    if args.operation == "create":
        if args.format == "json":
            write_stdout(payload)
        else:
            print("Saved release evidence manifest; verification remains required.")
    else:
        _present_evidence(payload, args.format, cargo_reproduced=args.reproduced_archive is not None, published=args.operation == "published")


def _present_evidence(
    payload: bytes, presentation: str, *, candidate: bool = False, acquisition_owner: str | None = None, cargo_reproduced: bool = False, published: bool = False
) -> None:
    if presentation == "json":
        write_stdout(payload)
    elif presentation == "summary":
        from research_repo_tools.release_summary import render_release_summary

        result = parse_release_json(payload)
        print(
            render_release_summary(
                result["native"] if candidate else result,
                candidate=result if candidate else None,
                acquisition_owner=acquisition_owner,
                cargo_reproduced=cargo_reproduced,
                published=published,
            ),
            end="",
        )
    else:
        print("Saved verified candidate and final release evidence." if candidate else "Saved verified release evidence.")
        record = parse_release_json(payload)
        policy = (record["native"] if candidate else record).get("policy", {})
        if policy.get("distributions", {}).get("index_url", "legacy") is None:
            print("Registry destination undecided; this evidence does not authorize publication.")


def _add_python_approval_command(commands, operation: str) -> None:
    command = commands.add_parser(operation, help="bind or revalidate explicit registry approval without builds, uploads or credentials")
    command.add_argument("configuration", help="original evidence policy")
    if operation == "approve-python":
        command.add_argument("--approve", action="store_true", required=True)
    else:
        command.add_argument("--approval", type=Path, required=True, help="trusted original approval record")
    command.add_argument("--authority", type=Path, required=True, help="independently approved registry endpoint/account/project/workflow policy")
    command.add_argument("--commit", required=True)
    command.add_argument("--distributions", type=Path, required=True)
    command.add_argument("--expected", type=Path, required=True, help="original frozen evidence selection")
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--version", required=True)


def _run_python_approval(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.python_publication import approve_python_publication, load_registry_authority, verify_python_publication

    configuration = _path(settings.root, args.configuration)
    original_configuration = _read_candidate_input(configuration)
    policy = load_evidence_policy(configuration)
    if policy.distributions is None:
        raise ValueError("Python approval requires Python distribution evidence")
    paths = (
        configuration,
        args.authority,
        args.expected,
        *([args.approval] if args.operation != "approve-python" else []),
    )
    _validate_distinct_paths((args.output, *paths, *(args.distributions / name for name in policy.distributions.filenames(policy.package, args.version))))
    inputs = {path: _read_candidate_input(path) for path in paths}
    if inputs[configuration] != original_configuration:
        raise ValueError("Python publication evidence policy changed while loading")
    authority = load_registry_authority(args.authority)
    read = partial(api, repository=policy.validation.repository)

    def unchanged() -> None:
        if any(_read_candidate_input(path) != data for path, data in inputs.items()):
            raise ValueError("Python publication inputs changed; preserve the original approval")

    unchanged()
    if args.operation == "approve-python":
        payload = approve_python_publication(
            policy,
            args.version,
            args.commit,
            read,
            selection=inputs[args.expected],
            authority=authority,
            distributions=args.distributions,
            approve=args.approve,
        )
    else:
        payload = inputs[args.approval]

    def verify() -> None:
        unchanged()
        verify_python_publication(
            policy,
            args.version,
            args.commit,
            read,
            selection=inputs[args.expected],
            authority=authority,
            approval=payload,
            distributions=args.distributions,
            published=args.operation == "published-python",
        )
        unchanged()

    if args.output.exists() and _read_candidate_input(args.output) != payload:
        raise ValueError("existing Python approval output differs; preserve the original authority and selection")
    write_release_outputs(args.output, payload, verify=verify)
    print("Verified original registry publication approval and exact distribution bytes; no credentials or upload requested.")


def _read_candidate_input(path: Path) -> bytes:
    from research_repo_tools.archives import ArchiveLimits

    _regular(path)
    limit = ArchiveLimits().content_bytes
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("candidate handoff exceeds content limit")
    return data


def _receive_candidate_acquisition(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.candidate_hosted import AcquisitionProducer, retrieve_candidate_handoff, serialize_acquisition_producer
    from research_repo_tools.evidence import deterministic_json
    from research_repo_tools.files import _directory_target

    producer = AcquisitionProducer(args.repository, args.workflow, args.event, args.branch, args.commit, args.run_id, args.attempt)
    destination = _directory_target(args.output)
    _validate_distinct_paths((settings.root, destination))
    selection = deterministic_json(
        {
            "schema": "research-repo-tools/acquisition-selection/v1",
            "producer": parse_release_json(serialize_acquisition_producer(producer)),
            "artifact_id": args.artifact_id,
            "artifact_prefix": args.artifact_prefix,
            "artifact_sha256": args.artifact_sha256,
        }
    )
    selection_path = destination / "selection.json"
    _regular(selection_path)
    if selection_path.exists() and _read_candidate_input(selection_path) != selection:
        raise ValueError("existing acquisition selection differs from the original producer or artifact")
    records = retrieve_candidate_handoff(
        producer,
        partial(api, repository=producer.repository),
        artifact_id=args.artifact_id,
        artifact_prefix=args.artifact_prefix,
        artifact_sha256=args.artifact_sha256,
    )
    updates = {destination / name: data for name, data in {**records, "selection.json": selection}.items()}

    def unchanged() -> None:
        if _directory_target(args.output) != destination:
            raise ValueError("acquisition handoff output directory changed")
        for path, data in updates.items():
            _regular(path)
            if path.exists() and _read_candidate_input(path) != data:
                raise ValueError(f"existing acquisition handoff differs: {path.name}")

    unchanged()
    replace_many(updates, verify=unchanged)
    print("Retained the original hosted acquisition and producer selection.")


def _run_candidate_archive(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.candidate_reuse import parse_candidate_policy, plan_candidate_archive, verify_candidate_archive
    from research_repo_tools.publication import publish_publication

    if args.archive_operation in {"attach", "verify-attachment"}:
        _run_candidate_attachment(args, settings)
        return
    if args.archive_operation == "inspect":
        _inspect_candidate_archive(args)
        return
    if args.archive_operation == "derive":
        plan = plan_candidate_archive(settings.root, args.payload, args.manifest, args.policy, args.acceptance, args.output)
        publish_publication(plan)
        print("Retained the archive frozen by candidate acceptance.")
        return
    _validate_distinct_paths((args.archive, args.policy, args.acceptance, *([args.target] if args.target else [])))
    _regular(args.archive)
    policy = parse_candidate_policy(_read_candidate_input(args.policy))
    target = parse_release_target(_read_candidate_input(args.target)) if args.target else None
    verify_candidate_archive(args.archive, policy, _read_candidate_input(args.acceptance), target=target)
    print("Verified the archive against the frozen candidate acceptance.")


def _inspect_candidate_archive(args: argparse.Namespace) -> None:
    from research_repo_tools.candidate_reuse import parse_candidate_policy, verify_candidate_archive
    from research_repo_tools.evidence import deterministic_json
    from research_repo_tools.release_assets import read_baseline

    _regular(args.archive)
    if args.policy is not None or args.acceptance is not None:
        if args.policy is None or args.acceptance is None or args.tag is not None or args.legacy_configuration is not None:
            raise ValueError("candidate inspection requires both policy and acceptance; historical inspection requires a tag")
        _validate_distinct_paths((args.archive, args.policy, args.acceptance))
        policy = parse_candidate_policy(_read_candidate_input(args.policy))
        evidence = verify_candidate_archive(args.archive, policy, _read_candidate_input(args.acceptance))
        result = {"format": "candidate", "tag": policy.tag, "run": policy.identity, "payload_schema": evidence.payload_schema}
    else:
        if args.tag is None:
            raise ValueError("historical archive inspection requires the expected release tag")
        configuration = _read_candidate_input(args.legacy_configuration) if args.legacy_configuration else None
        if args.legacy_configuration is not None:
            _validate_distinct_paths((args.archive, args.legacy_configuration))
        sample, source = read_baseline(args.archive, legacy_configuration=configuration, expected_tag=args.tag)
        result = {"format": "baseline", "tag": dict(source.context)["release"], "revision": source.revision, "benchmarks": len(sample.estimates)}
    write_stdout(deterministic_json(result))


def _run_candidate_attachment(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.candidate_attachment import attach_candidate_archive, verify_candidate_attachment
    from research_repo_tools.candidate_reuse import parse_candidate_policy, verify_candidate_archive

    paths = (args.policy, args.acceptance, args.target)
    attaching = args.archive_operation == "attach"
    _validate_distinct_paths((*paths, args.archive, args.output) if attaching else (*paths, args.expected))
    snapshots = {path: _read_candidate_input(path) for path in paths}
    policy = parse_candidate_policy(snapshots[args.policy])
    target = parse_release_target(snapshots[args.target])
    acceptance = snapshots[args.acceptance]
    checkout = args.checkout.resolve(strict=True) if args.checkout else None
    dependencies = settings.release.published_dependencies
    if checkout is not None and Path(__file__).resolve().is_relative_to(checkout):
        raise ValueError("candidate attachment requires a trusted installed runtime outside the observed checkout")
    if not attaching:
        verify_candidate_attachment(
            settings.root, policy, acceptance, target, _read_candidate_input(args.expected), checkout=checkout, dependencies=dependencies
        )
        print("Verified the original draft attachment.")
        return
    _regular(args.output)
    _regular(args.archive)
    if args.output.exists():
        receipt = _read_candidate_input(args.output)
        verify_candidate_archive(args.archive, policy, acceptance, target=target)
        verify_candidate_attachment(settings.root, policy, acceptance, target, receipt, checkout=checkout, dependencies=dependencies)
    else:
        receipt = attach_candidate_archive(
            settings.root, args.archive, policy, acceptance, target, approve=args.approve, checkout=checkout, dependencies=dependencies
        )

    def unchanged() -> None:
        if any(_read_candidate_input(path) != content for path, content in snapshots.items()):
            raise ValueError("candidate attachment assertions changed during verification")

    write_release_outputs(args.output, receipt, verify=unchanged)
    print(f"Verified candidate archive on draft {target.release_id}. Retain the attachment receipt for approval.")


def _run_candidate(args: argparse.Namespace, settings: Config) -> None:
    from dataclasses import replace

    from research_repo_tools._dependency_observation import DependencyOperation
    from research_repo_tools.candidate_reuse import _retain_candidate_release, parse_candidate_policy
    from research_repo_tools.evidence import parse_evidence

    checkout = args.checkout.resolve(strict=True)
    if (args.attachment is None) != (args.target is None) or (args.attachment is not None and args.acceptance is None):
        raise ValueError("approval revalidation requires the original acceptance, attachment receipt and independently captured target together")
    if args.commit is not None and args.policy_output is None:
        raise ValueError("final commit requires --policy-output")
    configuration = _path(settings.root, args.configuration)
    paths = (
        configuration,
        args.policy,
        args.payload,
        args.manifest,
        *([args.acceptance] if args.acceptance else []),
        *([args.attachment, args.target] if args.attachment else []),
        *([args.publication_receipt] if args.publication_receipt else []),
    )
    _validate_distinct_paths(
        (args.output, *paths, *([args.policy_output] if args.policy_output else []), *([args.native_output] if args.native_output else []))
    )
    # The command owns this entry observation before reading policy handoffs.
    # Only the private composition receives it; standalone APIs always check.
    entry = DependencyOperation(checkout, settings.release.published_dependencies)
    inputs = {path: _read_candidate_input(path) for path in paths}
    policy = parse_candidate_policy(inputs[args.policy])
    if args.commit is not None:
        if policy.commit not in {None, args.commit}:
            raise ValueError("final commit must not replace an existing different selection")
        policy = replace(policy, commit=args.commit)
    commit = policy.commit
    if commit is None:
        raise ValueError("candidate release requires an independently selected final commit")
    native = load_evidence_policy(configuration)

    def unchanged() -> None:
        for path, data in inputs.items():
            _regular(path)
            if path.read_bytes() != data:
                raise ValueError("candidate handoff changed during verification")
        _checkout(checkout, native.package, policy.tag.removeprefix("v"), commit, path_in_vcs=native.path_in_vcs, package_kind=native.package_kind)

    def verify_attachment(accepted: bytes) -> None:
        if args.attachment is not None:
            from research_repo_tools.candidate_attachment import verify_candidate_attachment

            verify_candidate_attachment(
                settings.root,
                policy,
                accepted,
                parse_release_target(inputs[args.target]),
                inputs[args.attachment],
                checkout=checkout,
                dependencies=settings.release.published_dependencies,
            )

    accepted = _retain_candidate_release(
        checkout,
        parse_evidence(inputs[args.payload], inputs[args.manifest]),
        policy,
        native,
        partial(api, repository=policy.repository),
        acquisition_sha256=args.acquisition_sha256,
        destination=args.output,
        policy_output=args.policy_output,
        native_output=args.native_output,
        verify_inputs=unchanged,
        verify_acceptance=verify_attachment,
        publication_receipt=inputs[args.publication_receipt] if args.publication_receipt else None,
        publication_sha256=args.publication_sha256,
        expected=inputs[args.acceptance] if args.acceptance else None,
        dependencies=settings.release.published_dependencies,
        entry=entry,
    )
    _present_evidence(accepted, args.format, candidate=True, acquisition_owner=policy.acquisition_owner)


def _retrieve_receipt(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.release_receipts import DiscoverOriginal, FrozenReceipt, OriginalRun, TagSource, retrieve_draft_receipt

    configuration = _path(settings.root, args.configuration)
    original_policy = configuration.read_bytes()
    policy = load_receipt_policy(configuration)
    source = TagSource(f"refs/tags/{args.tag}", args.commit)
    output = args.output if args.output.is_absolute() else settings.root / args.output
    if output.is_symlink() or output.is_junction():
        raise ValueError("receipt output must not be a link or junction")
    if Path(os.path.abspath(output)).is_relative_to(settings.root) or output.resolve().is_relative_to(settings.root.resolve()):
        raise ValueError("retain draft receipts outside the consumer checkout")
    output = output.resolve()
    names = {"selection.json", "target.json", "draft-receipt.json"}
    if output.exists() and (not output.is_dir() or {item.name for item in output.iterdir()} not in (set(), names)):
        raise ValueError("receipt output is incomplete or contains unrelated files; retain it and restore the original selection into a new directory")
    paths = {name: output / name for name in names}
    for path in paths.values():
        _regular(path)
    originals = {path: path.read_bytes() if path.exists() else None for path in paths.values()}
    expected = args.expected.resolve() if args.expected else None
    _validate_distinct_paths((configuration, *paths.values(), *([expected] if expected else [])))
    expected_bytes = expected.read_bytes() if expected else None
    retained = originals[paths["selection.json"]]
    if retained is not None and expected_bytes is not None and retained != expected_bytes:
        raise ValueError("explicit selection cannot replace the original receipt output")
    mode: DiscoverOriginal | OriginalRun | FrozenReceipt
    if (payload := retained if retained is not None else expected_bytes) is not None:
        mode = FrozenReceipt(payload)
        if args.run_id is not None and parse_release_json(payload)["run"]["id"] != args.run_id:
            raise ValueError("manual run ID cannot replace the frozen original")
    else:
        mode = DiscoverOriginal() if args.run_id is None else OriginalRun(args.run_id)
    retrieved = retrieve_draft_receipt(policy, source, partial(api, repository=policy.repository), selection=mode)
    updates = {
        paths["selection.json"]: retrieved.selection,
        paths["target.json"]: retrieved.target_bytes,
        paths["draft-receipt.json"]: retrieved.receipt_bytes,
    }
    if any(before is not None and before != updates[path] for path, before in originals.items()):
        raise ValueError("existing receipt output differs from the original authenticated files")

    def unchanged() -> None:
        if configuration.read_bytes() != original_policy or (expected and expected.read_bytes() != expected_bytes):
            raise ValueError("receipt assertions changed during retrieval")
        for path, before in originals.items():
            _regular(path)
            if (path.read_bytes() if path.exists() else None) != before:
                raise ValueError("receipt output changed during retrieval")

    replace_many(updates, verify=unchanged)
    print(f"Retrieved original draft {retrieved.target.release_id}; retain {paths['selection.json']} for every retry.")


def run_assets(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.dependency_provenance import check_release_dependencies

    if args.operation in ("plan", "prepare-authenticated"):
        from research_repo_tools.draft_workflow import DraftHandoffArtifact, plan_release_draft, prepare_authenticated_draft
        from research_repo_tools.release_receipts import TagSource

        source = TagSource(f"refs/tags/{args.tag}", args.commit)
        configuration = _path(settings.root, args.configuration)
        if args.operation == "plan":
            _validate_distinct_paths((configuration, args.output, *([args.github_output] if args.github_output is not None else [])))
            if settings.publishing is None or settings.publishing.repository != args.repository:
                raise ValueError("draft source repository differs from independent assertion")
            if args.output.resolve().is_relative_to(settings.root.resolve()):
                raise ValueError("retain draft handoff outside the observed checkout")
            original = _read_candidate_input(args.output) if args.output.exists() else None
            payload = plan_release_draft(settings, configuration, source, event=args.event, expected=original)

            def verify_plan() -> None:
                plan_release_draft(settings, configuration, source, event=args.event, expected=payload)

            write_release_outputs(args.output, payload, github_output=args.github_output, values={"commit": args.commit}, verify=verify_plan)
            print("Saved verified tag-source handoff; retain and authenticate its original artifact before draft preparation.")
        else:
            policy = load_receipt_policy(configuration)
            if policy.repository != args.repository:
                raise ValueError("draft handoff repository differs from independent assertion")
            target = prepare_authenticated_draft(
                settings.root,
                policy,
                source,
                DraftHandoffArtifact(args.run_id, args.attempt, args.artifact_id, args.artifact_sha256),
                output=args.output,
                executing_run_id=args.executing_run_id,
                executing_attempt=args.executing_attempt,
                approve=args.approve,
                recover_release_id=args.recover_release_id,
            )
            print(console_text(f"Prepared authenticated draft {target.release_id}; preserve the complete original state directory {args.output}.", sys.stdout))
        return
    if args.operation == "stage":
        from research_repo_tools.release_staging import stage_release_assets

        configuration = _path(settings.root, args.configuration)
        _validate_distinct_paths((configuration, args.target, args.journal, args.output, *args.asset))
        inputs = {path: _read_candidate_input(path) for path in (configuration, args.target)}
        policy = load_asset_policy(configuration)
        target = parse_release_target(inputs[args.target])
        title = args.tag if policy.expected_title == "tag" else None
        if (target.repository, target.tag, target.commit, target.expected_title) != (policy.repository, args.tag, args.commit, title):
            raise ValueError("release assertions/policy differ from captured target")
        checkout = args.checkout.resolve(strict=True) if args.checkout is not None else None
        if checkout is not None and Path(__file__).resolve().is_relative_to(checkout):
            raise ValueError("release assets require a trusted installed runtime outside the observed checkout")

        def unchanged() -> None:
            if any(_read_candidate_input(path) != data for path, data in inputs.items()):
                raise ValueError("asset batch policy or captured target changed")
            if checkout is not None and resolve_revision(checkout, args.tag) != args.commit:
                raise ValueError("local release tag differs from the independent commit assertion")

        stage_release_assets(
            settings.root,
            target,
            args.asset,
            journal=args.journal,
            destination=args.output,
            approve=args.approve,
            checkout=checkout,
            dependencies=settings.release.published_dependencies,
            verify_inputs=unchanged,
        )
        print("Verified and retained the complete asset transfer; preserve its original staging journal.")
        return
    if args.operation == "retrieve":
        _retrieve_receipt(args, settings)
        return
    if args.operation == "prepare":
        from research_repo_tools.release_drafts import prepare_release_draft

        configuration = _path(settings.root, args.configuration)
        _validate_distinct_paths((configuration, args.expected, args.notes, args.receipt, args.output))
        for path in (configuration, args.expected, args.notes, args.output):
            _regular(path)
        # Reject an unrelated output before authorizing a remote operation. A
        # retry may only reproduce the target already captured by its receipt.
        if args.output.exists():
            from research_repo_tools.release_drafts import _Assertions, _Captured, _parse_receipt

            assertions = _Assertions(args.repository, args.tag, args.commit, args.expected_title, args.notes.read_bytes(), args.expected.read_bytes())
            _regular(args.receipt)
            retained = _parse_receipt(args.receipt.read_bytes(), assertions) if args.receipt.exists() else None
            if not isinstance(retained, _Captured) or retained.target != _read_target(args.output):
                raise ValueError("existing draft target does not match the original captured receipt")
        target = prepare_release_draft(
            settings.root,
            args.repository,
            args.tag,
            args.commit,
            policy=load_evidence_policy(configuration),
            expected=args.expected.read_bytes(),
            title=args.expected_title,
            notes=args.notes.read_bytes(),
            receipt=args.receipt,
            approve=args.approve,
            recover_release_id=args.recover_release_id,
        )
        write_release_outputs(args.output, serialize_release_target(target))
        print(f"Prepared unpublished draft {target.release_id}; retain its preparation receipt for every retry.")
        return

    check_release_dependencies(args.checkout, packages=settings.release.published_dependencies)
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
        target = preflight_release_target(
            settings.root,
            policy.repository,
            args.tag,
            args.commit,
            expected_title=title,
            checkout=checkout,
            dependencies=settings.release.published_dependencies,
        )

        def verify() -> None:
            revalidate_release_target(settings.root, target, checkout=checkout, dependencies=settings.release.published_dependencies)

        write_release_outputs(
            args.output,
            serialize_release_target(target),
            github_output=args.github_output,
            values={"commit": target.commit, "tag": target.tag, "release_id": str(target.release_id)},
            verify=verify,
        )
        print("Saved verified release target.")
    else:
        target = _read_target(args.target)
        if (target.repository, target.tag, target.commit, target.expected_title) != (policy.repository, args.tag, args.commit, title):
            raise ValueError("release assertions/policy differ from captured target")
        revalidate_release_target(settings.root, target, checkout=checkout, dependencies=settings.release.published_dependencies)
        if args.operation == "upload":
            publish_release_asset(
                settings.root,
                policy.repository,
                args.tag,
                args.asset,
                target=target,
                checkout=checkout,
                publish=args.publish,
                dependencies=settings.release.published_dependencies,
            )
        print("Verified release target and requested asset operation.")


def run(args: argparse.Namespace, settings: Config) -> int:
    try:
        # Every Path-valued option in these commands names a consumer file or
        # checkout. Keep the parser namespace reusable and apply --root once.
        args = argparse.Namespace(
            **{
                name: settings.path(str(value))
                if isinstance(value, Path)
                else [settings.path(str(path)) for path in value]
                if name == "asset" and isinstance(value, list)
                else value
                for name, value in vars(args).items()
            }
        )
        from research_repo_tools.dependency_progress import DependencyProgress, reporting

        with reporting(DependencyProgress(mode=getattr(args, "progress", "none")), "release " + args.action):
            (run_evidence if args.action == "evidence" else run_assets)(args, settings)
    except Exception as error:
        diagnostic = redact_release_diagnostic(format_exception_diagnostics(error))
        if args.action == "evidence" and getattr(args, "format", None) == "summary":
            diagnostic += (
                "\nRelease evidence was not accepted. Check the reported paths, exact commit and original trusted handoffs. "
                "Wait for pending CI; resolve missing/expired artifacts through the complete-run recovery procedure. "
                "Changed measured inputs require a new acquisition; changed approval selections require explicit review."
            )
        raise ValueError(diagnostic) from error
    return 0
