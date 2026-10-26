"""Native Just composition using the installed template and inert command bodies."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from research_repo_tools.changelog import template
from research_repo_tools.process import resolve_executable


class TestDependencyRecipes(unittest.TestCase):
    def test_audit_security_and_updates_preserve_explicit_scope_and_failure_status(self):
        with tempfile.TemporaryDirectory(prefix="audit recipes café ") as directory:
            root = Path(directory).resolve()
            recorder = root / "record commands.py"
            recorder.write_text(
                "import json, os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "with pathlib.Path('calls.jsonl').open('a', encoding='utf-8', newline='\\n') as stream: stream.write(json.dumps(args) + '\\n')\n"
                "locks = json.loads(os.environ['TEST_LOCKS'])\n"
                "if 'update-python' in args:\n"
                "    for name in locks: pathlib.Path(name).write_bytes(b'final resolution\\n')\n"
                "if 'osv' in args:\n"
                "    selected = args[args.index('osv')+1:]\n"
                "    assert selected == locks, selected\n"
                "    if os.environ.get('TEST_FINAL'):\n"
                "        assert all(pathlib.Path(name).read_bytes() == b'final resolution\\n' for name in selected)\n"
                "    sys.exit(int(os.environ.get('TEST_AUDIT_FAILURE', '0')))\n",
                encoding="utf-8",
                newline="\n",
            )
            locks = ["uv.lock", "tooling/scanner/uv.lock", "Cargo.lock", "fixtures/extra root/Cargo.lock"]
            for name in locks:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"old resolution\n")
            (root / "Cargo.toml").write_bytes(b"[workspace]\nmembers=[]\n")
            source = template("justfile").replace('(security-osv "uv.lock")', "(security-osv " + " ".join(json.dumps(name) for name in locks) + ")")
            command = shlex.join([sys.executable, "-I", "-X", "utf8", str(recorder)])
            (root / "justfile").write_text(source.replace("uv run", command + " run"), encoding="utf-8", newline="\n")
            for recipe in ("audit", "security", "update-dependencies", "update", "update-tools"):
                for failure in (0, 1, 23, 124):
                    with self.subTest(recipe=recipe, failure=failure):
                        (root / "calls.jsonl").unlink(missing_ok=True)
                        env = os.environ | {"TEST_LOCKS": json.dumps(locks), "TEST_AUDIT_FAILURE": str(failure)}
                        if recipe in {"update", "update-dependencies"}:
                            env["TEST_FINAL"] = "1"
                        result = subprocess.run(
                            [resolve_executable("just"), "--justfile", str(root / "justfile"), recipe],
                            cwd=root,
                            env=env,
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=30,
                        )
                        self.assertEqual(result.returncode, 0 if recipe == "update-tools" else failure, result.stderr)
                        calls = [json.loads(line) for line in (root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
                        audits = [call for call in calls if "osv" in call]
                        self.assertEqual(len(audits), 0 if recipe == "update-tools" else 1)
                        if recipe in {"update", "update-dependencies"}:
                            self.assertIs(calls[-1], audits[0])
                            self.assertEqual(sum("update-python" in call for call in calls), 1)
                            self.assertEqual(sum("update-uv" in call for call in calls), int(recipe == "update"))
                        self.assertEqual(any("secrets" in call for call in calls), recipe == "security" and failure == 0)


if __name__ == "__main__":
    unittest.main()
