"""Native source builds and authenticated receipt regressions, also run installed."""

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import override
from unittest.mock import Mock, patch

from research_repo_tools import dependency_build_receipts, dependency_builds, dependency_graphs, dependency_provenance
from research_repo_tools._dependency_probe import distribution_inventory, inspect_distribution, inspect_installation
from research_repo_tools.dependency_artifacts import verify_python_environment
from research_repo_tools.dependency_build_policy import parse_build_policy
from research_repo_tools.dependency_build_receipts import verify_build_receipt
from research_repo_tools.dependency_builds import prepare_dependency_builds
from research_repo_tools.dependency_policy import DependencyProject, load_policy
from research_repo_tools.dependency_provenance import verify_wheel_payload
from research_repo_tools.process import run_safe_command

SOURCE_URL = "https://files.pythonhosted.org/packages/fixture/sample_pkg-1.2.3.tar.gz"
API_URL = "https://pypi.org/pypi/sample-pkg/1.2.3/json"
PAYLOAD = b"native\r\nbytes\xff\x00\n"


def wheel_bytes() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("sample_pkg-1.2.3.dist-info/METADATA", "Metadata-Version: 2.4\nName: sample-pkg\nVersion: 1.2.3\n")
        archive.writestr("sample_pkg-1.2.3.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("sample_pkg-1.2.3.dist-info/RECORD", "")
        archive.writestr("sample_pkg-1.2.3.data/purelib/sample_pkg.py", b"VALUE = 42\n")
        archive.writestr("_vendor/other-1.0.dist-info/METADATA", b"Name: other\nVersion: 1.0\n")
        archive.writestr("_vendor/other-1.0.dist-info/RECORD", b"vendored record is payload\n")
        archive.writestr("sample_pkg-1.2.3.data/scripts/native-tool.exe", PAYLOAD)
        archive.writestr("sample_pkg-1.2.3.data/data/share/sample/kernel.json", b'{"argv":["python"]}\n')
    return stream.getvalue()


class Provider:
    def __init__(self, source: bytes) -> None:
        self.source = source

    def read(self, url: str) -> bytes:
        if url == SOURCE_URL:
            return self.source
        if url != API_URL:
            raise AssertionError(f"unexpected network lookup: {url}")
        return json.dumps(
            {
                "info": {"name": "sample-pkg", "version": "1.2.3"},
                "urls": [
                    {
                        "filename": "sample_pkg-1.2.3.tar.gz",
                        "url": SOURCE_URL,
                        "size": len(self.source),
                        "digests": {"sha256": hashlib.sha256(self.source).hexdigest()},
                        "yanked": False,
                        "packagetype": "sdist",
                    }
                ],
            }
        ).encode()


class TestSourceBuilds(unittest.TestCase):
    @override
    def setUp(self) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="source builds caf\u00e9 "))
        self.root = Path(temporary) / "checkout"
        self.root.mkdir()
        self.store = Path(temporary) / "private store"
        self.enterContext(patch.dict(os.environ, {"RESEARCH_REPO_TOOLS_BUILD_STORE": str(self.store)}))
        self.enterContext(patch.dict(os.environ, {key: value for key, value in os.environ.items() if not key.startswith("UV_")}, clear=True))
        (self.root / "pyproject.toml").write_bytes(
            b'[project]\nname="consumer"\nversion="0.1.0"\nrequires-python=">=3.15"\ndependencies=["sample-pkg==1.2.3"]\n'
            b"[tool.uv]\npackage=false\n[dependency-groups]\nbuild=[]\n"
        )
        (self.root / "dependency-sources.toml").write_bytes(
            b'schema=1\n[projects.python]\necosystem="python"\n[source-builds]\npackages=["sample-pkg"]\ngroup="build"\n'
        )
        wheel = wheel_bytes()
        self.wheel = wheel
        backend = (
            "from pathlib import Path\n"
            "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
            "    name = 'sample_pkg-1.2.3-py3-none-any.whl'\n"
            "    Path(wheel_directory, name).write_bytes(Path('payload.whl').read_bytes())\n"
            "    return name\n"
        ).encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, data in {
                "pyproject.toml": b'[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n',
                "backend.py": backend,
                "payload.whl": wheel,
            }.items():
                member = tarfile.TarInfo("sample_pkg-1.2.3/" + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        source = stream.getvalue()
        self.source: dict[str, object] = {"url": SOURCE_URL, "size": len(source), "hash": "sha256:" + hashlib.sha256(source).hexdigest()}
        (self.root / "uv.lock").write_bytes(
            (
                'version=1\nrevision=3\nrequires-python=">=3.15"\n[[package]]\nname="consumer"\nversion="0.1.0"\nsource={virtual="."}\n'
                '[[package]]\nname="sample-pkg"\nversion="1.2.3"\nsource={registry="https://pypi.org/simple"}\n'
                f'sdist={{url="{SOURCE_URL}",size={len(source)},hash="{self.source["hash"]}"}}\n'
            ).encode()
        )
        self.provider = Provider(source)
        policy = load_policy(self.root)
        assert policy is not None
        self.graph = dependency_graphs.inspect_project(self.root, policy.projects[0])

    def build(self) -> tuple[Path, ...]:
        run_safe_command("uv", ["venv", "--no-config", "--no-python-downloads", "--python", sys.executable, str(self.root / ".venv")])

        def isolated(root, temporary, policy, uv):
            # Replace only registry build-tool acquisition: this fixture's backend
            # has no dependencies. uv still builds and installs the actual sdist.
            build_env = temporary / "environment"
            run_safe_command(str(uv), ["venv", "--no-config", "--no-python-downloads", "--python", sys.executable, str(build_env)])
            python = build_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            return python, dict(os.environ), {"installed": {}, "fixture": "stdlib-only PEP 517 backend"}

        with patch.object(dependency_builds, "_build_environment", side_effect=isolated):
            return prepare_dependency_builds(self.root, provider=self.provider)

    def test_real_sdist_build_install_and_tampering_fail_closed(self) -> None:
        wheels = self.build()
        self.assertEqual([path.name for path in wheels], ["sample_pkg-1.2.3-py3-none-any.whl"])
        receipt = verify_build_receipt(self.root, "sample-pkg", "1.2.3", self.source)
        self.assertEqual(receipt.payload, self.wheel)
        scripts = self.root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        self.assertEqual((scripts / "native-tool.exe").read_bytes(), PAYLOAD)
        self.assertEqual((scripts.parent / "share/sample/kernel.json").read_bytes(), b'{"argv":["python"]}\n')
        verify_python_environment(self.graph)
        (scripts / "native-tool.exe").write_bytes(PAYLOAD.replace(b"\r\n", b"\n"))
        with self.assertRaisesRegex(ValueError, "installed script differs"):
            verify_python_environment(self.graph)
        (scripts / "native-tool.exe").write_bytes(PAYLOAD)
        data = scripts.parent / "share/sample/kernel.json"
        data.write_bytes(b'{"argv":["untrusted"]}\n')
        with self.assertRaisesRegex(ValueError, "differs"):
            verify_python_environment(self.graph)
        data.write_bytes(b'{"argv":["python"]}\n')
        site = self.root / ".venv/Lib/site-packages" if os.name == "nt" else next((self.root / ".venv/lib").glob("python*/site-packages"))
        vendored = site / "_vendor/other-1.0.dist-info/RECORD"
        vendored.write_bytes(b"changed vendored payload\n")
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            verify_python_environment(self.graph)
        vendored.write_bytes(b"vendored record is payload\n")
        wheels[0].write_bytes(b"untrusted wheel")
        with self.assertRaisesRegex(ValueError, "differs from authenticated receipt"):
            verify_build_receipt(self.root, "sample-pkg", "1.2.3", self.source)

    def test_receipt_authentication_and_live_inputs_are_required(self) -> None:
        self.build()
        path = next(self.store.glob("*.json"))
        original = path.read_bytes()
        receipt = json.loads(original)
        receipt["payload"]["tools"] = {"forged": True}
        path.write_text(json.dumps(receipt), encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            verify_python_environment(self.graph)
        path.write_bytes(original)
        for filename in ("pyproject.toml", "uv.lock", "dependency-sources.toml"):
            with self.subTest(input=filename):
                target = self.root / filename
                saved = target.read_bytes()
                target.write_bytes(saved + b"\n# changed\n")
                with self.assertRaisesRegex(ValueError, "stale"):
                    verify_build_receipt(self.root, "sample-pkg", "1.2.3", self.source)
                target.write_bytes(saved)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            verify_python_environment(self.graph)

    def test_bad_source_hash_never_installs_or_issues_receipt(self) -> None:
        self.provider.source += b"tampered"
        with patch.object(dependency_builds, "_build_environment") as build, self.assertRaisesRegex(ValueError, "differs|missing"):
            prepare_dependency_builds(self.root, provider=self.provider)
        build.assert_not_called()
        self.assertFalse(self.store.exists())
        self.assertFalse((self.root / ".venv").exists())

    def test_receipt_store_cannot_overlap_checkout_or_be_relative(self) -> None:
        for path in (Path("relative"), self.root, self.root / "store", self.root.parent):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "absolute|separate"):
                dependency_build_receipts.build_store(self.root, path)


class TestBuildPolicy(unittest.TestCase):
    def test_environment_inventory_is_linear_and_rechecks_live_identities(self) -> None:
        for damage in (None, "duplicate", "added", "removed", "changed"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                environment = root / ".venv"
                site = environment / ("Lib/site-packages" if os.name == "nt" else "lib/python3.15/site-packages")
                site.mkdir(parents=True)
                (environment / "pyvenv.cfg").write_bytes(b"include-system-site-packages = false\n")
                packages = [f"sample{index}" for index in range(8)]
                dependencies = ",".join(f'"{name}==1.0"' for name in packages)
                (root / "pyproject.toml").write_bytes(
                    f'[project]\nname="consumer"\nversion="1.0"\nrequires-python=">=3.15"\ndependencies=[{dependencies}]\n'.encode()
                )
                lock = 'version=1\n[[package]]\nname="consumer"\nversion="1.0"\nsource={virtual="."}\n'
                expected = {}
                wheels = {}
                for name in packages:
                    lock += f'[[package]]\nname="{name}"\nversion="1.0"\nsource={{registry="https://pypi.org/simple"}}\n'
                    metadata = f"{name}-1.0.dist-info"
                    (site / metadata).mkdir()
                    contents = {
                        f"{name}.py": b'raise RuntimeError("must never import consumer code")\r\n',
                        f"{metadata}/METADATA": f"Name: {name}\nVersion: 1.0\n".encode(),
                        f"{metadata}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
                    }
                    record = f"{metadata}/RECORD"
                    contents[record] = "".join(f"{path},,\n" for path in (*contents, record)).encode()
                    for path, data in contents.items():
                        (site / path).write_bytes(data)
                    expected[name] = {path: hashlib.sha256(data).hexdigest() for path, data in contents.items()}
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, "w") as archive:
                        for path, data in contents.items():
                            archive.writestr(path, data)
                    wheels[name] = buffer.getvalue()
                    digest = hashlib.sha256(wheels[name]).hexdigest()
                    lock += (
                        f'wheels=[{{url="https://files.pythonhosted.org/fixture/{name}-1.0-py3-none-any.whl",'
                        f'hash="sha256:{digest}",size={len(wheels[name])}}}]\n'
                    )
                (root / "uv.lock").write_bytes(lock.encode())
                graph = dependency_graphs.inspect_project(root, DependencyProject("python"))
                original_open = Path.open
                metadata_reads = []
                verified = []

                def counted_open(path, *args, **kwargs):
                    if path.name == "METADATA":
                        metadata_reads.append(path)
                    return original_open(path, *args, **kwargs)

                def published(name, version, locked, observations, **kwargs):
                    self.assertEqual(observations[0]["files"], expected[name])
                    verify_wheel_payload(name, wheels[name], observations[0])
                    verified.append(name)
                    if len(verified) != len(packages):
                        return
                    if damage in {"duplicate", "added"}:
                        extra = site / "extra-1.0.dist-info"
                        extra.mkdir()
                        (extra / "METADATA").write_bytes(f"Name: {'sample0' if damage == 'duplicate' else 'extra'}\nVersion: 1.0\n".encode())
                        (extra / "RECORD").write_bytes(b"extra-1.0.dist-info/METADATA,,\nextra-1.0.dist-info/RECORD,,\n")
                    elif damage == "removed":
                        shutil.rmtree(site / "sample0-1.0.dist-info")
                    elif damage == "changed":
                        (site / "sample0.py").write_bytes(b"unpublished bytes\n")

                with patch.object(Path, "open", counted_open), patch.object(dependency_provenance, "_published_wheel", side_effect=published):
                    if damage is None:
                        verify_python_environment(graph)
                        self.assertLessEqual(len(metadata_reads), 8 * len(packages), "metadata reads must scale with packages, not packages squared")
                    else:
                        with self.assertRaisesRegex(ValueError, "duplicate installed identities|changed during verification"):
                            verify_python_environment(graph)
                self.assertEqual(verified, packages)

    def test_parsed_distribution_owns_metadata_and_requires_a_fresh_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            metadata = site / "sample-1.0.dist-info"
            metadata.mkdir()
            identity = metadata / "METADATA"
            identity.write_bytes(b"Name: Sample_Pkg\nVersion: 1.0\n")
            (metadata / "RECORD").write_bytes(b"sample-1.0.dist-info/METADATA,,\nsample-1.0.dist-info/RECORD,,\n")
            parsed = distribution_inventory((site,))["sample-pkg"]
            self.assertEqual((parsed.name, parsed.version), ("sample-pkg", "1.0"))
            self.assertIsInstance(parsed.records, tuple)
            with self.assertRaises(FrozenInstanceError):
                setattr(parsed, "name", "unrelated")
            original = inspect_distribution(parsed)
            identity.write_bytes(b"Name: Sample_Pkg\nVersion: 2.0\n")
            self.assertEqual(parsed.version, "1.0")
            fresh = distribution_inventory((site,))["sample-pkg"]
            self.assertEqual(fresh.version, "2.0")
            self.assertNotEqual(inspect_distribution(fresh), original)
            for invalid in (b"Name: sample\n", b"Name: ../unsafe\nVersion: 1.0\n"):
                identity.write_bytes(invalid)
                with self.assertRaisesRegex(ValueError, "requires a package name and version"):
                    distribution_inventory((site,))
            identity.write_bytes(b"Name: sample\nVersion: 1.0\n")
            unsafe = site / "outside.py"
            unsafe.write_bytes(b"untrusted bytes\n")
            # importlib.metadata omits nonexistent RECORD targets. Retain an
            # existing absolute target so the parser actually sees the defect.
            (metadata / "RECORD").write_bytes(f"{unsafe.as_posix()},,\n".encode())
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                distribution_inventory((site,))

    def test_installed_hashes_stream_exact_bytes_with_a_cumulative_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            metadata = site / "sample-1.0.dist-info"
            metadata.mkdir()
            contents = {
                "sample.bin": b"native\r\n\xff\x00" * 250000,
                "empty.bin": b"",
                "sample-1.0.dist-info/METADATA": b"Name: sample\nVersion: 1.0\n",
            }
            record = "sample-1.0.dist-info/RECORD"
            contents[record] = "".join(f"{name},,\n" for name in (*contents, record)).encode()
            for name, data in contents.items():
                (site / name).write_bytes(data)
            original_open = Path.open
            reads = []

            @contextmanager
            def bounded_open(path, *args, **kwargs):
                with original_open(path, *args, **kwargs) as stream:
                    if args != ("rb",):
                        yield stream
                        return

                    def read(size=-1):
                        self.assertGreaterEqual(size, 0)
                        self.assertLessEqual(size, 4 * 1024 * 1024, "small files must not reserve the whole package allowance")
                        reads.append(size)
                        return stream.read(size)

                    proxy = Mock(wraps=stream)
                    proxy.read.side_effect = read
                    yield proxy

            expected = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
            total = sum(map(len, contents.values()))
            with patch.object(Path, "open", bounded_open):
                self.assertEqual(inspect_installation("sample", paths=(site,), limit=512 * 1024 * 1024)["files"], expected)
                self.assertEqual(inspect_installation("sample", paths=(site,), limit=total)["files"], expected)
                with self.assertRaisesRegex(ValueError, "sample: .*byte limits"):
                    inspect_installation("sample", paths=(site,), limit=total - 1)
            self.assertTrue(reads)
            changed = contents["sample.bin"][:-1] + b"!"
            (site / "sample.bin").write_bytes(changed)
            hashes = inspect_installation("sample", paths=(site,))["files"]
            assert isinstance(hashes, dict)
            self.assertEqual(hashes["sample.bin"], hashlib.sha256(changed).hexdigest())

    def test_build_environment_preserves_native_compiler_discovery_and_isolates_configuration(self) -> None:
        for discovery_key in ("ProgramFiles", "PROGRAMFILES", "ProgramFiles(x86)", "PROGRAMFILES(X86)", "ProgramData", "PROGRAMDATA"):
            with self.subTest(key=discovery_key), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                uv = root / "uv.exe"
                uv.write_bytes(b"native executable fixture")
                inherited = {
                    discovery_key: r"C:\Native toolchain",
                    "PATH": "native-path",
                    "PYTHONPATH": "untrusted",
                    "UV_INDEX_URL": "untrusted",
                    "HOME": "ambient",
                }
                policy = parse_build_policy({"packages": ["sample-pkg"], "group": "build"})
                assert policy is not None
                with (
                    patch.dict(os.environ, inherited, clear=True),
                    patch.object(dependency_builds, "run_safe_command") as run,
                    patch.object(dependency_builds, "_tool_inventory", return_value={}),
                ):
                    run.return_value.stdout = "# locked build requirements\n"
                    _, environment, _ = dependency_builds._build_environment(root, root, policy, uv)
                self.assertEqual({key.upper(): value for key, value in environment.items()}[discovery_key.upper()], r"C:\Native toolchain")
                self.assertEqual(environment["PATH"], "native-path")
                self.assertEqual(environment["HOME"], str(root / "home"))
                self.assertNotIn("PYTHONPATH", environment)
                self.assertNotIn("UV_INDEX_URL", environment)
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs["env"], environment)

    def test_source_build_selection_is_explicit_and_strict(self) -> None:
        policy = parse_build_policy({"packages": ["sample-pkg"], "group": "build"})
        self.assertEqual(policy.packages if policy else None, ("sample-pkg",))
        for data in (
            {},
            {"packages": []},
            {"packages": ["sample-pkg", "sample-pkg"], "group": "build"},
            {"packages": ["Sample_Pkg"], "group": "build"},
            {"packages": ["sample-pkg"], "group": "../build"},
            {"packages": ["sample-pkg"], "group": "build", "trust-local": True},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_build_policy(data)

    def test_published_wheel_defaults_do_not_accept_relocation(self) -> None:
        payload = wheel_bytes()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
        with self.assertRaisesRegex(ValueError, "relocation"):
            verify_wheel_payload("sample-pkg", payload, {"files": files})

    def test_external_inputs_require_pinned_portable_paths(self) -> None:
        baseline = {
            "package": "sample-pkg",
            "platform": "win32-x86_64",
            "url": "https://github.com/example/tool/releases/download/v1/tool.zip",
            "sha256": "a" * 64,
            "member": "native.exe",
            "wheel-member": "sample_pkg-1.2.3.data/scripts/native.exe",
        }
        policy = parse_build_policy({"packages": ["sample-pkg"], "group": "build", "external": [baseline]})
        assert policy is not None
        self.assertEqual(policy.external[0].member, "native.exe")
        for changes in (
            {"url": "http://github.com/example/tool/releases/download/v1/tool.zip"},
            {"url": "https://example.com/tool.zip"},
            {"sha256": "latest"},
            {"member": "../native.exe"},
            {"wheel-member": "C:\\native.exe"},
            {"package": "unselected"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_build_policy({"packages": ["sample-pkg"], "group": "build", "external": [{**baseline, **changes}]})

    def test_namespace_ownership_does_not_hide_unrecorded_files_and_limits_remain_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            (site / "shared").mkdir()
            for package in ("first", "second"):
                metadata = site / f"{package}-1.0.dist-info"
                metadata.mkdir()
                (metadata / "METADATA").write_bytes(f"Name: {package}\nVersion: 1.0\n".encode())
                (metadata / "RECORD").write_bytes(f"shared/{package}.py,,\n{package}-1.0.dist-info/METADATA,,\n{package}-1.0.dist-info/RECORD,,\n".encode())
                (site / "shared" / f"{package}.py").write_bytes(b"value = 1\n")
            shared = frozenset({"shared/first.py", "shared/second.py"})
            observed = inspect_installation("first", paths=(site,), shared_files=shared)
            assert isinstance(observed["files"], dict)
            self.assertIn("shared/first.py", observed["files"])
            self.assertNotIn("shared/second.py", observed["files"])
            self.assertEqual(inspect_installation("first", paths=(site,), limit=512 * 1024 * 1024, shared_files=shared), observed)
            with self.assertRaisesRegex(ValueError, "first: .*at most 512 MiB"):
                inspect_installation("first", paths=(site,), limit=512 * 1024 * 1024 + 1)
            (site / "shared/unrecorded.py").write_bytes(b"raise RuntimeError('untrusted')\n")
            files = inspect_installation("first", paths=(site,), shared_files=shared)["files"]
            assert isinstance(files, dict)
            self.assertIn("shared/unrecorded.py", files)
            with self.assertRaisesRegex(ValueError, "byte limits"):
                inspect_installation("first", paths=(site,), limit=8, shared_files=shared)

    def test_wheel_limits_retain_small_default_and_allow_bounded_native_payloads(self) -> None:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("engine-1.0.dist-info/METADATA", b"Name: engine\nVersion: 1.0\n")
            archive.writestr("engine-1.0.dist-info/WHEEL", b"Wheel-Version: 1.0\nTag: py3-none-any\n")
            archive.writestr("engine-1.0.dist-info/RECORD", b"")
            archive.writestr("engine.bin", b"x" * (32 * 1024 * 1024))
        payload = stream.getvalue()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            observed = {"files": {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}}
        with self.assertRaisesRegex(ValueError, "content limits"):
            verify_wheel_payload("engine", payload, observed)
        verify_wheel_payload("engine", payload, observed, limit=512 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "bounded byte limit"):
            verify_wheel_payload("engine", payload, observed, limit=512 * 1024 * 1024 + 1)
        observed["files"]["engine.bin"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            verify_wheel_payload("engine", payload, observed, limit=512 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
