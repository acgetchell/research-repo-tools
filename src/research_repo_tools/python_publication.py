"""Explicit destination approval for already frozen Python distribution evidence.

Approval records are caller-authenticated handoffs, never credentials. Consumers
own the approval environment and credential request, which follows live approval
verification. This module never builds, uploads, or requests registry credentials.
"""

import hashlib
import re
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from packaging.utils import canonicalize_name

from research_repo_tools.evidence import _object, _string, deterministic_json
from research_repo_tools.registry import pypi_destination, verify_published_distributions
from research_repo_tools.release_evidence import EvidencePolicy, verify_release_evidence
from research_repo_tools.release_validation import _policy_name, _repository, parse_release_json, verify_distributions

__all__ = ["PythonRegistryAuthority", "approve_python_publication", "load_registry_authority", "verify_python_publication"]

SCHEMA = "research-repo-tools/python-publication-approval/v1"


@dataclass(frozen=True, slots=True)
class PythonRegistryAuthority:
    """Independently approved endpoint and publisher authority, without secrets."""

    index_url: str
    account: str
    project: str
    repository: str
    workflow: str
    environment: str
    protocol: Literal["pypi-json"] = "pypi-json"
    authentication: Literal["none"] = "none"

    def __post_init__(self) -> None:
        if self.protocol != "pypi-json" or self.authentication != "none":
            raise ValueError("unsupported registry protocol/authentication; only unauthenticated HTTPS PyPI JSON verification is supported")
        pypi_destination(self.index_url)
        _repository(self.repository)
        for value, context in ((self.account, "registry account"), (self.project, "registry project"), (self.environment, "approval environment")):
            _policy_name(value, context)
        if not isinstance(self.workflow, str) or re.fullmatch(r"\.github/workflows/[A-Za-z0-9_-]+\.yml", self.workflow) is None:
            raise ValueError("publication workflow must be an explicit .github/workflows/*.yml path")


def load_registry_authority(path: Path) -> PythonRegistryAuthority:
    raw = _object(
        tomllib.loads(path.read_bytes().decode("utf-8")),
        "registry authority",
        {"schema", "index-url", "account", "project", "repository", "workflow", "environment", "protocol", "authentication"},
    )
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("registry authority schema must be integer 1")
    if raw["protocol"] != "pypi-json" or raw["authentication"] != "none":
        raise ValueError("unsupported registry protocol/authentication; only unauthenticated HTTPS PyPI JSON verification is supported")
    return PythonRegistryAuthority(
        _string(raw["index-url"], "index-url"),
        _string(raw["account"], "account"),
        _string(raw["project"], "project"),
        _string(raw["repository"], "repository"),
        _string(raw["workflow"], "workflow"),
        _string(raw["environment"], "environment"),
    )


def _record(policy: EvidencePolicy, selection: bytes, authority: PythonRegistryAuthority) -> bytes:
    if policy.distributions is None or not isinstance(authority, PythonRegistryAuthority):
        raise ValueError("Python publication requires distribution evidence and explicit registry authority")
    if authority.repository != policy.validation.repository or canonicalize_name(authority.project) != canonicalize_name(policy.package):
        raise ValueError("registry authority repository/project differs from evidence")
    if policy.distributions.index_url is not None and policy.distributions.index_url != authority.index_url:
        raise ValueError("registry-bound evidence cannot change destination; approve its original endpoint")
    if not isinstance(selection, bytes) or not 0 < len(selection) <= 1024 * 1024:
        raise ValueError("publication requires the original bounded evidence selection")
    return deterministic_json({"schema": SCHEMA, "selection_sha256": hashlib.sha256(selection).hexdigest(), "authority": asdict(authority)})


def _unchanged_distributions(policy: EvidencePolicy, version: str, selection: bytes, distributions: Path) -> None:
    assert policy.distributions is not None
    current = verify_distributions(
        distributions,
        f"v{version}",
        package=policy.package,
        filenames=policy.distributions.filenames(policy.package, version),
        max_total_bytes=policy.distributions.max_distribution_bytes,
    )
    if current != parse_release_json(selection)["files"]:
        raise ValueError("publication distribution bytes changed during approval verification")


def approve_python_publication(
    policy: EvidencePolicy,
    version: str,
    commit: str,
    read: Callable[[str], Any],
    *,
    selection: bytes,
    authority: PythonRegistryAuthority,
    distributions: Path,
    approve: bool = False,
) -> bytes:
    """Bind an explicitly approved authority to original evidence and exact bytes.

    Call only after the consumer's approval boundary. The evidence is revalidated
    here before any subsequent credential request. No replacement run is selected.
    """
    if approve is not True:
        raise ValueError("Python registry publication requires explicit approval")
    result = _record(policy, selection, authority)
    verify_release_evidence(policy, version, commit, read, expected=selection, distributions=distributions)
    _unchanged_distributions(policy, version, selection, distributions)
    return result


def verify_python_publication(
    policy: EvidencePolicy,
    version: str,
    commit: str,
    read: Callable[[str], Any],
    *,
    selection: bytes,
    authority: PythonRegistryAuthority,
    approval: bytes,
    distributions: Path,
    published: bool = False,
) -> bytes:
    """Revalidate a trusted approval immediately before credentials or after upload.

    The caller supplies the original selection and authority independently of the
    approval. After an uncertain upload use published=True to inspect the exact
    registry inventory and byte identities; this never retries an upload.
    """
    expected = _record(policy, selection, authority)
    if not isinstance(approval, bytes) or approval != expected:
        raise ValueError("publication approval is missing, malformed or differs from original selection/authority; obtain new approval")
    if type(published) is not bool:
        raise ValueError("published verification flag must be boolean")
    current = verify_release_evidence(policy, version, commit, read, expected=selection, distributions=distributions)
    if published:
        verify_published_distributions(policy.package, version, parse_release_json(current)["files"], index_url=authority.index_url)
        verify_release_evidence(policy, version, commit, read, expected=selection, distributions=distributions)
    _unchanged_distributions(policy, version, selection, distributions)
    return expected
