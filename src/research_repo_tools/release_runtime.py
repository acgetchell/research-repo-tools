"""Prepare managed tools for an external, isolated release controller."""

import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from research_repo_tools import release_credentials, release_validation, toolchain, toolchain_config
from research_repo_tools._git_checkout import _in_checkout
from research_repo_tools.config import Config
from research_repo_tools.process import ExecutableNotFoundError, resolve_executable
lazy from research_repo_tools.runtime_policy import RuntimePolicy

__all__ = ["isolated_release_runtime"]


def _external(path: Path, checkout: Path) -> bool:
    if not path.is_absolute() or path.is_relative_to(checkout) or _in_checkout(path, checkout):
        return False
    # Reject paths that traverse the checkout to an external symlink target,
    # including filesystem aliases that pathlib cannot compare lexically.
    return not any(parent.exists() and parent.samefile(checkout) for parent in path.parents)


def _prepare(settings: Config, checkout: Path) -> tuple[dict[str, str], Path]:
    if not sys.flags.isolated:
        raise ValueError("start the release runtime with Python -I in an external installed environment")
    python = Path(sys.executable)
    if any(not _external(path, checkout) for path in (Path(__file__), Path(sys.prefix), python)):
        raise ValueError("release verification requires a trusted installed runtime outside the observed checkout")
    runtime = toolchain.Runtime(toolchain_config.load(settings))
    if not _external(runtime.base, checkout):
        raise ValueError("RESEARCH_REPO_TOOLS_HOME must be outside the observed checkout")
    env, managed = runtime._managed_environment()
    for name in ("UV_PYTHON_INSTALL_DIR", "CARGO_HOME", "RUSTUP_HOME"):
        if env.get(name) and not _external(Path(env[name]), checkout):
            raise ValueError(f"{name} must be an absolute managed location outside the observed checkout")
    if any(not _external(path, checkout) for path in managed):
        raise ValueError("managed executable directories must be outside the observed checkout")
    paths = (*managed, python.parent, *(Path(entry) for entry in env.get("PATH", "").split(os.pathsep)))
    env["PATH"] = os.pathsep.join(dict.fromkeys(str(path.resolve()) for path in paths if _external(path, checkout)))
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE"):
        env.pop(key, None)
    env.update(PYTHONNOUSERSITE="1", PYTHONSAFEPATH="1")
    if sys.platform == "win32":
        env["NoDefaultCurrentDirectoryInExePath"] = "1"

    selected = {}
    names = {"cargo", "gh", "git", "python", "rustup", "uv", *(tool.binary for tool in runtime.plan.cargo), *(tool.name for tool in runtime.plan.binaries)}
    for name in sorted(names):
        try:
            candidate = resolve_executable(name, cwd=checkout, env=env)
        except ExecutableNotFoundError as error:
            if name in {"gh", "git", "uv"}:
                raise ValueError(f"release runtime requires {name} on an absolute PATH entry outside the observed checkout") from error
            continue
        if not _external(candidate, checkout):
            raise ValueError(f"release runtime found {name} through a link into the observed checkout: {candidate}")
        selected[name] = candidate

    # Tool probes may load process-local configuration. Use an external cwd
    # and an absolute executable, and never probe the observed checkout's venv.
    with tempfile.TemporaryDirectory(prefix="rrt-release-runtime-") as directory:
        probe_root = Path(directory).resolve()
        if not _external(probe_root, checkout):
            raise ValueError("release runtime requires a temporary directory outside the observed checkout")
        uv = toolchain._probe(selected["uv"], "uv", runtime.plan.uv, env=release_credentials.environment(env), cwd=probe_root)
    if not uv.ok:
        raise ValueError(f"release runtime requires uv {runtime.plan.uv}; found {uv.actual} at {uv.path}")
    return env, selected["gh"]


@contextmanager
def isolated_release_runtime(
    settings: Config, *, checkout: Path | None = None, policy: RuntimePolicy | None = None, artifacts: Path | None = None
) -> Iterator[None]:
    """Scope managed tool selection around a single-threaded release entry point.

    Call from Python -I launched in a trusted installed environment outside the
    observed checkout. The caller still owns the exact package selection and
    independent release assertions. With policy/artifacts, verify the complete
    executing graph against an independently approved runtime manifest first.
    No project setup or sync runs here.
    """
    root = (settings.root if checkout is None else checkout).resolve(strict=True)
    if (policy is None) != (artifacts is None):
        raise ValueError("runtime policy and artifact directory must be supplied together")
    if policy is not None and artifacts is not None:
        from research_repo_tools.runtime_policy import verify_executing_runtime

        if not _external(artifacts.absolute(), root):
            raise ValueError("runtime policy artifacts must be outside the observed checkout")
        verify_executing_runtime(policy, artifacts=artifacts)
    env, gh = _prepare(settings, root)
    originals = dict(os.environ)
    token = release_validation._GH_EXECUTABLE.set(str(gh))
    try:
        os.environ.clear()
        os.environ.update(env)
        yield
    finally:
        os.environ.clear()
        os.environ.update(originals)
        release_validation._GH_EXECUTABLE.reset(token)
