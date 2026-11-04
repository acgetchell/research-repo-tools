"""Real stable rustdoc checks for source and installed package consumers."""

import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import cli
from research_repo_tools.cargo_docs import check_documentation
from research_repo_tools.process import run_command

STABLE_RUST = "1.99.1"
MANIFEST = b"""[package]
name = "docs-fixture"
version = "0.1.0"
edition = "2024"
include = ["src/**", "Cargo.toml", "guide.md"]

[features]
default = ["default-api"]
default-api = []
extra = []
"""
LIBRARY = b"""#![doc = include_str!("../guide.md")]
#![cfg_attr(feature = "default-api", doc = "See [default_api].")]
#![cfg_attr(feature = "extra", doc = "See [optional_api].")]

#[cfg(feature = "default-api")]
pub fn default_api() {}
#[cfg(feature = "extra")]
pub fn optional_api() {}
"""


class TestCargoDocumentation(unittest.TestCase):
    @override
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="stable cargo docs café ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.manifest = self.root / "Cargo.toml"
        self.manifest.write_bytes(MANIFEST)
        self.guide = self.root / "guide.md"
        self.guide.write_bytes(b"# Packaged documentation\n")
        (self.root / "src").mkdir()
        self.library = self.root / "src/lib.rs"
        self.library.write_bytes(LIBRARY)
        (self.root / "rust-toolchain.toml").write_bytes(f'[toolchain]\nchannel = "{STABLE_RUST}"\n'.encode())
        run_command("rustup", ["run", STABLE_RUST, "cargo", "generate-lockfile", "--offline"], cwd=self.root, timeout=30)

    def test_public_cli_and_api_respect_feature_selection_and_keep_source_bytes(self):
        original = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        default = check_documentation(self.root, "docs-fixture")
        self.assertIn(f"release: {STABLE_RUST}", default.compiler.splitlines())
        self.assertEqual(default.source_manifest_sha256, hashlib.sha256(MANIFEST).hexdigest())
        self.assertEqual(default.source_lock_sha256, hashlib.sha256(original[self.root / "Cargo.lock"]).hexdigest())
        self.assertRegex(default.archive_sha256, r"^[a-f0-9]{64}$")
        self.assertTrue(default.default_features)
        explicit = check_documentation(self.root, "docs-fixture", features=("extra",), default_features=False)
        self.assertEqual(explicit.features, ("extra",))
        self.assertFalse(explicit.default_features)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["--root", str(self.root), "validation", "cargo-docs", "docs-fixture", "--all-features"]), 0)
        evidence = json.loads(output.getvalue())
        self.assertEqual(evidence["version"], "0.1.0")
        self.assertTrue(evidence["all_features"])
        self.assertEqual(evidence["toolchain"], STABLE_RUST)
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, original)
        self.assertFalse((self.root / "target").exists())

    def test_broken_optional_intra_doc_link_fails_the_stable_gate(self):
        self.library.write_bytes(LIBRARY.replace(b"See [optional_api].", b"See [missing_optional_api]."))
        check_documentation(self.root, "docs-fixture")
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = cli.main(["--root", str(self.root), "validation", "cargo-docs", "docs-fixture", "--all-features"])
        self.assertEqual(status, 1)
        self.assertIn("unresolved link to `missing_optional_api`", error.getvalue())

    def test_missing_packaged_doc_input_fails_despite_existing_source_file(self):
        self.manifest.write_bytes(MANIFEST.replace(b', "guide.md"', b""))
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            check_documentation(self.root, "docs-fixture")
        self.assertIn(b"couldn't read", raised.exception.stderr)
        self.assertIn(b"guide.md", raised.exception.stderr)
        self.assertEqual(self.guide.read_bytes(), b"# Packaged documentation\n")

    def test_consumer_config_and_explicit_cfg_reach_packaged_rustdoc(self):
        (self.root / ".cargo").mkdir()
        (self.root / ".cargo/config.toml").write_bytes(b'[build]\nrustdocflags = ["--cfg", "consumer_doc"]\n')
        self.library.write_bytes(
            LIBRARY + b'\n#[cfg(doc)]\n#[cfg(not(all(consumer_doc, explicit_doc)))]\ncompile_error!("documentation settings missing");\n'
            b'\n#[cfg(doc)]\n#[cfg(docsrs)]\ncompile_error!("docsrs cfg was enabled automatically");\n'
        )
        # Cargo config, and not any caller's Rust flags, should supply consumer_doc.
        with patch.dict("os.environ", {"RUSTDOCFLAGS": "--check-cfg=cfg(consumer_doc,explicit_doc,docsrs)"}):
            # Environment flags take precedence over Cargo's build.rustdocflags.
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                check_documentation(self.root, "docs-fixture", cfg=("explicit_doc",))
            self.assertIn(b"documentation settings missing", raised.exception.stderr)
        (self.root / ".cargo/config.toml").write_bytes(
            b'[build]\nrustdocflags = ["--cfg", "consumer_doc", "--check-cfg=cfg(consumer_doc,explicit_doc,docsrs)"]\n'
        )
        evidence = check_documentation(self.root, "docs-fixture", cfg=("explicit_doc",))
        self.assertEqual(evidence.cfg, ("explicit_doc",))

    def test_unsupported_settings_fail_clearly_on_stable(self):
        self.library.write_bytes(b"#![cfg_attr(docsrs, feature(doc_cfg))]\n" + LIBRARY)
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            check_documentation(self.root, "docs-fixture", cfg=("docsrs",))
        self.assertIn(b"may not be used on the stable release channel", raised.exception.stderr)
        with patch.dict("os.environ", {"RUSTC_BOOTSTRAP": "1"}):
            with self.assertRaisesRegex(ValueError, "requires RUSTC_BOOTSTRAP to be unset"):
                check_documentation(self.root, "docs-fixture")


if __name__ == "__main__":
    unittest.main()
