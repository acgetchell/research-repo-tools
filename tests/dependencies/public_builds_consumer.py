"""Native source builds and authenticated receipt regressions, also run installed."""

import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import dependency_build_receipts, dependency_builds, dependency_graphs
from research_repo_tools._dependency_probe import inspect_installation
from research_repo_tools.dependency_artifacts import verify_python_environment
from research_repo_tools.dependency_build_policy import parse_build_policy
from research_repo_tools.dependency_build_receipts import verify_build_receipt
from research_repo_tools.dependency_builds import prepare_dependency_builds
from research_repo_tools.dependency_policy import load_policy
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
            (site / "shared/unrecorded.py").write_bytes(b"raise RuntimeError('untrusted')\n")
            files = inspect_installation("first", paths=(site,), shared_files=shared)["files"]
            assert isinstance(files, dict)
            self.assertIn("shared/unrecorded.py", files)
            with self.assertRaisesRegex(ValueError, "byte limits"):
                inspect_installation("first", paths=(site,), limit=8, shared_files=shared)


if __name__ == "__main__":
    unittest.main()
