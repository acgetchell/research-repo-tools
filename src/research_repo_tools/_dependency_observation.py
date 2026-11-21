"""Private operation-owned guards collected from successful dependency checks."""

import hashlib
import time
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path

_REFRESH_INTERVAL_SECONDS = 5 * 60
_GUARDS: ContextVar[list[tuple[Callable[[], None], str]] | None] = ContextVar("dependency_guards", default=None)


def retain_guard(verify: Callable[[], None], identity: str) -> None:
    """Register a dependency freshness check and its identity with the active observation, if any."""
    guards = _GUARDS.get()
    if guards is not None:
        guards.append((verify, identity))


class DependencyOperation:
    """One synchronous batch, with fresh entry/final proof and five-minute expiry.

    This is not a public verification override. The batch owns construction and
    disposal; standalone APIs always run their own full verification. Repeated
    identity observations recheck original local inputs and installed bytes.
    """

    def __init__(self, root: Path, packages: tuple[str, ...]) -> None:
        self.root, self.packages = root, packages
        self.guards: tuple[tuple[Callable[[], None], str], ...] = ()
        self.checked_at = 0.0
        self.refresh()

    def refresh(self) -> None:
        """Refresh dependency evidence and reject any change to the guarded inputs or runtime."""
        from research_repo_tools.dependency_provenance import check_release_dependencies

        for verify, _ in self.guards:
            verify()
        guards: list[tuple[Callable[[], None], str]] = []
        token = _GUARDS.set(guards)
        try:
            check_release_dependencies(self.root, packages=self.packages)
        finally:
            _GUARDS.reset(token)
        for verify, _ in self.guards:
            verify()
        if self.guards and tuple(identity for _, identity in guards) != tuple(identity for _, identity in self.guards):
            raise ValueError("dependency operation inputs or runtime changed during fresh verification")
        self.guards = tuple(guards)
        self.checked_at = time.monotonic()

    def check_inputs(self) -> None:
        """Recheck the exact local inputs retained by this operation."""
        for verify, _ in self.guards:
            verify()

    def verify(self) -> None:
        """Recheck local inputs and refresh registry observations after five minutes."""
        self.check_inputs()
        if time.monotonic() - self.checked_at >= _REFRESH_INTERVAL_SECONDS:
            self.refresh()

    @property
    def digest(self) -> str:
        """Hash the ordered identities of the retained dependency guards."""
        return hashlib.sha256("\n".join(identity for _, identity in self.guards).encode("ascii")).hexdigest()
