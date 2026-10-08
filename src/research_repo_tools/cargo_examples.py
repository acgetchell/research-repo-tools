"""Discover, build and run consumer-selected Cargo binary examples."""

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.evidence import _load_json, _object, _string
from research_repo_tools.measurement import _strings
from research_repo_tools.process import run_command, run_command_live
from research_repo_tools.publication import _path
from research_repo_tools.publication_config import _table

__all__ = ["CargoExample", "discover_examples", "run_examples"]


@dataclass(frozen=True)
class CargoExample:
    """One binary example reported by Cargo, including its feature requirements."""

    package: str
    package_id: str
    name: str
    source: Path
    required_features: tuple[str, ...]


@dataclass(frozen=True)
class _Policy:
    args: tuple[str, ...] = ()
    expect: tuple[bytes, ...] = ()
    features: tuple[str, ...] = ()
    no_default_features: bool = False
    timeout: int = 300

    @property
    def overrides_features(self) -> bool:
        return bool(self.features) or self.no_default_features


def _names(value: object, context: str, *, nonempty: bool = False) -> tuple[str, ...]:
    values = _strings(value, context, nonempty=nonempty)
    if len(set(values)) != len(values) or any("\x00" in item for item in values):
        raise ValueError(f"{context} must contain unique strings without NUL")
    return values


def _budget(value: object, context: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{context} must be a positive integer")
    return value


def _literals(value: object, context: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or "\x00" in item or (not allow_empty and not item) for item in value):
        raise ValueError(f"{context} must be an array of {'possibly empty ' if allow_empty else 'nonempty '}strings without NUL")
    for item in value:
        item.encode("utf-8")
    return tuple(value)


def _binary_example(target: dict[str, object]) -> bool:
    kind = _names(target.get("kind"), "Cargo target kind", nonempty=True)
    crate_types = _names(target.get("crate_types"), "Cargo target crate_types", nonempty=True)
    if "example" not in kind:
        return False
    if kind != ("example",) or ("bin" in crate_types and crate_types != ("bin",)):
        raise ValueError("Cargo example kind/crate_types are ambiguous")
    if set(crate_types) - {"bin", "lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}:
        raise ValueError("Cargo example crate_types are unsupported")
    return crate_types == ("bin",)


def discover_examples(root: Path, *, package: str | None = None, timeout: int = 300) -> tuple[CargoExample, ...]:
    """Return sorted binary examples from exactly one workspace package.

    Cargo metadata --locked --no-deps is authoritative, including nested and
    explicit targets. Virtual/multi-package workspaces require package selection.
    Empty, ambiguous or malformed inventories fail before building anything.
    Library examples are outside this executable runner's supported inventory.
    """
    _budget(timeout, "metadata timeout")
    if package is not None:
        _string(package, "Cargo package selection")
    result = run_command("cargo", ["metadata", "--locked", "--no-deps", "--format-version=1"], cwd=root, timeout=timeout)
    metadata = _object(_load_json(result.stdout.encode("utf-8"), "Cargo metadata"), "Cargo metadata")
    if type(metadata.get("version")) is not int or metadata["version"] != 1:
        raise ValueError("Cargo metadata version must be integer 1")
    members = _names(metadata.get("workspace_members"), "Cargo workspace_members", nonempty=True)
    packages = metadata.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("Cargo metadata packages must be a nonempty array")
    entries = [_object(item, "Cargo package") for item in packages]
    ids = [_string(item.get("id"), "Cargo package id") for item in entries]
    if len(set(ids)) != len(ids) or set(members) - set(ids):
        raise ValueError("Cargo workspace package identities are missing or duplicated")
    entries = [item for item in entries if item["id"] in members]
    for item in entries:
        _string(item.get("name"), "Cargo package name")
    if package is not None:
        entries = [item for item in entries if item["name"] == package]
    if len(entries) != 1:
        raise ValueError("select exactly one Cargo workspace package for example discovery")
    selected = entries[0]
    targets = selected.get("targets")
    if not isinstance(targets, list):
        raise ValueError("Cargo package targets must be an array")
    examples = []
    for item in targets:
        target = _object(item, "Cargo target")
        if not _binary_example(target):
            continue
        name = _string(target.get("name"), "Cargo example name")
        source = Path(_string(target.get("src_path"), "Cargo example src_path"))
        if not source.is_absolute():
            raise ValueError("Cargo example src_path must be absolute")
        required = _names(target.get("required-features", []), "Cargo example required-features")
        examples.append(CargoExample(str(selected["name"]), str(selected["id"]), name, source, required))
    if not examples or len({item.name for item in examples}) != len(examples):
        raise ValueError("Cargo discovery must return nonempty, unique binary examples")
    return tuple(sorted(examples, key=lambda item: item.name))


def _policy(value: object, timeout: int) -> _Policy:
    table = _table(value, "example policy", set(), {"args", "expect", "features", "no-default-features", "timeout"})
    args = _literals(table.get("args", []), "example args", allow_empty=True)
    expected = _literals(table.get("expect", []), "example expected markers")
    features = _names(table.get("features", []), "example features")
    if any(any(character.isspace() for character in feature) or "," in feature for feature in features):
        raise ValueError("declare each Cargo feature as a separate name")
    no_default = table.get("no-default-features", False)
    if type(no_default) is not bool:
        raise ValueError("no-default-features must be boolean")
    return _Policy(args, tuple(item.encode("utf-8") for item in expected), features, no_default, _budget(table.get("timeout", timeout), "example timeout"))


def _build(root: Path, examples: tuple[CargoExample, ...], profile: str, timeout: int, override: _Policy | None = None) -> dict[str, Path]:
    first = examples[0]
    args = ["build", "--locked", "--package", first.package_id, "--profile", profile, "--message-format=json-render-diagnostics"]
    if override is None:
        args.append("--examples")
    else:
        args.extend(["--example", first.name])
        if override.features:
            args.extend(["--features", ",".join(override.features)])
        if override.no_default_features:
            args.append("--no-default-features")
    result = run_command("cargo", args, cwd=root, timeout=timeout)
    print(result.stderr, end="", file=sys.stderr)
    executables: dict[str, Path] = {}
    wanted = {example.name for example in examples}
    # Cargo emits one JSON record per LF. Unicode line separators may occur
    # inside valid JSON strings (including native paths), so splitlines is wrong.
    for line in result.stdout.removesuffix("\n").split("\n"):
        message = _object(_load_json(line.encode("utf-8"), "Cargo build message"), "Cargo build message")
        reason = _string(message.get("reason"), "Cargo build message reason")
        if reason != "compiler-artifact" or message.get("package_id") != first.package_id:
            continue
        target = _object(message.get("target"), "Cargo artifact target")
        if not _binary_example(target):
            continue
        name = _string(target.get("name"), "Cargo artifact name")
        if name not in wanted:
            continue
        executable = Path(_string(message.get("executable"), "Cargo example executable"))
        if not executable.is_absolute() or name in executables:
            raise ValueError("Cargo example executable must be absolute and unique")
        executables[name] = executable
    missing = wanted - executables.keys()
    if missing:
        raise ValueError(f"Cargo build omitted executable examples {sorted(missing)!r}; declare their required feature overrides in consumer configuration")
    return executables


def run_examples(root: Path, configuration: str) -> None:
    """Run schema-1 Cargo-example TOML; see docs/cargo-examples-api.md.

    Validate policy and complete discovery before builds. Build default-feature
    examples once, run the selected ordinary binaries, then build/run each
    feature override immediately so another build cannot overwrite its binary.
    No shell is used. Fail fast with the process API's original command/timeout
    exceptions; consumer marker failures raise ValueError.
    """
    root = root.resolve()
    raw = _table(
        tomllib.loads(_path(root, configuration).read_text(encoding="utf-8")),
        "Cargo examples",
        {"schema"},
        {"package", "profile", "include", "exclude", "timeout", "build-timeout", "metadata-timeout", "examples"},
    )
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("Cargo examples schema must be integer 1")
    package = _string(raw["package"], "Cargo package") if "package" in raw else None
    profile = _string(raw.get("profile", "release"), "Cargo profile")
    include = _names(raw["include"], "included examples", nonempty=True) if "include" in raw else None
    exclude = _names(raw.get("exclude", []), "excluded examples")
    timeout = _budget(raw.get("timeout", 300), "example timeout")
    build_timeout = _budget(raw.get("build-timeout", 1800), "build timeout")
    metadata_timeout = _budget(raw.get("metadata-timeout", 300), "metadata timeout")
    policies = {name: _policy(value, timeout) for name, value in _object(raw.get("examples", {}), "example policies").items()}
    discovered = discover_examples(root, package=package, timeout=metadata_timeout)
    available = {example.name for example in discovered}
    unknown = (set(include or ()) | set(exclude) | policies.keys()) - available
    if unknown:
        raise ValueError(f"configuration names unknown Cargo examples: {sorted(unknown)!r}")
    selected = tuple(example for example in discovered if (include is None or example.name in include) and example.name not in exclude)
    if not selected:
        raise ValueError("Cargo example selection must not be empty")
    default = _Policy(timeout=timeout)
    ordinary = tuple(example for example in selected if not policies.get(example.name, default).overrides_features)

    def execute(example: CargoExample, executable: Path) -> None:
        policy = policies.get(example.name, default)
        print(f"Running Cargo example {example.package}/{example.name}", file=sys.stderr, flush=True)
        run_command_live(executable, policy.args, cwd=root, timeout=policy.timeout, stdout_markers=policy.expect)

    if ordinary:
        binaries = _build(root, ordinary, profile, build_timeout)
        for example in ordinary:
            execute(example, binaries[example.name])
    for example in selected:
        policy = policies.get(example.name, default)
        if policy.overrides_features:
            binaries = _build(root, (example,), profile, build_timeout, policy)
            execute(example, binaries[example.name])
