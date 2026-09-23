"""Public notebook review contracts, also run from installed wheel and sdist."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cli import main


def code(source="value = 1\n", cell_id="calculate-value"):
    return {"cell_type": "code", "id": cell_id, "metadata": {}, "source": source, "outputs": [], "execution_count": None}


class Consumer(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="notebook consumer ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "analysis with spaces.ipynb"

    def write(self, cells, *, minor=5):
        self.path.write_bytes((json.dumps({"nbformat": 4, "nbformat_minor": minor, "metadata": {}, "cells": cells}) + "\r\n").encode("utf-8"))
        return self.path.read_bytes()

    def run_cli(self, action, *options):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--root", str(self.root), "notebooks", action, self.path.name, *options])
        return status, out.getvalue(), err.getvalue()

    def configure(self, advice):
        (self.root / "pyproject.toml").write_text(
            '[project]\nrequires-python=">=3.14"\n[tool.research-repo-tools.notebooks.advice]\n' + advice,
            encoding="utf-8",
            newline="\n",
        )


class TestInspection(Consumer):
    def test_repair_inventory_preserves_positions_existing_ids_bytes_and_privacy(self):
        missing = code(["secret source\n", "second line\n"])
        del missing["id"]
        missing["outputs"] = [{"output_type": "stream", "name": "stdout", "text": "private stored output"}]
        missing["execution_count"] = 7
        original = self.write([missing, code(cell_id="reused"), code(cell_id="reused"), code(cell_id="invalid id"), None], minor=2)
        status, out, err = self.run_cli("inspect", "--json", "--no-preview")
        self.assertEqual((status, err), (0, ""))
        self.assertNotIn("secret source", out)
        self.assertNotIn("private stored output", out)
        report = json.loads(out)
        self.assertEqual(report["schema"], 1)
        notebook = report["notebooks"][0]
        self.assertEqual(notebook["path"], str(self.path))
        self.assertTrue(notebook["problems"])
        cells = notebook["cells"]
        self.assertEqual([cell["number"] for cell in cells], [1, 2, 3, 4, 5])
        self.assertEqual([cell["id_status"] for cell in cells], ["missing", "existing", "duplicate", "invalid", "missing"])
        self.assertIsNone(cells[0]["id"])
        self.assertEqual(cells[3]["id"], "invalid id")
        self.assertEqual((cells[0]["source_lines"], cells[0]["output_count"], cells[0]["execution_count"]), (2, 1, 7))
        self.assertNotIn("preview", cells[0])
        self.assertEqual(self.path.read_bytes(), original)
        status, out, err = self.run_cli("inspect")
        self.assertEqual(status, 0, err)
        self.assertIn("secret source second line", out)
        self.assertIn("cell 5", out)
        self.assertIn("(missing)", out)
        self.assertNotIn("private stored output", out)
        self.assertEqual(self.path.read_bytes(), original)

    def test_malformed_fields_are_reported_without_serializing_arbitrary_values(self):
        private = {"sensitive": "must not leak"}
        original = self.write([{"cell_type": private, "id": private, "source": [private], "metadata": []}, code()])
        status, out, err = self.run_cli("inspect", "--json")
        self.assertEqual((status, err), (0, ""))
        self.assertNotIn("must not leak", out)
        cell = json.loads(out)["notebooks"][0]["cells"][0]
        self.assertEqual(cell["id_status"], "invalid")
        self.assertIsNone(cell["source_lines"])
        self.assertGreaterEqual(len(cell["problems"]), 4)
        self.assertEqual(self.path.read_bytes(), original)

    def test_unreadable_or_ambiguous_input_fails_without_partial_json(self):
        for raw in (
            b'{"nbformat":4,"nbformat":4,"cells":[]}',
            b'{"nbformat":4,"cells":[],"value":1e999}',
            b'{"nbformat":4,"cells":[],"value":NaN}',
            b'{"nbformat":3,"cells":[]}',
            b'{"nbformat":4,"cells":{}}',
            b"\xff",
            b"{",
        ):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                status, out, err = self.run_cli("inspect", "--json")
                self.assertEqual((status, out), (1, ""))
                self.assertIn(str(self.path), err)
                self.assertEqual(self.path.read_bytes(), raw)
        self.write([code()])
        status, out, err = self.run_cli("inspect", "missing.ipynb", "--json")
        self.assertEqual((status, out), (1, ""))

    def test_duplicate_keys_never_expose_private_payloads(self):
        for field in ("source", "metadata", "outputs"):
            with self.subTest(field=field):
                private = '{"private marker":1,"private marker":2}'
                value = '[{"data":{"application/json":' + private + "}}]" if field == "outputs" else private
                raw = ('{"nbformat":4,"cells":[{"' + field + '":' + value + "}]}").encode()
                self.path.write_bytes(raw)
                status, out, err = self.run_cli("inspect", "--json", "--no-preview")
                self.assertEqual((status, out), (1, ""))
                self.assertIn("duplicate JSON key", err)
                self.assertNotIn("private marker", err)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_non_finite_numbers_never_expose_private_payloads(self):
        for field in ("source", "metadata", "outputs"):
            for token in ("8675309123456789e999", "-8675309123456789e999", "NaN", "Infinity", "-Infinity"):
                with self.subTest(field=field, token=token):
                    private = '{"private marker":' + token + "}"
                    value = '[{"data":{"application/json":' + private + "}}]" if field == "outputs" else private
                    raw = ('{"nbformat":4,"cells":[{"' + field + '":' + value + "}]}").encode()
                    self.path.write_bytes(raw)
                    status, out, err = self.run_cli("inspect", "--json", "--no-preview")
                    self.assertEqual((status, out), (1, ""))
                    self.assertIn("non-finite JSON number", err)
                    self.assertNotIn(token, err)
                    self.assertNotIn("private marker", err)
                    self.assertEqual(self.path.read_bytes(), raw)

    def test_unicode_strings_are_validated_without_exposing_private_values(self):
        for fields in (
            {"metadata": {"private marker": "\ud800"}},
            {"metadata": {"\udfff": "private marker"}},
            {"source": "\ud800"},
            {"source": ["\udfff"]},
            {"outputs": [{"output_type": "stream", "name": "stdout", "text": "\ud800"}]},
        ):
            with self.subTest(fields=list(fields)):
                original = self.write([{**code(), **fields}])
                status, out, err = self.run_cli("inspect", "--json", "--no-preview")
                self.assertEqual((status, out), (1, ""))
                self.assertIn("unpaired Unicode surrogate", err)
                self.assertNotIn("private marker", err)
                self.assertEqual(self.path.read_bytes(), original)
        original = self.write([code('text = "😀"')])
        self.assertIn(b"\\ud83d\\ude00", original)
        status, out, err = self.run_cli("inspect", "--json")
        self.assertEqual((status, err), (0, ""))
        self.assertIn("😀", json.loads(out)["notebooks"][0]["cells"][0]["preview"])
        self.assertEqual(self.path.read_bytes(), original)


class TestAdvice(Consumer):
    def test_invalid_unicode_is_rejected_before_notebook_execution(self):
        self.path = self.root / "notebooks" / "invalid.ipynb"
        self.path.parent.mkdir()
        cell = code("raise AssertionError('must not execute')")
        cell["metadata"] = {"private marker": "\ud800"}
        original = self.write([cell])
        out, err = io.StringIO(), io.StringIO()
        with patch("research_repo_tools.notebooks._execute") as execute, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--root", str(self.root), "notebooks", "execute", str(self.path)])
        self.assertEqual(status, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("unpaired Unicode surrogate", err.getvalue())
        self.assertNotIn("private marker", err.getvalue())
        execute.assert_not_called()
        self.assertFalse((self.root / "target").exists())
        self.assertEqual(self.path.read_bytes(), original)

    def test_generated_and_positional_identity_are_only_advice(self):
        original = self.write(
            [
                {"cell_type": "markdown", "id": "a0b1c2d3", "metadata": {}, "source": "# Introduction"},
                code(cell_id="cell-2"),
                code(cell_id="calculate-value"),
            ]
        )
        self.assertEqual(self.run_cli("check")[0], 0)
        status, out, err = self.run_cli("advise")
        self.assertEqual(status, 0, err)
        self.assertIn("cell 1 (a0b1c2d3)", err)
        self.assertIn("cell 2 (cell-2)", err)
        self.assertNotIn("cell 3", err)
        self.assertEqual(self.run_cli("advise", "--strict")[0], 1)
        self.configure("descriptive-ids=false\nstrict=true\n")
        self.assertEqual(self.run_cli("advise")[0], 0)
        self.assertEqual(self.path.read_bytes(), original)

    def test_native_ruff_annotations_exceptions_and_consumer_library_policy(self):
        self.configure(
            'ruff-rules=["ANN001", "ANN201", "BLE001", "TID251"]\n'
            "[tool.ruff.lint.flake8-tidy-imports.banned-api]\n"
            '"pandas"={msg="Prefer Polars for this project"}\n"csv"={msg="Prefer Polars for this project"}\n'
        )
        original = self.write(
            [code("import pandas as pd\nimport csv\n\ndef calculate(value):\n    try:\n        return value + 1\n    except Exception:\n        return 0\n")]
        )
        status, out, err = self.run_cli("advise")
        self.assertEqual(status, 0, err)
        for message in ("ANN001", "ANN201", "BLE001", "TID251", "Prefer Polars", "cell 1 (calculate-value)"):
            self.assertIn(message, err)
        self.assertEqual(self.run_cli("advise", "--strict")[0], 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.root / ".ruff_cache").exists())
        self.configure("descriptive-ids=false\n")
        self.assertEqual(self.run_cli("advise")[2], "")

    def test_magic_skips_are_informational_and_no_cell_can_execute(self):
        self.configure('descriptive-ids=false\nsubprocess-timeout=true\nruff-rules=["ANN"]\nstrict=true\n')
        sentinel = self.root / "must-not-exist"
        original = self.write(
            [
                code(f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n", "side-effect"),
                code('%matplotlib inline\nsubprocess.run(["tool"])', "magic"),
                code("%%bash\nthis is not Python {", "foreign"),
                code("result = !printf example", "shell"),
                code('text = """\n%this is a string\nsubprocess.run([])\n"""', "literal"),
            ]
        )
        status, out, err = self.run_cli("advise")
        self.assertEqual(status, 0, err)
        self.assertEqual(err.count("INFO"), 3)
        self.assertNotIn("WARNING", err)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.path.read_bytes(), original)

    def test_timeout_advice_is_opt_in_and_distinguishes_explicit_bounds(self):
        original = self.write(
            [code('import subprocess\nsubprocess.run(["tool"])\nsubprocess.check_output(["tool"], timeout=None)\nsubprocess.run(["tool"], timeout=10)\n')]
        )
        self.assertEqual(self.run_cli("advise")[2], "")
        self.configure("subprocess-timeout=true\n")
        status, out, err = self.run_cli("advise", "--strict")
        self.assertEqual(status, 1)
        self.assertEqual(err.count("WARNING"), 2)
        self.assertIn("(calculate-value):2:1", err)
        self.assertIn("(calculate-value):3:1", err)
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_ruff_configuration_and_syntax_are_always_errors(self):
        self.configure('ruff-rules=["NOTARULE999"]\n')
        self.write([code()])
        status, out, err = self.run_cli("advise")
        self.assertEqual(status, 1)
        self.assertIn("ERROR", err)
        self.configure('ruff-rules=["ANN"]\n')
        self.write([code("if True print(1)")])
        status, out, err = self.run_cli("advise")
        self.assertEqual(status, 1)
        self.assertIn("invalid-syntax", err)

    def test_inspection_does_not_weaken_strict_commands(self):
        broken = code()
        del broken["id"]
        original = self.write([broken], minor=2)
        self.assertEqual(self.run_cli("inspect")[0], 0)
        for action in ("advise", "check", "clear", "execute", "lint"):
            with self.subTest(action=action):
                status, out, err = self.run_cli(action)
                self.assertEqual(status, 1)
                self.assertIn("nbformat 4.5", err)
                self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.root / "target").exists())


if __name__ == "__main__":
    unittest.main()
