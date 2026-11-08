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

from packaging.utils import canonicalize_name

from research_repo_tools._console import write_stdout
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
        options = _table(raw["distributions"], "distributions", {"files"}, {"index-url", "max-age-seconds", "max-distribution-bytes"})
        distributions = PythonDistributionPolicy(
            _strings(options["files"], "distribution files"),
            _string(options.get("index-url", "https://pypi.org"), "PyPI index URL"),
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
    for operation in ("preflight", "prepare", "upload", "verify"):
        command = assets.add_parser(operation)
        command.add_argument("configuration")
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
    candidate = commands.add_parser("candidate", help="bind a prepared benchmark acquisition to final native evidence without measuring")
    candidate.add_argument("configuration", help="native release evidence policy")
    candidate.add_argument("--acceptance", type=Path, help="frozen Step 2 approval record to revalidate")
    candidate.add_argument("--acquisition-sha256", required=True, help="independent Step 1 assertion digest from the trusted acquisition owner")
    candidate.add_argument("--checkout", type=Path, required=True)
    candidate.add_argument("--format", choices=("json", "status", "summary"), default="status")
    candidate.add_argument("--manifest", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--payload", type=Path, required=True)
    candidate.add_argument("--policy", type=Path, required=True, help="independently trusted acquisition assertions and final commit")
    candidate.add_argument("--publication-receipt", type=Path, help="original shared-publisher receipt captured before promotion")
    candidate.add_argument("--publication-sha256", help="independent Step 1 publication digest from the trusted owner")
    for operation in ("create", "published", "verify"):
        command = commands.add_parser(operation)
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

    check_release_dependencies(args.checkout, packages=settings.release.published_dependencies)
    if args.operation == "candidate":
        _run_candidate(args, settings)
        return
    path = _path(settings.root, args.configuration)
    original = path.read_bytes()
    policy = load_evidence_policy(path)
    if args.operation == "published" and policy.distributions is None:
        raise ValueError("published evidence verification requires Python distribution policy")
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


def _run_candidate(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.candidate_reuse import parse_candidate_policy, verify_candidate_release
    from research_repo_tools.evidence import parse_evidence

    configuration = _path(settings.root, args.configuration)
    paths = (
        configuration,
        args.policy,
        args.payload,
        args.manifest,
        *([args.acceptance] if args.acceptance else []),
        *([args.publication_receipt] if args.publication_receipt else []),
    )
    _validate_distinct_paths((args.output, *paths))
    inputs = {}
    from research_repo_tools.archives import ArchiveLimits

    for path in paths:
        _regular(path)
        with path.open("rb") as stream:
            data = stream.read(ArchiveLimits().content_bytes + 1)
        if len(data) > ArchiveLimits().content_bytes:
            raise ValueError("candidate handoff exceeds content limit")
        inputs[path] = data
    policy = parse_candidate_policy(inputs[args.policy])
    commit = policy.commit
    if commit is None:
        raise ValueError("candidate release requires an independently selected final commit")
    native = load_evidence_policy(configuration)
    checkout = args.checkout.resolve(strict=True)

    def unchanged() -> None:
        from research_repo_tools.dependency_provenance import check_release_dependencies

        check_release_dependencies(checkout, packages=settings.release.published_dependencies)
        for path, data in inputs.items():
            _regular(path)
            if path.read_bytes() != data:
                raise ValueError("candidate handoff changed during verification")
        _checkout(checkout, native.package, policy.tag.removeprefix("v"), commit, path_in_vcs=native.path_in_vcs, package_kind=native.package_kind)

    unchanged()
    accepted = verify_candidate_release(
        checkout,
        parse_evidence(inputs[args.payload], inputs[args.manifest]),
        policy,
        native,
        partial(api, repository=policy.repository),
        acquisition_sha256=args.acquisition_sha256,
        publication_receipt=inputs[args.publication_receipt] if args.publication_receipt else None,
        publication_sha256=args.publication_sha256,
        expected=inputs[args.acceptance] if args.acceptance else None,
        dependencies=settings.release.published_dependencies,
    )
    write_release_outputs(args.output, accepted, verify=unchanged)
    _present_evidence(accepted, args.format, candidate=True, acquisition_owner=policy.acquisition_owner)


def run_assets(args: argparse.Namespace, settings: Config) -> None:
    from research_repo_tools.dependency_provenance import check_release_dependencies

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
            if not isinstance(retained, _Captured) or retained.target != parse_release_target(args.output.read_bytes()):
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
        target = parse_release_target(args.target.read_bytes())
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
        args = argparse.Namespace(**{name: settings.path(str(value)) if isinstance(value, Path) else value for name, value in vars(args).items()})
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
