"""Rust fence extraction retains source lines and delegates rule assertions."""

import pytest

from research_repo_tools.semgrep_docs import rust_blocks


@pytest.mark.parametrize(
    "suffix,source",
    [
        (".md", "# Guide\n\n```rust,no_run\n# let value = 1;\nassert!(value > 0);\n```\n"),
        (".rs", "//! Guide\n//!\n//! ```no_run\n//! # let value = 1;\n//! assert!(value > 0);\n//! ```\n"),
        (".rs", "/** Guide\n *\n * ```rust\n * # let value = 1;\n * assert!(value > 0);\n * ```\n */\n"),
    ],
)
def test_rust_fences_keep_line_numbers(tmp_path, suffix, source):
    path = tmp_path / ("source" + suffix)
    path.write_text(source, encoding="utf-8", newline="\n")
    assert rust_blocks(path) == ("\n\n\nlet value = 1;\nassert!(value > 0);\n",)


def test_other_languages_excluded_and_unclosed_rust_blocks(tmp_path):
    path = tmp_path / "guide.md"
    path.write_bytes(b"```python\nraise Exception()\n```\n")
    assert rust_blocks(path) == ()
    path.write_bytes(b"```rust\nlet x = 1;\n")
    with pytest.raises(ValueError, match="unclosed"):
        rust_blocks(path)


def test_native_scan_requires_complete_coverage_and_retains_findings(tmp_path, monkeypatch):
    import json
    import subprocess

    from research_repo_tools import config, security, semgrep_scan

    source = tmp_path / "source.py"
    source.write_bytes(b"raise RuntimeError()\n")
    settings = config.Config(root=tmp_path, semgrep=config.SemgrepSettings(config="rules.yml"))
    monkeypatch.setattr(semgrep_scan, "security_inventory", lambda *_args, **_kwargs: ("source.py",))
    monkeypatch.setattr(semgrep_scan, "resolve_executable", lambda *_args, **_kwargs: tmp_path / "semgrep")
    covered = False

    def execute(_binary, args, **kwargs):
        assert "--disable-nosem" in args and "--strict" in args and "--error" in args
        value = (
            {
                "results": [{"check_id": "local.rule", "path": str(source), "start": {"line": 1}, "end": {"line": 1}}],
                "errors": [],
                "paths": {"scanned": [str(source)] if covered else []},
            }
            if "--json" in args
            else {"version": "2.1.0", "runs": [{"tool": {}, "results": [{"message": {"text": "local finding"}}]}]}
        )
        from pathlib import Path

        Path(args[args.index("--output") + 1]).write_bytes(json.dumps(value).encode())
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(security, "run_command_bytes", execute)
    assert semgrep_scan.scan(settings, include=("*.py",)) == 1
    assert not (tmp_path / "target/security/semgrep/0.json").exists()
    covered = True
    assert semgrep_scan.scan(settings, include=("*.py",)) == 1
    assert json.loads((tmp_path / "target/security/semgrep/0.json").read_bytes())["results"][0]["check_id"] == "local.rule"


def test_documentation_fixture_assertions_use_shared_checker(tmp_path, monkeypatch):
    from research_repo_tools import config, semgrep, semgrep_scan

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    source = fixtures / "bad.md"
    source.write_bytes(b"```rust\n// ruleid: local.rule\nunsafe { operation() }\n```\n")
    settings = config.Config(root=tmp_path, semgrep=config.SemgrepSettings(config="rules.yml", fixtures="fixtures"))
    original = source.read_bytes()

    def check(adapted):
        from pathlib import Path

        generated = Path(adapted.semgrep.fixtures) / "bad.md.block-0.rs"
        assert generated.read_bytes() == b"\n// ruleid: local.rule\nunsafe { operation() }\n"
        raise ValueError("local.rule: expected finding not reported")

    monkeypatch.setattr(semgrep, "check", check)
    with pytest.raises(ValueError, match="expected finding"):
        semgrep_scan.check_documentation_fixtures(settings)
    assert source.read_bytes() == original
