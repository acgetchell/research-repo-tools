"""CLI composition for dedicated runtime locks and portable verified bundles."""

import argparse
from pathlib import Path

lazy from research_repo_tools.config import Config


def add_commands(release) -> None:
    commands = release.add_parser("runtime", help="resolve, record and verify a dedicated isolated Python runtime").add_subparsers(
        dest="operation", required=True
    )
    command = commands.add_parser("install", help="install only approved retained wheels in a new venv and verify its graph")
    command.add_argument("--policy", type=Path, required=True)
    command.add_argument("--sha256", required=True)
    command.add_argument("--directory", type=Path, required=True)
    command = commands.add_parser("record", help="authenticate a dedicated project's installed graph and retain a new wheel bundle")
    command.add_argument("--project", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--build-store", type=Path)
    command = commands.add_parser("requirements", help="export hash requirements from an independently pinned runtime policy")
    command.add_argument("--policy", type=Path, required=True)
    command.add_argument("--sha256", required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("resolve", help="create a new dedicated runtime project and resolve its PyPI-only lock")
    command.add_argument("--directory", type=Path, required=True)
    command.add_argument("--require", action="append", required=True)
    command = commands.add_parser("verify", help="verify an installed external venv without importing it")
    command.add_argument("--policy", type=Path, required=True)
    command.add_argument("--sha256", required=True)
    command.add_argument("--environment", type=Path, required=True)


def run(args: argparse.Namespace, settings: Config) -> int:
    from research_repo_tools.dependency_build_receipts import read_regular
    from research_repo_tools.files import _validate_distinct_paths, replace_many
    from research_repo_tools.runtime_policy import MAX_POLICY_BYTES, parse_runtime_policy, runtime_requirements, verify_runtime
    from research_repo_tools.runtime_policy_build import install_runtime, record_runtime_policy, resolve_runtime

    def path(value: Path) -> Path:
        return settings.path(str(value))

    if args.operation == "resolve":
        print(resolve_runtime(path(args.directory), tuple(args.require)))
    elif args.operation == "record":
        policy = record_runtime_policy(path(args.project), path(args.output), build_directory=None if args.build_store is None else path(args.build_store))
        print(f"Recorded runtime policy SHA-256: {policy.digest}")
    else:
        policy_path = path(args.policy)
        policy = parse_runtime_policy(read_regular(policy_path, MAX_POLICY_BYTES), expected_sha256=args.sha256)
        if args.operation == "install":
            print(install_runtime(policy, path(args.directory), artifacts=policy_path.parent))
        elif args.operation == "requirements":
            output = path(args.output)
            _validate_distinct_paths((policy_path, output))
            replace_many({output: runtime_requirements(policy)})
        else:
            names = verify_runtime(policy, environment=path(args.environment), artifacts=policy_path.parent)
            print(f"Verified {len(names)} runtime distributions against {policy.digest}.")
    return 0
