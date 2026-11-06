"""Public source selection and release graph contracts, also run installed."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import override
from unittest.mock import patch
from urllib.parse import urlsplit

from research_repo_tools import (
    cli,
    dependency_artifacts,
    dependency_graphs,
    dependency_policy,
    dependency_sources,
    dependency_workflows,
    files,
    process,
    registry,
    tooling_runtime,
)
from research_repo_tools.dependency_policy import DependencyProject, load_policy, parse_policy
from research_repo_tools.dependency_provenance import check_release_dependencies
from research_repo_tools.dependency_sources import GitCommit, RegistryRelease, VerifiedSource, parse_source, verify_source
from research_repo_tools.dependency_workflows import apply_source, check_dependency_policy, plan_source

COMMIT = "a" * 40
DIGEST = "b" * 64
PYTHON_URL = "https://files.pythonhosted.org/packages/fixture/sample_pkg-1.2.3-py3-none-any.whl"
PYTHON_API = "https://pypi.org/pypi/sample-pkg/1.2.3/json"
CARGO_API = "https://crates.io/api/v1/crates/sample-crate/1.2.3"


class Provider:
    def __init__(self) -> None:
        self.responses: dict[str, bytes] = {}
        self.calls: list[str] = []

    def put(self, url: str, value: object) -> None:
        self.responses[url] = json.dumps(value).encode()

    def read(self, url: str) -> bytes:
        self.calls.append(url)
        if url not in self.responses:
            raise ValueError("provider unavailable or source missing")
        return self.responses[url]

    def python(self, **changes: object) -> None:
        upload = {
            "filename": "sample_pkg-1.2.3-py3-none-any.whl",
            "url": PYTHON_URL,
            "size": 12,
            "digests": {"sha256": DIGEST},
            "yanked": False,
            "packagetype": "bdist_wheel",
        }
        upload.update(changes)
        self.put(PYTHON_API, {"info": {"name": "sample-pkg", "version": "1.2.3"}, "urls": [upload]})

    def cargo(self, **changes: object) -> None:
        data = {"crate": "sample-crate", "num": "1.2.3", "yanked": False, "checksum": DIGEST, "crate_size": 12}
        data.update(changes)
        self.put(CARGO_API, {"version": data})

    def git(self, ecosystem: str, *, directory: str = ".", commit: str = COMMIT) -> None:
        self.put("https://api.github.com/repos/example/upstream", {"full_name": "example/upstream"})
        self.put(f"https://api.github.com/repos/example/upstream/git/commits/{commit}", {"sha": commit, "tree": {"sha": "c" * 40}})
        manifest = "project" if ecosystem == "python" else "package"
        name = "sample-pkg" if ecosystem == "python" else "sample-crate"
        filename = "pyproject.toml" if ecosystem == "python" else "Cargo.toml"
        path = filename if directory == "." else f"{directory}/{filename}"
        payload = f'[{manifest}]\nname="{name}"\nversion="1.2.3"\n'.encode()
        self.responses[f"https://raw.githubusercontent.com/example/upstream/{commit}/{path}"] = payload
        self.put(
            f"https://api.github.com/repos/example/upstream/git/trees/{'c' * 40}?recursive=1",
            {
                "sha": "c" * 40,
                "truncated": False,
                "tree": [{"path": path, "type": "blob", "mode": "100644", "sha": hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()}],
            },
        )


class TestSources(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.provider = Provider()
        self.provider.python()
        self.provider.cargo()

    def test_validated_variants_and_verified_immutable_result(self) -> None:
        python = parse_source({"ecosystem": "python", "package": "Sample_Pkg", "version": "1.2.3"})
        self.assertEqual(python, RegistryRelease("python", "sample-pkg", "1.2.3"))
        for source in (python, RegistryRelease("cargo", "sample-crate", "1.2.3")):
            with self.subTest(source=source):
                proof = verify_source(source, provider=self.provider, release=True)
                self.assertEqual(proof.source, source)
                self.assertEqual(proof.version, "1.2.3")
                self.assertEqual(proof.artifacts[0].sha256, DIGEST)
                with self.assertRaises(FrozenInstanceError):
                    setattr(proof, "version", "9.0.0")
        with self.assertRaisesRegex(TypeError, "verify_source"):
            VerifiedSource()

    def test_floating_competing_and_unsupported_selectors_fail_before_lookup(self) -> None:
        baseline = {"ecosystem": "cargo", "package": "sample-crate", "github": "example/upstream", "commit": COMMIT}
        for changes in (
            {"commit": "main"},
            {"commit": COMMIT[:12]},
            {"version": "1.2.3"},
            {"branch": "main"},
            {"github": "https://github.com/example/upstream"},
            {"directory": "../escape"},
            {"directory": "fixtures:old"},
            {"registry": "private"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_source({**baseline, **changes})
        for version in ("^1.2", "1.2", "01.2.3", "1.2.3-01"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                RegistryRelease("cargo", "sample-crate", version)
        for version in ("~=1.2", "1.2.*", "1.2.3+local", "v1.2.3"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                RegistryRelease("python", "sample-pkg", version)
        self.assertEqual(self.provider.calls, [])

    def test_commit_verification_requires_repository_commit_and_package(self) -> None:
        for ecosystem, name in (("python", "sample-pkg"), ("cargo", "sample-crate")):
            self.provider.git(ecosystem)
            source = GitCommit(ecosystem, name, "example/upstream", COMMIT)
            proof = verify_source(source, provider=self.provider)
            self.assertEqual(proof.version, "1.2.3")
            self.assertEqual(proof.artifacts, ())
            self.provider.calls.clear()
            with self.assertRaisesRegex(ValueError, "development-only"):
                verify_source(source, provider=self.provider, release=True)
            self.assertEqual(self.provider.calls, [])
            for url, replacement in (
                ("https://api.github.com/repos/example/upstream", b'{"full_name":"other/repo"}'),
                (f"https://api.github.com/repos/example/upstream/git/commits/{COMMIT}", b'{"sha":"bbbb"}'),
            ):
                original = self.provider.responses[url]
                self.provider.responses[url] = replacement
                with self.subTest(url=url), self.assertRaises(ValueError):
                    verify_source(source, provider=self.provider)
                self.provider.responses[url] = original
            wrong = GitCommit(ecosystem, "wrong-package", "example/upstream", COMMIT)
            with self.assertRaisesRegex(ValueError, "different package"):
                verify_source(wrong, provider=self.provider)

    def test_registry_rejections_are_distinct_and_retryable(self) -> None:
        python = RegistryRelease("python", "sample-pkg", "1.2.3")
        cargo = RegistryRelease("cargo", "sample-crate", "1.2.3")
        for changes in (
            {"yanked": True},
            {"size": 0},
            {"size": True},
            {"url": "https://private.example/file.whl"},
            {"digests": {"sha256": "missing"}},
            {"filename": "different-1.2.3-py3-none-any.whl"},
        ):
            self.provider.python(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                verify_source(python, provider=self.provider)
        self.provider.python()
        for changes in ({"yanked": True}, {"crate": "another"}, {"num": "1.2.4"}, {"checksum": "invalid"}, {"crate_size": 0}):
            self.provider.cargo(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                verify_source(cargo, provider=self.provider)
        for payload in (b"{}", b'{"info":{},"info":{}}', b'{"version":NaN}', b"not json"):
            self.provider.responses[PYTHON_API] = payload
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                verify_source(python, provider=self.provider)
        self.provider.python()
        self.assertEqual(verify_source(python, provider=self.provider).version, "1.2.3")
        with self.assertRaisesRegex(ValueError, "missing"):
            verify_source(RegistryRelease("python", "sample-pkg", "9.9.9"), provider=self.provider)

    def test_verified_identity_ignores_mutable_provider_counters_but_binds_artifacts(self) -> None:
        source = RegistryRelease("cargo", "sample-crate", "1.2.3")
        before = verify_source(source, provider=self.provider)
        self.provider.cargo(downloads=4567)
        self.assertEqual(verify_source(source, provider=self.provider), before)
        self.provider.cargo(downloads=4568, checksum="d" * 64)
        self.assertNotEqual(verify_source(source, provider=self.provider), before)
        self.provider.git("python")
        git = GitCommit("python", "sample-pkg", "example/upstream", COMMIT)
        before = verify_source(git, provider=self.provider)
        self.provider.put("https://api.github.com/repos/example/upstream", {"full_name": "example/upstream", "stargazers_count": 42, "pushed_at": "changed"})
        self.assertEqual(verify_source(git, provider=self.provider), before)

    def test_cargo_workspace_versions_use_only_verified_nearest_manifests(self) -> None:
        for directory, parents, expected in (
            (".", {}, "1.2.3"),
            ("crates/member", {"Cargo.toml": b'[workspace.package]\nversion="2.0.0"\n'}, "2.0.0"),
            (
                "crates/member",
                {"Cargo.toml": b'[workspace.package]\nversion="3.0.0"\n', "crates/Cargo.toml": b'[workspace.package]\nversion="2.0.0"\n'},
                "2.0.0",
            ),
        ):
            with self.subTest(directory=directory, parents=parents):
                self.provider.git("cargo", directory=directory)
                path = "Cargo.toml" if directory == "." else f"{directory}/Cargo.toml"
                manifests = {**parents, path: b'[package]\nname="sample-crate"\nversion.workspace=true\n'}
                if directory == ".":
                    manifests[path] += b'[workspace.package]\nversion="1.2.3"\n'
                tree = []
                for member, payload in manifests.items():
                    self.provider.responses[f"https://raw.githubusercontent.com/example/upstream/{COMMIT}/{member}"] = payload
                    tree.append(
                        {"path": member, "type": "blob", "mode": "100644", "sha": hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()}
                    )
                self.provider.put(
                    f"https://api.github.com/repos/example/upstream/git/trees/{'c' * 40}?recursive=1", {"sha": "c" * 40, "truncated": False, "tree": tree}
                )
                self.provider.calls.clear()
                proof = verify_source(GitCommit("cargo", "sample-crate", "example/upstream", COMMIT, directory), provider=self.provider)
                self.assertEqual(proof.version, expected)
                raw_calls = [url for url in self.provider.calls if urlsplit(url).hostname == "raw.githubusercontent.com"]
                self.assertEqual(len(raw_calls), len(manifests))

    def test_cargo_tree_allows_unrelated_git_filenames_but_rejects_malformed_paths(self) -> None:
        self.provider.git("cargo")
        source = GitCommit("cargo", "sample-crate", "example/upstream", COMMIT)
        url = f"https://api.github.com/repos/example/upstream/git/trees/{'c' * 40}?recursive=1"
        original = json.loads(self.provider.responses[url])
        entry = {"path": "fixtures/input:1.txt", "type": "blob", "mode": "100644", "sha": "d" * 40}
        self.provider.put(url, {**original, "tree": [*original["tree"], entry]})
        self.assertEqual(verify_source(source, provider=self.provider).version, "1.2.3")
        self.assertEqual(len(self.provider.calls), 4)
        for path in ("", ".", "../input", "/input", "nested//input", "nested/./input", "nested/../input", "input\0.txt", "Cargo.toml"):
            with self.subTest(path=path):
                self.provider.put(url, {**original, "tree": [*original["tree"], {**entry, "path": path}]})
                with self.assertRaises(ValueError):
                    verify_source(source, provider=self.provider)

        manifest = b'[package]\nname="unrelated"\nversion="2.0.0"\n'
        member = "old:1/Cargo.toml"
        blob = {**entry, "path": member, "sha": hashlib.sha1(f"blob {len(manifest)}\0".encode() + manifest).hexdigest()}
        raw_url = f"https://raw.githubusercontent.com/example/upstream/{COMMIT}/old%3A1/Cargo.toml"
        self.provider.responses[raw_url] = manifest
        self.provider.put(url, {**original, "tree": [*original["tree"], blob]})
        self.assertEqual(verify_source(source, provider=self.provider).version, "1.2.3")
        self.assertIn(raw_url, self.provider.calls)
        self.provider.responses[raw_url] = manifest + b"# changed\n"
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            verify_source(source, provider=self.provider)


class TestSourceWorkflows(unittest.TestCase):
    @override
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="source café ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.provider = Provider()
        self.provider.python()
        self.provider.cargo()
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    key: value
                    for key, value in os.environ.items()
                    if key in {"UV_RUN_RECURSION_DEPTH", "UV_TOOL_BIN_DIR", "UV_TOOL_DIR", "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN"}
                    or not key.upper().startswith(("UV_", "CARGO_", "RUST"))
                },
                clear=True,
            )
        )
        self.native = self.enterContext(patch.object(dependency_workflows, "check_native_graph"))

        def fixture_configuration_paths(root: Path, ecosystem: dependency_sources.Ecosystem) -> tuple[Path, ...]:
            return tuple(path for path in dependency_policy.configuration_paths(root, ecosystem) if path.is_relative_to(self.root))

        for module in (dependency_graphs, dependency_workflows):
            self.enterContext(patch.object(module, "configuration_paths", side_effect=fixture_configuration_paths))

    def snapshot(self) -> dict[str, bytes]:
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def policy(self, ecosystem: str, *, git: bool = False) -> None:
        name = "sample-pkg" if ecosystem == "python" else "sample-crate"
        selector = f'github="example/upstream"\ncommit="{COMMIT}"' if git else 'version="1.2.3"'
        (self.root / "dependency-sources.toml").write_bytes(
            (f'schema=1\n[projects.main]\necosystem="{ecosystem}"\n[sources.upstream]\nproject="main"\npackage="{name}"\n{selector}\n').encode()
        )

    def python(self, *, git: bool = False) -> None:
        self.policy("python", git=git)
        (self.root / "pyproject.toml").write_bytes(
            b'# keep caf\xc3\xa9\r\n[project]\r\nname="consumer"\r\nversion="0.1.0"\r\nrequires-python=">=3.15"\r\n'
            b"dependencies=[\"sample-pkg[feature]==1.2.3; python_version >= '3.15'\"] # keep\r\n"
            b"[tool.uv]\r\npackage=false\r\n"
        )
        self.python_lock(git=git)
        self.provider.git("python")

    def python_lock(self, *, git: bool = False, root: Path | None = None) -> None:
        source = f'git="https://github.com/example/upstream.git?rev={COMMIT}#{COMMIT}"' if git else 'registry="https://pypi.org/simple"'
        artifacts = "" if git else f'wheels=[{{url="{PYTHON_URL}",hash="sha256:{DIGEST}",size=12}}]\n'
        (root or self.root).joinpath("uv.lock").write_bytes(
            (
                f'version=1\nrevision=3\nrequires-python=">=3.15"\n[[package]]\nname="consumer"\nversion="0.1.0"\nsource={{virtual="."}}\n[[package]]\nname="sample-pkg"\nversion="1.2.3"\nsource={{{source}}}\n{artifacts}'
            ).encode()
        )

    def cargo(self, *, git: bool = False) -> None:
        self.policy("cargo", git=git)
        (self.root / "Cargo.toml").write_bytes(
            b'# retain\r\n[package]\r\nname="consumer"\r\nversion="0.1.0"\r\n[dependencies]\r\nrenamed={package="sample-crate",version="=1.2.3",features=["extra"],optional=true}\r\n'
        )
        self.cargo_lock(git=git)
        self.provider.git("cargo")

    def cargo_lock(self, *, git: bool = False, root: Path | None = None) -> None:
        source = f"git+https://github.com/example/upstream.git?rev={COMMIT}#{COMMIT}" if git else dependency_graphs.CRATES_IO
        checksum = "" if git else f'checksum="{DIGEST}"\n'
        (root or self.root).joinpath("Cargo.lock").write_bytes(
            (
                f'version=4\n[[package]]\nname="consumer"\nversion="0.1.0"\n[[package]]\nname="sample-crate"\nversion="1.2.3"\nsource="{source}"\n{checksum}'
            ).encode()
        )

    def test_registry_graph_accepts_all_locked_dependencies_without_importing_them(self) -> None:
        for ecosystem in ("python", "cargo"):
            with self.subTest(ecosystem=ecosystem):
                (self.python if ecosystem == "python" else self.cargo)()
                before = self.snapshot()
                proofs = check_dependency_policy(self.root, provider=self.provider)
                self.assertEqual(
                    [(p.source.ecosystem, p.source.package, p.version) for p in proofs],
                    [(ecosystem, "sample-pkg" if ecosystem == "python" else "sample-crate", "1.2.3")],
                )
                self.assertEqual(self.snapshot(), before)
                self.native.assert_called()

    def test_crates_metadata_is_fresh_and_paced_across_graphs_and_providers_only(self) -> None:
        self.cargo()
        graph = dependency_graphs.inspect_project(self.root, DependencyProject("cargo"))
        now = [0.0]
        requests = []

        def read(url: str) -> bytes:
            requests.append((url, now[0]))
            return self.provider.read(url)

        def sleep(seconds: float) -> None:
            now[0] += seconds

        class Response(io.BytesIO):
            status = 200
            url = "https://static.crates.io/crates/sample-crate/sample-crate-1.2.3.crate"

        with (
            patch.object(dependency_sources, "_crates_io_requested_at", None),
            patch.object(dependency_sources.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(dependency_sources.time, "sleep", side_effect=sleep),
            patch.object(tooling_runtime, "_read", side_effect=read),
            patch.object(registry._OPENER, "open", side_effect=lambda *_args, **_kwargs: Response(b"archive")),
        ):
            dependency_graphs.verify_graph(graph)
            dependency_graphs.verify_graph(graph)
            verify_source(RegistryRelease("cargo", "sample-crate", "1.2.3"))
            self.assertEqual(requests, [(CARGO_API, 0.0), (CARGO_API, 1.0), (CARGO_API, 2.0)])
            self.assertEqual(dependency_sources.HttpSourceProvider().read(Response.url), b"archive")
            verify_source(RegistryRelease("python", "sample-pkg", "1.2.3"))
            self.assertEqual(requests[-1], (PYTHON_API, 2.0))
            self.assertEqual(now[0], 2.0)

    def test_missing_policy_rejects_declarations_and_hidden_lock_dependencies_before_effects(self) -> None:
        for ecosystem in ("python", "cargo"):
            with self.subTest(ecosystem=ecosystem):
                (self.python if ecosystem == "python" else self.cargo)()
                (self.root / "dependency-sources.toml").unlink()
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "requires dependency-sources.toml"):
                    check_dependency_policy(self.root, provider=self.provider)
                with self.assertRaisesRegex(ValueError, "requires dependency-sources.toml"):
                    check_release_dependencies(self.root)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.provider.calls, [])
                self.native.assert_not_called()
                for filename in ("pyproject.toml", "uv.lock", "Cargo.toml", "Cargo.lock"):
                    (self.root / filename).unlink(missing_ok=True)
        (self.root / "Cargo.toml").write_bytes(b'[package]\nname="consumer"\nversion="0.1.0"\n')
        self.cargo_lock()
        with self.assertRaisesRegex(ValueError, "requires dependency-sources.toml"):
            check_dependency_policy(self.root, provider=self.provider)
        (self.root / "Cargo.lock").unlink()
        self.assertEqual(check_dependency_policy(self.root, provider=self.provider), ())
        (self.root / "Cargo.toml").write_bytes(b'[workspace]\nmembers=[]\n[workspace.package]\nversion="9.8.7"\n')
        self.assertEqual(check_dependency_policy(self.root, provider=self.provider), ())

    def test_release_identity_results_deduplicate_shared_packages_across_policy_projects(self) -> None:
        self.python()
        second = self.root / "second"
        second.mkdir()
        for name in ("pyproject.toml", "uv.lock"):
            (second / name).write_bytes((self.root / name).read_bytes())
        policy = self.root / "dependency-sources.toml"
        policy.write_bytes(policy.read_bytes() + b'[projects.second]\necosystem="python"\ndirectory="second"\n')
        with patch.object(dependency_sources, "HttpSourceProvider", return_value=self.provider):
            identities = check_release_dependencies(self.root)
        self.assertEqual([(item.name, item.version) for item in identities], [("sample-pkg", "1.2.3")])
        self.assertEqual(self.provider.calls, [PYTHON_API, PYTHON_API])
        self.assertEqual(self.native.call_count, 2)

    def test_missing_policy_accepts_dependency_free_root_with_inherited_cargo_version(self) -> None:
        (self.root / "Cargo.toml").write_bytes(b'[package]\nname="consumer"\nversion.workspace=true\n[workspace.package]\nversion="1.2.3"\n')
        lock = self.root / "Cargo.lock"
        lock.write_bytes(b'version=4\n[[package]]\nname="consumer"\nversion="1.2.3"\n')
        before = self.snapshot()
        self.assertEqual(check_release_dependencies(self.root), ())
        self.assertEqual(self.snapshot(), before)
        lock.write_bytes(b'version=4\n[[package]]\nname="consumer"\nversion="1.2.4"\n')
        with self.assertRaisesRegex(ValueError, "requires dependency-sources.toml"):
            check_release_dependencies(self.root)

    def test_non_directory_configuration_ancestor_is_absent_and_still_checked_for_staleness(self) -> None:
        cargo = self.root / ".cargo"
        cargo.write_bytes(b"not a configuration directory\n")
        config = cargo / "config.toml"
        snapshot = dependency_policy.InputSnapshot.capture((config,))
        self.assertEqual(snapshot.files, ((config, None),))
        with patch.object(dependency_policy.InputSnapshot, "capture", wraps=dependency_policy.InputSnapshot.capture) as capture:
            self.assertEqual(check_release_dependencies(self.root), ())
        self.assertTrue(all(path.is_relative_to(self.root) for call in capture.call_args_list for path in call.args[0]))
        cargo.unlink()
        cargo.mkdir()
        config.write_bytes(b"[net]\noffline=true\n")
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            snapshot.verify()

    def test_registry_provider_rejections_start_no_process_and_mutate_nothing(self) -> None:
        self.python()
        before = self.snapshot()
        for payload in (b"{}", b'{"info":null}', b'{"info":{},"info":{}}'):
            self.provider.responses[PYTHON_API] = payload
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                check_dependency_policy(self.root, provider=self.provider)
            self.native.assert_not_called()
            self.assertEqual(self.snapshot(), before)
        self.provider.python()
        check_dependency_policy(self.root, provider=self.provider)
        self.native.assert_called_once()

    def test_direct_transitive_alternate_target_and_override_sources_reject_before_effects(self) -> None:
        self.cargo()
        manifest = self.root / "Cargo.toml"
        original = manifest.read_bytes()
        additions = [
            f'[target.\'cfg(windows)\'.build-dependencies]\nother={{git="https://github.com/example/repo",rev="{COMMIT}",version="1.2.3"}}\n',
            '[dev-dependencies]\nother={path="../external",version="1.2.3"}\n',
            '[patch.crates-io]\nother={path="vendor"}\n',
            '[replace]\n"other:1.2.3"={path="vendor"}\n',
            '[workspace.dependencies]\nother={git="https://github.com/example/repo",version="1.2.3"}\n',
            '[build-dependencies]\nother={version="1.2.3",registry="private"}\n',
        ]
        for addition in additions:
            manifest.write_bytes(original + addition.encode())
            before = self.snapshot()
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                check_dependency_policy(self.root, provider=self.provider)
            self.assertEqual(self.snapshot(), before)
            self.native.assert_not_called()
        manifest.write_bytes(original)
        lock = self.root / "Cargo.lock"
        lock.write_bytes(
            lock.read_bytes()
            + f'\n[[package]]\nname="transitive"\nversion="1.0.0"\nsource="git+https://github.com/example/repo?rev={COMMIT}#{COMMIT}"\n'.encode()
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "every external Cargo"):
            check_dependency_policy(self.root, provider=self.provider)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.provider.calls, [])
        self.native.assert_not_called()

    def test_python_overrides_build_requirements_and_transitive_paths_cannot_hide(self) -> None:
        self.python()
        manifest = self.root / "pyproject.toml"
        original = manifest.read_bytes()
        for addition in (
            '[tool.uv.sources]\nsample-pkg={git="https://github.com/example/upstream",rev="' + COMMIT + '"}\n',
            "[project.optional-dependencies]\nother=[\"hidden @ https://example.test/archive.whl; sys_platform == 'win32'\"]\n",
            '[build-system]\nrequires=["unlocked-builder==1.0.0"]\n',
            '[[tool.uv.index]]\nurl="https://private.example/simple"\n',
        ):
            manifest.write_bytes(original + addition.encode())
            before = self.snapshot()
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                check_dependency_policy(self.root, provider=self.provider)
            self.assertEqual(self.snapshot(), before)
            self.native.assert_not_called()
        manifest.write_bytes(original)
        lock = self.root / "uv.lock"
        lock.write_bytes(lock.read_bytes() + b'\n[[package]]\nname="hidden"\nversion="1.0.0"\nsource={directory="vendor/hidden"}\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "every external Python"):
            check_dependency_policy(self.root, provider=self.provider)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.provider.calls, [])

    def test_version_identical_commit_policy_rejects_even_with_registry_lock(self) -> None:
        self.python()
        self.policy("python", git=True)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "development commit"):
            check_dependency_policy(self.root, provider=self.provider)
        with patch.object(dependency_sources, "HttpSourceProvider", return_value=self.provider):
            with self.assertRaisesRegex(ValueError, "development commit"):
                check_release_dependencies(self.root)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.provider.calls, [])
        self.native.assert_not_called()

    def test_preview_apply_commit_and_published_transition_preserve_options_and_evidence(self) -> None:
        for ecosystem in ("python", "cargo"):
            with self.subTest(ecosystem=ecosystem):
                (self.python if ecosystem == "python" else self.cargo)()
                self.policy(ecosystem, git=True)
                evidence = self.root / "rehearsal.json"
                evidence.write_bytes(b'{"old":"retained"}\r\n')
                original = self.snapshot()

                def resolve(root: Path, verified: VerifiedSource, *, checkout: Path, candidate_checkout: Path) -> None:
                    self.assertEqual(checkout, self.root)
                    self.assertTrue(root.is_relative_to(candidate_checkout))
                    (self.python_lock if ecosystem == "python" else self.cargo_lock)(root=root, git=isinstance(verified.source, GitCommit))

                with patch.object(dependency_workflows, "_resolve_candidate", side_effect=resolve):
                    plan = plan_source(self.root, "upstream", provider=self.provider)
                self.assertEqual(self.snapshot(), original)
                self.assertIn(COMMIT, plan.diff())
                self.assertIn("lock", plan.diff())
                apply_source(plan, provider=self.provider)
                manifest = self.root / ("pyproject.toml" if ecosystem == "python" else "Cargo.toml")
                data = tomllib.loads(manifest.read_text(encoding="utf-8"))
                if ecosystem == "python":
                    self.assertEqual(data["tool"]["uv"]["sources"]["sample-pkg"], {"git": "https://github.com/example/upstream.git", "rev": COMMIT})
                    self.assertEqual(data["project"]["dependencies"], ['sample-pkg[feature]==1.2.3; python_version >= "3.15"'])
                else:
                    dep = data["dependencies"]["renamed"]
                    self.assertEqual(
                        dep,
                        {
                            "package": "sample-crate",
                            "version": "=1.2.3",
                            "features": ["extra"],
                            "optional": True,
                            "git": "https://github.com/example/upstream.git",
                            "rev": COMMIT,
                        },
                    )
                self.assertIn(b"\r\n", manifest.read_bytes())
                self.assertEqual(evidence.read_bytes(), b'{"old":"retained"}\r\n')
                self.policy(ecosystem)
                with self.assertRaisesRegex(ValueError, "changed"):
                    apply_source(plan, provider=self.provider)
                with patch.object(dependency_workflows, "_resolve_candidate", side_effect=resolve):
                    published = plan_source(self.root, "upstream", provider=self.provider)
                apply_source(published, provider=self.provider)
                self.assertNotIn(COMMIT.encode(), manifest.read_bytes())
                self.assertEqual(evidence.read_bytes(), b'{"old":"retained"}\r\n')
                check_dependency_policy(self.root, provider=self.provider)

    def test_failed_resolution_concurrent_change_and_publication_failure_preserve_inputs(self) -> None:
        self.python()
        self.policy("python", git=True)
        original = self.snapshot()
        with patch.object(dependency_workflows, "_resolve_candidate", side_effect=ValueError("resolution failed")):
            with self.assertRaisesRegex(ValueError, "resolution failed"):
                plan_source(self.root, "upstream", provider=self.provider)
        self.assertEqual(self.snapshot(), original)
        with patch.object(dependency_workflows, "_resolve_candidate", side_effect=lambda root, verified, **_kwargs: self.python_lock(root=root, git=True)):
            plan = plan_source(self.root, "upstream", provider=self.provider)
        marker = self.root / "concurrent.txt"
        marker.write_bytes(b"user bytes")
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            apply_source(plan, provider=self.provider)
        marker.unlink()
        real_replace = files._replace_path
        attempts = 0

        def fail_second(source: Path, target: Path) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise OSError("publication failed")
            real_replace(source, target)

        with patch.object(files, "_replace_path", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "publication failed"):
                apply_source(plan, provider=self.provider)
        self.assertEqual(self.snapshot(), original)
        apply_source(plan, provider=self.provider)
        self.assertIn(COMMIT.encode(), (self.root / "uv.lock").read_bytes())

    def test_snapshot_limits_reject_oversized_and_aggregate_inputs_before_copying(self) -> None:
        self.python()
        self.policy("python", git=True)
        data = self.root / "untracked.data"
        data.write_bytes(b"x" * 2049)
        (self.root / ".gitignore").write_bytes(b"*.data\n")
        before = self.snapshot()
        with patch.object(dependency_policy, "MAX_SNAPSHOT_FILE_BYTES", 2048), patch.object(dependency_workflows, "_resolve_candidate") as resolve:
            with self.assertRaisesRegex(ValueError, "byte limit"):
                plan_source(self.root, "upstream", provider=self.provider)
            resolve.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        data.write_bytes(b"x" * 128)
        aggregate = sum(len(content) for content in self.snapshot().values())
        with patch.object(dependency_policy, "MAX_SNAPSHOT_BYTES", aggregate - 1), patch.object(dependency_workflows, "_resolve_candidate") as resolve:
            with self.assertRaisesRegex(ValueError, "byte limit"):
                plan_source(self.root, "upstream", provider=self.provider)
            resolve.assert_not_called()
        with patch.object(dependency_workflows, "MAX_SNAPSHOT_FILES", 2):
            with self.assertRaisesRegex(ValueError, "entry-count limit"):
                plan_source(self.root, "upstream", provider=self.provider)

        def resolve(root: Path, _verified: VerifiedSource, **_kwargs: object) -> None:
            self.assertEqual((root / "untracked.data").read_bytes(), b"x" * 128)
            self.python_lock(root=root, git=True)

        with patch.object(dependency_workflows, "_resolve_candidate", side_effect=resolve):
            plan = plan_source(self.root, "upstream", provider=self.provider)
        data.write_bytes(b"y" * 128)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            apply_source(plan, provider=self.provider)
        self.assertEqual(self.snapshot(), before)

    def test_source_copy_keeps_declared_workspaces_inside_normally_excluded_directories(self) -> None:
        self.cargo()
        self.policy("cargo", git=True)
        manifest = self.root / "Cargo.toml"
        manifest.write_bytes(manifest.read_bytes() + b'[workspace]\nmembers=["target/support"]\n')
        member = self.root / "target/support"
        member.mkdir(parents=True)
        (member / "Cargo.toml").write_bytes(b'[package]\nname="support"\nversion="0.1.0"\n')
        (member / "source.rs").write_bytes(b"// resolver input\r\n")

        def resolve(root: Path, _verified: VerifiedSource, **_kwargs: object) -> None:
            self.assertEqual((root / "target/support/source.rs").read_bytes(), b"// resolver input\r\n")
            self.cargo_lock(root=root, git=True)

        with patch.object(dependency_workflows, "_resolve_candidate", side_effect=resolve):
            plan = plan_source(self.root, "upstream", provider=self.provider)
        self.assertIn(member / "source.rs", dict(plan.snapshot.files))

    def test_resolver_preserves_managed_homes_but_excludes_overrides_and_credentials(self) -> None:
        self.provider.git("cargo")
        proof = verify_source(GitCommit("cargo", "sample-crate", "example/upstream", COMMIT), provider=self.provider)
        settings = {
            "CARGO_HOME": str(self.root / "managed cargo"),
            "RUSTUP_HOME": str(self.root / "managed rustup"),
            "RUSTUP_TOOLCHAIN": "managed-toolchain",
            "CARGO_BUILD_RUSTC_WRAPPER": "untrusted-wrapper",
            "RUSTFLAGS": "untrusted-flags",
            "GITHUB_TOKEN": "secret",
            "GH_TOKEN": "secret",
            "RRT_GITHUB_LOOKUP_TOKEN": "secret",
            "UV_INDEX_URL": "https://example.invalid",
            "PATH": str(self.root) + os.pathsep + os.environ.get("PATH", ""),
        }
        with (
            patch.dict(os.environ, settings),
            patch.object(process, "resolve_executable", return_value=Path(sys.executable)),
            patch.object(process, "run_command", return_value=subprocess.CompletedProcess([], 0, str(Path(sys.executable)), "")) as run,
        ):
            dependency_workflows._resolve_candidate(self.root, proof, checkout=self.root, candidate_checkout=self.root)
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["CARGO_HOME"], settings["CARGO_HOME"])
        self.assertEqual(environment["RUSTUP_HOME"], settings["RUSTUP_HOME"])
        self.assertEqual(environment["RUSTUP_TOOLCHAIN"], settings["RUSTUP_TOOLCHAIN"])
        self.assertFalse(set(settings) - {"CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN", "PATH"} & environment.keys())
        self.assertNotIn(str(self.root), environment["PATH"].split(os.pathsep))

    def test_native_rustup_cargo_selection_rejects_both_checkouts_and_accepts_external_toolchains(self) -> None:
        self.cargo()
        proof = verify_source(GitCommit("cargo", "sample-crate", "example/upstream", COMMIT), provider=self.provider)
        marker = self.root / "cargo-ran"
        with tempfile.TemporaryDirectory(prefix="cargo selection café ") as directory:
            arena = Path(directory).resolve()
            candidate = arena / "checkout"
            project = candidate / "project"
            project.mkdir(parents=True)
            (project / "Cargo.toml").write_bytes(b'[package]\nname="consumer"\nversion="0.1.0"\n')
            trusted = arena / "trusted"
            (trusted / "bin").mkdir(parents=True)
            binary_name = "cargo.exe" if sys.platform == "win32" else "cargo"
            binary = trusted / "bin" / binary_name
            program = arena / "probe.rs"
            program.write_bytes(b'fn main() { std::fs::write(std::env::var("RRT_CARGO_PROBE_MARKER").unwrap(), b"selected").unwrap(); }\n')
            process.run_command("rustc", [str(program), "-o", str(binary)], cwd=arena)
            for toolchain in (self.root / "toolchain", candidate / "toolchain", trusted):
                if toolchain != trusted:
                    (toolchain / "bin").mkdir(parents=True)
                    shutil.copy2(binary, toolchain / "bin" / binary_name)
                with self.subTest(toolchain=toolchain), patch.dict(os.environ, {"RUSTUP_TOOLCHAIN": str(toolchain), "RRT_CARGO_PROBE_MARKER": str(marker)}):
                    if toolchain == trusted:
                        dependency_workflows._resolve_candidate(project, proof, checkout=self.root, candidate_checkout=candidate)
                        self.assertEqual(marker.read_bytes(), b"selected")
                        marker.unlink()
                    else:
                        with self.assertRaisesRegex(ValueError, "outside dependency checkouts"):
                            dependency_workflows._resolve_candidate(project, proof, checkout=self.root, candidate_checkout=candidate)
                        self.assertFalse(marker.exists())
                        if toolchain.is_relative_to(self.root):
                            graph = dependency_graphs.inspect_project(self.root, DependencyProject("cargo"))
                            with self.assertRaisesRegex(ValueError, "outside dependency checkouts"):
                                dependency_graphs.check_native_graph(graph)
                            self.assertFalse(marker.exists())

    def test_cli_preview_and_release_gate_share_the_public_policy(self) -> None:
        self.python()
        self.policy("python", git=True)
        before = self.snapshot()
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(dependency_sources, "HttpSourceProvider", return_value=self.provider),
            patch.object(dependency_workflows, "_resolve_candidate", side_effect=lambda root, verified, **_kwargs: self.python_lock(root=root, git=True)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(cli.main(["--root", str(self.root), "deps", "source", "upstream"]), 0)
            self.assertEqual(cli.main(["--root", str(self.root), "release", "dependencies"]), 1)
        self.assertIn(COMMIT, stdout.getvalue())
        self.assertIn("development commit", stderr.getvalue())
        self.assertEqual(self.snapshot(), before)

    def test_policy_rejects_competing_or_ambiguous_boundaries(self) -> None:
        for data in (
            {"schema": 1, "projects": {}},
            {"schema": True, "projects": {"main": {"ecosystem": "cargo"}}},
            {"schema": 1, "projects": {"main": {"ecosystem": "npm"}}},
            {"schema": 1, "projects": {"main": {"ecosystem": "cargo", "release-owned": [".", "."]}}},
            {"schema": 1, "projects": {"one": {"ecosystem": "cargo"}, "two": {"ecosystem": "cargo"}}},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_policy(data)
        self.assertIsNone(load_policy(self.root))

    def test_native_uv_and_cargo_check_locks_without_startup_or_build_hooks(self) -> None:
        (self.root / "pyproject.toml").write_bytes(b'[project]\nname="consumer"\nversion="0.1.0"\nrequires-python=">=3.15"\n[tool.uv]\npackage=false\n')
        (self.root / "uv.lock").write_bytes(
            b'version=1\nrevision=3\nrequires-python=">=3.15"\n[[package]]\nname="consumer"\nversion="0.1.0"\nsource={virtual="."}\n'
        )
        (self.root / "sitecustomize.py").write_bytes(b'raise RuntimeError("consumer startup must never run")\n')
        (self.root / "Cargo.toml").write_bytes(b'[package]\nname="consumer"\nversion="0.1.0"\nedition="2024"\n')
        (self.root / "Cargo.lock").write_bytes(b'version=4\n[[package]]\nname="consumer"\nversion="0.1.0"\n')
        (self.root / "src").mkdir()
        (self.root / "src/lib.rs").write_bytes(b'compile_error!("inspection must not compile");\n')
        (self.root / "build.rs").write_bytes(b'compile_error!("inspection must not build");\n')
        (self.root / "rust-toolchain.toml").write_bytes(b'[toolchain]\nchannel="must-not-select-checkout-toolchain"\n')
        toolchain = process.run_command("rustup", ["show", "active-toolchain"], cwd=self.root.parent).stdout.strip().split(" (", 1)[0]
        launcher = {
            "UV_RUN_RECURSION_DEPTH": "2",
            "UV_TOOL_BIN_DIR": str(self.root / "tool launchers"),
            "UV_TOOL_DIR": str(self.root / "tool environments"),
            "RUSTUP_TOOLCHAIN": toolchain,
            "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
        }
        before = self.snapshot()
        for ecosystem in ("python", "cargo"):
            with self.subTest(ecosystem=ecosystem), patch.dict(os.environ, launcher), patch.object(process, "run_command", wraps=process.run_command) as run:
                graph = dependency_graphs.inspect_project(self.root, DependencyProject(ecosystem))
                dependency_graphs.check_native_graph(graph)
                self.assertEqual(self.snapshot(), before)
                if ecosystem == "cargo":
                    self.assertEqual(run.call_args.kwargs["env"]["RUSTUP_HOME"], launcher["RUSTUP_HOME"])
                    self.assertEqual(run.call_args.kwargs["env"]["RUSTUP_TOOLCHAIN"], toolchain)
                override = (
                    {"UV_INDEX_URL": "https://example.invalid"} if ecosystem == "python" else {"CARGO_REGISTRIES_CRATES_IO_INDEX": "https://example.invalid"}
                )
                with patch.dict(os.environ, override), self.assertRaisesRegex(ValueError, "environment override"):
                    dependency_graphs.inspect_project(self.root, DependencyProject(ecosystem))

    def test_native_cargo_graph_and_published_archive_detect_cache_tampering(self) -> None:
        self.cargo()
        cached = self.root / "registry cache"
        cached.mkdir()
        content = {"Cargo.toml": b'[package]\nname="sample-crate"\nversion="1.2.3"\n', "src/lib.rs": b"pub fn value() -> u8 { 42 }\r\n"}
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, payload in content.items():
                member = tarfile.TarInfo("sample-crate-1.2.3/" + name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
                path = cached / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
        payload = stream.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        self.provider.cargo(checksum=digest, crate_size=len(payload))
        self.provider.responses["https://static.crates.io/crates/sample-crate/sample-crate-1.2.3.crate"] = payload
        lock = self.root / "Cargo.lock"
        lock.write_bytes(lock.read_bytes().replace(DIGEST.encode(), digest.encode()))
        graph = dependency_graphs.inspect_project(self.root, DependencyProject("cargo"))
        verified = dependency_graphs.verify_graph(graph, provider=self.provider)
        metadata = {
            "workspace_root": str(self.root),
            "packages": [
                {"id": "consumer", "name": "consumer", "version": "0.1.0", "source": None, "manifest_path": str(self.root / "Cargo.toml")},
                {
                    "id": "upstream",
                    "name": "sample-crate",
                    "version": "1.2.3",
                    "source": dependency_graphs.CRATES_IO,
                    "manifest_path": str(cached / "Cargo.toml"),
                },
            ],
            "resolve": {"nodes": [{"id": "consumer", "dependencies": ["upstream"]}, {"id": "upstream", "dependencies": []}]},
        }
        with (
            patch.object(dependency_graphs, "_resolve_cargo", return_value=Path(sys.executable)),
            patch.object(process, "run_command", return_value=subprocess.CompletedProcess([], 0, json.dumps(metadata), "")) as command,
        ):
            dependency_graphs.check_native_graph(graph, sources=verified, provider=self.provider)
            arguments = command.call_args.args[1]
            self.assertIn("--all-features", arguments)
            self.assertIn("--locked", arguments)
            self.assertIn("--offline", arguments)
            self.assertNotIn("--filter-platform", arguments)
            (cached / "src/lib.rs").write_bytes(b"// unpublished change with identical version\n")
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, "differs from published bytes"):
                dependency_graphs.check_native_graph(graph, sources=verified, provider=self.provider)
            self.assertEqual(self.snapshot(), before)
            (cached / "src/lib.rs").write_bytes(content["src/lib.rs"])
            (cached / "unpublished.rs").write_bytes(b"// unlisted source\n")
            with self.assertRaisesRegex(ValueError, "unpublished files"):
                dependency_graphs.check_native_graph(graph, sources=verified, provider=self.provider)

    def test_workspace_ownership_requires_explicit_private_members_and_registry_order(self) -> None:
        self.cargo()
        manifest = self.root / "Cargo.toml"
        manifest.write_bytes(manifest.read_bytes() + b'[workspace]\nmembers=["support"]\n')
        support = self.root / "support"
        support.mkdir()
        (support / "Cargo.toml").write_bytes(b'[package]\nname="support"\nversion="1.0.0"\npublish=false\n')
        with manifest.open("ab") as output:
            output.write(b'[dev-dependencies]\nsupport={path="support",version="1.0.0"}\n')
        graph = dependency_graphs.inspect_project(self.root, DependencyProject("cargo", owned=(".", "support")))
        self.assertEqual(tuple(name for name, _, _ in graph.owned), ("consumer", "support"))
        self.assertEqual(tuple(item.source.package for item in graph.dependencies), ("sample-crate",))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "external path"):
            dependency_graphs.inspect_project(self.root, DependencyProject("cargo"))
        self.assertEqual(self.snapshot(), before)
        (support / "Cargo.toml").write_bytes(b'[package]\nname="support"\nversion="1.0.0"\n')
        with self.assertRaisesRegex(ValueError, "upstream-first"):
            dependency_graphs.inspect_project(self.root, DependencyProject("cargo", owned=(".", "support")))

    def test_installed_python_inputs_match_published_wheel_without_importing_package(self) -> None:
        self.python()
        environment = self.root / ".venv"
        site = environment / ("Lib/site-packages" if os.name == "nt" else "lib/python3.15/site-packages")
        site.mkdir(parents=True)
        (environment / "pyvenv.cfg").write_bytes(b"include-system-site-packages = false\n")
        dist = "sample_pkg-1.2.3.dist-info"
        contents = {
            "sample_pkg/__init__.py": b'raise RuntimeError("must never import consumer code")\r\n',
            f"{dist}/METADATA": b"Metadata-Version: 2.4\nName: sample-pkg\nVersion: 1.2.3\n",
            f"{dist}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        contents[f"{dist}/RECORD"] = "".join(f"{name},,\n" for name in (*contents, f"{dist}/RECORD")).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, payload in contents.items():
                archive.writestr(name, payload)
                path = site / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        self.provider.python(digests={"sha256": digest}, size=len(payload))
        lock = self.root / "uv.lock"
        lock.write_bytes(lock.read_bytes().replace(DIGEST.encode(), digest.encode()).replace(b"size=12", f"size={len(payload)}".encode()))
        graph = dependency_graphs.inspect_project(self.root, DependencyProject("python"))

        class Response(io.BytesIO):
            status = 200
            url = PYTHON_URL

        with (
            patch.object(registry, "_json", return_value=json.loads(self.provider.responses[PYTHON_API])),
            patch.object(registry._OPENER, "open", side_effect=lambda *_args, **_kwargs: Response(payload)),
        ):
            before = self.snapshot()
            dependency_artifacts.verify_python_environment(graph)
            self.assertEqual(self.snapshot(), before)
            legacy = (Path(__file__).parent / "fixtures/uv-0.8.0-virtualenv.txt").read_bytes()
            (site / "_virtualenv.py").write_bytes(legacy)
            (site / "_virtualenv.pth").write_bytes(b"import _virtualenv")
            before = self.snapshot()
            dependency_artifacts.verify_python_environment(graph)
            self.assertEqual(self.snapshot(), before)
            for filename, unsafe in (("_virtualenv.py", legacy + b'\nraise RuntimeError("injected")\n'), ("_virtualenv.pth", b"import arbitrary_hook\n")):
                target = site / filename
                original = target.read_bytes()
                target.write_bytes(unsafe)
                before = self.snapshot()
                with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, "unrecognized legacy uv startup hooks"):
                    dependency_artifacts.verify_python_environment(graph)
                self.assertEqual(self.snapshot(), before)
                target.write_bytes(original)
            hook = site / "injected.pth"
            hook.write_bytes(b"import unpublished_hook\n")
            with self.assertRaisesRegex(ValueError, "startup hooks"):
                dependency_artifacts.verify_python_environment(graph)
            hook.unlink()
            installed = site / "sample_pkg/__init__.py"
            installed.write_bytes(b"# same version, unpublished bytes\n")
            with self.assertRaisesRegex(ValueError, "differ from the published wheel"):
                dependency_artifacts.verify_python_environment(graph)
            installed.write_bytes(contents["sample_pkg/__init__.py"])
            (site / dist / "direct_url.json").write_bytes(b'{"url":"https://github.com/example/upstream"}')
            with self.assertRaisesRegex(ValueError, "development provenance"):
                dependency_artifacts.verify_python_environment(graph)

    def test_transitive_cargo_commit_patch_has_an_explicit_registry_transition(self) -> None:
        self.cargo()
        manifest = self.root / "Cargo.toml"
        manifest.write_bytes(b'[package]\nname="consumer"\nversion="0.1.0"\n')
        self.policy("cargo", git=True)
        with patch.object(dependency_workflows, "_resolve_candidate", side_effect=lambda root, verified, **_kwargs: self.cargo_lock(root=root, git=True)):
            plan = plan_source(self.root, "upstream", provider=self.provider)
        apply_source(plan, provider=self.provider)
        patch_data = tomllib.loads(manifest.read_text(encoding="utf-8"))["patch"]["crates-io"]["sample-crate"]
        self.assertEqual(patch_data, {"git": "https://github.com/example/upstream.git", "rev": COMMIT, "version": "=1.2.3"})
        self.policy("cargo")
        with patch.object(dependency_workflows, "_resolve_candidate", side_effect=lambda root, verified, **_kwargs: self.cargo_lock(root=root)):
            plan = plan_source(self.root, "upstream", provider=self.provider)
        apply_source(plan, provider=self.provider)
        self.assertEqual(tomllib.loads(manifest.read_text(encoding="utf-8"))["patch"]["crates-io"], {})
        check_dependency_policy(self.root, provider=self.provider)

    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "disposable Git mutations disabled")
    def test_native_cargo_resolves_registry_to_git_and_git_to_registry(self) -> None:
        upstream = self.root / "upstream"
        vendor = self.root / "vendor/sample-crate"
        consumer = self.root / "consumer"
        cargo_home = self.root / "cargo home"
        payloads = {"Cargo.toml": b'[package]\nname="sample-crate"\nversion="1.2.3"\nedition="2021"\n', "src/lib.rs": b'compile_error!("must not build");\n'}
        for directory in (upstream, vendor):
            for name, content in payloads.items():
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        (vendor / ".cargo-checksum.json").write_bytes(
            json.dumps({"package": DIGEST, "files": {name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()}}).encode()
        )
        consumer.mkdir()
        (consumer / "src").mkdir()
        (consumer / "src/lib.rs").write_bytes(b'compile_error!("must not build consumer");\n')
        manifest = consumer / "Cargo.toml"
        manifest.write_bytes(b'[package]\nname="consumer"\nversion="0.1.0"\nedition="2021"\n[dependencies]\nsample-crate="=1.2.3"\n')
        cargo_home.mkdir()
        # Hermetic registry transport for the native resolver; release-policy
        # tests separately reject this source replacement in real consumers.
        (cargo_home / "config.toml").write_bytes(
            (
                '[net]\ngit-fetch-with-cli=true\n[source.crates-io]\nreplace-with="fixture"\n[source.fixture]\ndirectory='
                + json.dumps(str(vendor.parent))
                + "\n"
            ).encode()
        )
        git_config = self.root / "gitconfig"
        git_config.write_bytes((f'[url "{upstream.as_uri()}"]\n\tinsteadOf = https://github.com/example/upstream.git\n').encode())
        environment = {
            "CARGO_HOME": str(cargo_home),
            "RUSTUP_TOOLCHAIN": process.run_command("rustup", ["show", "active-toolchain"], cwd=self.root.parent).stdout.strip().split(" (", 1)[0],
            "GIT_CONFIG_GLOBAL": str(git_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file",
        }
        with patch.dict(os.environ, environment):
            process.run_command("git", ["init", "--quiet"], cwd=upstream)
            process.run_command("git", ["add", "."], cwd=upstream)
            process.run_command(
                "git",
                ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture"],
                cwd=upstream,
            )
            commit = process.run_command("git", ["--no-pager", "rev-parse", "HEAD"], cwd=upstream).stdout.strip()
            process.run_command("cargo", ["generate-lockfile", "--manifest-path", str(manifest), "--offline"], cwd=self.root.parent)
            self.provider.git("cargo", commit=commit)
            selections = (GitCommit("cargo", "sample-crate", "example/upstream", commit), RegistryRelease("cargo", "sample-crate", "1.2.3"))
            for source in selections:
                with self.subTest(source=source):
                    proof = verify_source(source, provider=self.provider)
                    manifest.write_text(dependency_workflows._cargo_selection(manifest.read_text(encoding="utf-8"), proof), encoding="utf-8", newline="\n")
                    try:
                        dependency_workflows._resolve_candidate(consumer, proof, checkout=consumer, candidate_checkout=consumer)
                    except subprocess.CalledProcessError as error:
                        self.fail(f"native Cargo transition failed: {error.stderr!r}")
                    packages = tomllib.loads((consumer / "Cargo.lock").read_text(encoding="utf-8"))["package"]
                    locked = next(item for item in packages if item["name"] == "sample-crate")
                    self.assertEqual(locked["version"], "1.2.3")
                    if isinstance(source, GitCommit):
                        self.assertEqual(locked["source"], f"git+{source.url}?rev={commit}#{commit}")
                    else:
                        self.assertEqual(locked["source"], dependency_graphs.CRATES_IO)
                        self.assertEqual(locked["checksum"], DIGEST)


if __name__ == "__main__":
    unittest.main()
