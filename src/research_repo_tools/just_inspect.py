"""Inspect native Just metadata and dry-run text with the package's Just version.

Only trusted Justfiles should be inspected: native evaluation, imports and
configuration are not a security sandbox. No recipe body is deliberately run.
"""

import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from research_repo_tools.process import resolve_executable, run_command

__all__ = ["Justfile", "dry_run", "inspect_justfile"]


@dataclass(frozen=True)
class Justfile:
    """A snapshot of native root-level recipes and alias target names.

    Recipe dictionaries retain native JSON fields/expressions. This is metadata,
    not parsed shell argv or a policy model. Module contents remain outside this
    small interface; module recipes can still be selected by dry_run.
    """

    recipes: dict[str, dict[str, Any]]
    aliases: dict[str, str]


def _command(root: Path, justfile: Path, executable: str | Path, env: Mapping[str, str] | None, timeout: float) -> tuple[Path, list[str], Path]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Just working directory does not exist: {root}")
    path = root / justfile
    if not path.is_file():
        raise ValueError(f"Justfile does not exist: {path}")
    binary = resolve_executable(executable, cwd=root, env=env)
    expected = version("rust-just")
    actual = run_command(binary, ["--version"], cwd=root, env=env, timeout=timeout).stdout.strip()
    if actual != f"just {expected}":
        raise ValueError(f"Just inspection requires just {expected} from the installed rust-just dependency; found {actual!r} at {binary}")
    return binary, ["--color", "never", "--justfile", str(path), "--working-directory", str(root)], root


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in Just metadata")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value in Just metadata: {value}")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON value in Just metadata")
    return result


def _metadata(text: str) -> Justfile:
    try:
        document = json.loads(text, object_pairs_hook=_object, parse_constant=_invalid_constant, parse_float=_finite_float)
        if not isinstance(document, dict) or not isinstance(document.get("recipes"), dict) or not isinstance(document.get("aliases"), dict):
            raise ValueError("expected recipes and aliases tables")
        recipes = document["recipes"]
        for name, recipe in recipes.items():
            if not isinstance(recipe, dict) or recipe.get("name") != name:
                raise ValueError(f"invalid recipe {name!r}")
            for field, identity in (("parameters", "name"), ("dependencies", "recipe")):
                items = recipe.get(field)
                if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get(identity), str) for item in items):
                    raise ValueError(f"invalid {field} for recipe {name!r}")
            if not isinstance(recipe.get("body"), list) or any(not isinstance(line, list) for line in recipe["body"]):
                raise ValueError(f"invalid body for recipe {name!r}")
        aliases = {}
        for name, alias in document["aliases"].items():
            if not isinstance(alias, dict) or alias.get("name") != name or not isinstance(alias.get("target"), str):
                raise ValueError(f"invalid alias {name!r}")
            aliases[name] = alias["target"]
        return Justfile(recipes, aliases)
    except (ValueError, TypeError) as error:
        raise ValueError(f"invalid Just JSON metadata: {error}") from error


def inspect_justfile(
    root: Path,
    *,
    justfile: Path = Path("justfile"),
    executable: str | Path = "just",
    env: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> Justfile:
    """Load validated native JSON metadata, without running recipe bodies.

    Relative Justfile/executable paths use root. env replaces the subprocess
    environment; omission inherits it. Missing executables, process failures and
    timeouts use the public process exceptions; malformed metadata/version drift
    raise ValueError. No installation or implicit parent Justfile search occurs.
    """
    binary, arguments, root = _command(root, justfile, executable, env, timeout)
    result = run_command(binary, [*arguments, "--dump", "--dump-format", "json"], cwd=root, env=env, timeout=timeout)
    return _metadata(result.stdout)


def dry_run(
    root: Path,
    recipe: str,
    arguments: Sequence[str] = (),
    *,
    justfile: Path = Path("justfile"),
    executable: str | Path = "just",
    env: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    """Return native stdout/stderr for one recipe invocation, without shell parsing.

    Arguments remain separate argv entries (including leading dashes and spaces).
    Just owns arity, aliases, dependencies and expression evaluation. Dry-run is
    not a sandbox for untrusted Justfiles. Nonzero results raise CalledProcessError
    with original diagnostics, following process.run_command.
    """
    if not isinstance(recipe, str) or not recipe or "\0" in recipe or "=" in recipe:
        raise ValueError("select a nonempty Just recipe name, not a variable assignment")
    if not isinstance(arguments, Sequence) or isinstance(arguments, (str, bytes)) or any(not isinstance(arg, str) or "\0" in arg for arg in arguments):
        raise TypeError("arguments must be a sequence of strings without NUL")
    binary, options, root = _command(root, justfile, executable, env, timeout)
    return run_command(binary, [*options, "--one", "--dry-run", "--", recipe, *arguments], cwd=root, env=env, timeout=timeout)
