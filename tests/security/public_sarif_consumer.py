"""Installed SARIF selection, metadata, indexing and failure contracts."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from research_repo_tools import config, sarif
from research_repo_tools.cli import main


def document():
    return {
        "version": "2.1.0",
        "properties": {"source": "synthetic"},
        "runs": [
            {
                "tool": {"driver": {"name": "Scanner", "rules": [{"id": "foreign.rule"}, {"id": "project.rule", "properties": {"credit": "author"}}]}},
                "results": [
                    {"ruleId": "foreign.rule", "ruleIndex": 0, "message": {"text": "foreign"}},
                    {"ruleId": "project.rule", "ruleIndex": 1, "rule": {"id": "project.rule", "index": 1}, "message": {"text": "selected"}},
                ],
                "automationDetails": {"guid": "12345678-1234-1234-1234-123456789abc"},
                "invocations": [{"executionSuccessful": True}],
                "originalUriBaseIds": {"SRCROOT": {"uri": "file:///repo/"}},
                "properties": {"ruleIndex": 99},
            }
        ],
    }


class TestSarifConsumer(unittest.TestCase):
    def test_source_link_stored_inside_output_is_rejected(self):
        for directory_link in (False, True):
            with self.subTest(directory_link=directory_link), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                output, external = root / "reports", root / "external"
                output.mkdir()
                external.mkdir()
                source = external / "input.sarif"
                original = b'{"version":"2.1.0","runs":[]}\r\n'
                source.write_bytes(original)
                (output / "old.sarif").write_bytes(b"previous\r\n")
                alias = output / "alias"
                try:
                    alias.symlink_to(external if directory_link else source, target_is_directory=directory_link)
                except OSError:
                    self.skipTest("symlinks are unavailable")
                selected = alias / source.name if directory_link else alias
                self.assertEqual(selected.resolve(), source)
                with self.assertRaisesRegex(ValueError, "source must be outside"):
                    sarif.split(selected, output, sarif.SarifPolicy({"Scanner": ()}))
                self.assertTrue(alias.is_symlink())
                self.assertEqual(selected.read_bytes(), original)
                self.assertEqual((output / "old.sarif").read_bytes(), b"previous\r\n")
                self.assertEqual(sorted(path.name for path in output.iterdir()), ["alias", "old.sarif"])

    def test_portable_case_alias_of_source_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "reports"
            output.mkdir()
            source = output / "input.sarif"
            original = b'{"version":"2.1.0","runs":[]}\r\n'
            source.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "source must be outside"):
                sarif.split(source, root / "REPORTS", sarif.SarifPolicy({"Scanner": ()}))
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual([path.name for path in root.iterdir()], ["reports"])

    def test_source_alias_into_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "reports"
            output.mkdir()
            source = output / "input.sarif"
            original = b'{"version":"2.1.0","runs":[]}\r\n'
            source.write_bytes(original)
            alias = root / "alias.sarif"
            try:
                alias.symlink_to(source)
            except OSError:
                self.skipTest("file symlinks are unavailable")
            self.assertEqual(alias.resolve(), source)
            with self.assertRaisesRegex(ValueError, "source must be outside"):
                sarif.split(alias, output, sarif.SarifPolicy({"Scanner": ()}))
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(alias.is_symlink())

    def test_input_inside_output_is_rejected_without_replacing_any_bytes(self):
        for value in (document(), {"version": "2.1.0", "runs": []}):
            for nested in (False, True):
                with self.subTest(value=value, nested=nested), tempfile.TemporaryDirectory() as directory:
                    output = Path(directory).resolve() / "reports"
                    source = output / "nested" / "input.sarif" if nested else output / "input.sarif"
                    source.parent.mkdir(parents=True)
                    source.write_bytes(json.dumps(value).encode())
                    (output / "old.sarif").write_bytes(b"previous\r\n")
                    before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
                    with self.assertRaisesRegex(ValueError, "source must be outside"):
                        sarif.split(source, output, sarif.SarifPolicy({"Scanner": ()}))
                    self.assertEqual(before, {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()})

    def test_publication_status_survives_a_strict_legacy_terminal_encoding(self):
        with tempfile.TemporaryDirectory(prefix="sarif 漢字 ") as directory:
            root = Path(directory).resolve()
            (root / "input.sarif").write_bytes(json.dumps(document()).encode())
            settings = config.parse({"sarif": {"drivers": {"Scanner": ["project."]}}}, root=root)
            buffer = io.BytesIO()
            with io.TextIOWrapper(buffer, encoding="cp1252", errors="strict", newline="\n") as output:
                with patch("research_repo_tools.cli.config.load", return_value=settings), redirect_stdout(output):
                    self.assertEqual(main(["sarif", "split", "input.sarif", "--output", "reports"]), 0)
                output.flush()
                self.assertIn(b"\\u6f22\\u5b57", buffer.getvalue())
            self.assertEqual(len(list((root / "reports").glob("*.sarif"))), 1)

    def test_namespace_filter_reindexes_both_reference_forms_and_preserves_metadata(self):
        source = document()
        original = copy.deepcopy(source)
        (output,) = sarif.transform(source, sarif.SarifPolicy({"Scanner": ("project.",)}, "review"))
        result = json.loads(output.payload)
        run = result["runs"][0]
        self.assertEqual([rule["id"] for rule in run["tool"]["driver"]["rules"]], ["project.rule"])
        self.assertEqual(run["results"][0]["ruleIndex"], 0)
        self.assertEqual(run["results"][0]["rule"]["index"], 0)
        self.assertEqual(run["properties"], {"ruleIndex": 99})
        self.assertEqual(run["originalUriBaseIds"], original["runs"][0]["originalUriBaseIds"])
        self.assertEqual(run["automationDetails"]["guid"], original["runs"][0]["automationDetails"]["guid"])
        # GitHub splits at the last slash into category and optional run ID.
        self.assertEqual(run["automationDetails"]["id"].rpartition("/"), (output.category, "/", ""))
        self.assertEqual(output.filename, f"{output.category}.sarif")
        self.assertEqual(result["properties"], original["properties"])
        self.assertEqual(source, original)

    def test_empty_runs_repeated_names_and_slug_collisions_have_stable_distinct_categories(self):
        run = document()["runs"][0]
        empty: dict[str, Any] = {"tool": {"driver": {"name": "Scanner"}}}
        collision = copy.deepcopy(run)
        collision["tool"]["driver"]["name"] = "SCANNER"
        source = {"version": "2.1.0", "runs": [empty, run, run, collision]}
        policy = sarif.SarifPolicy({"Scanner": (), "SCANNER": ()})
        outputs = sarif.transform(source, policy)
        self.assertEqual(len({item.filename for item in outputs}), 3)
        categories = [json.loads(item.payload)["runs"][0]["automationDetails"]["id"].rpartition("/")[0] for item in outputs]
        self.assertEqual(categories, [item.category for item in outputs])
        self.assertEqual(len(set(categories)), 3)
        self.assertTrue(outputs[0].category.endswith("-2"))
        empty.update(tool={"driver": {"name": "Scanner", "rules": [{"id": "project.empty"}]}})
        self.assertEqual([item.category for item in sarif.transform(source, policy)[1:]], [item.category for item in outputs])

    def test_descriptor_relationships_notifications_and_overrides_are_reindexed(self):
        source = document()
        run = source["runs"][0]
        run["tool"]["driver"]["rules"][1]["relationships"] = [{"target": {"id": "project.rule", "index": 1}, "kinds": ["superset"]}]
        run["invocations"][0]["toolExecutionNotifications"] = [{"associatedRule": {"id": "project.rule", "index": 1}, "message": {"text": "note"}}]
        run["invocations"][0]["ruleConfigurationOverrides"] = [{"descriptor": {"id": "project.rule", "index": 1}, "configuration": {"level": "warning"}}]
        (output,) = sarif.transform(source, sarif.SarifPolicy({"Scanner": ("project.",)}))
        transformed = json.loads(output.payload)["runs"][0]
        self.assertEqual(transformed["tool"]["driver"]["rules"][0]["relationships"][0]["target"]["index"], 0)
        self.assertEqual(transformed["invocations"][0]["toolExecutionNotifications"][0]["associatedRule"]["index"], 0)
        self.assertEqual(transformed["invocations"][0]["ruleConfigurationOverrides"][0]["descriptor"]["index"], 0)

    def test_invalid_metadata_references_preserve_previous_publication_even_in_unselected_runs(self):
        for kind in ("relationship", "notification", "override"):
            for reference, diagnostic in (
                ({"id": "foreign.rule", "index": 1}, "inconsistent rule identity"),
                ({"id": "", "index": 1}, "nonempty string"),
                ({"id": 12}, "nonempty string"),
                ({"index": 99}, "removed or invalid rule index"),
                ({"id": "project.rule", "toolComponent": {"index": 0}}, "extension rule references"),
            ):
                for selected in ("Scanner", "unselected"):
                    with self.subTest(kind=kind, reference=reference, selected=selected), tempfile.TemporaryDirectory() as directory:
                        root = Path(directory).resolve()
                        source, output = root / "input.sarif", root / "reports"
                        output.mkdir()
                        (output / "old.sarif").write_bytes(b"previous\r\n")
                        value = document()
                        run = value["runs"][0]
                        if kind == "relationship":
                            run["tool"]["driver"]["rules"][1]["relationships"] = [{"target": reference}]
                        elif kind == "notification":
                            run["invocations"][0]["toolExecutionNotifications"] = [{"associatedRule": reference, "message": {"text": "note"}}]
                        else:
                            run["invocations"][0]["ruleConfigurationOverrides"] = [{"descriptor": reference, "configuration": {"level": "warning"}}]
                        source.write_bytes(json.dumps(value).encode())
                        with self.assertRaisesRegex(ValueError, diagnostic):
                            sarif.split(source, output, sarif.SarifPolicy({selected: ("project.",)}))
                        self.assertEqual({path.name for path in output.iterdir()}, {"old.sarif"})
                        self.assertEqual((output / "old.sarif").read_bytes(), b"previous\r\n")

    def test_metadata_referencing_a_removed_rule_rejects_selection(self):
        value = document()
        value["runs"][0]["invocations"][0]["ruleConfigurationOverrides"] = [
            {"descriptor": {"id": "foreign.rule", "index": 0}, "configuration": {"level": "warning"}}
        ]
        with self.assertRaisesRegex(ValueError, "removed or invalid"):
            sarif.transform(value, sarif.SarifPolicy({"Scanner": ("project.",)}))

    def test_validation_precedes_publication_and_empty_generation_removes_stale_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, output = root / "input.sarif", root / "reports"
            output.mkdir()
            (output / "previous.sarif").write_bytes(b"old\r\n")
            policy = sarif.SarifPolicy({"Scanner": ()})
            for payload in (b'{"version":"2.1.0","runs":[],"bad":NaN}', b'{"version":"2.1.0","runs":[],"runs":[]}'):
                source.write_bytes(payload)
                with self.assertRaises(ValueError):
                    sarif.split(source, output, policy)
                self.assertEqual((output / "previous.sarif").read_bytes(), b"old\r\n")
            source.write_bytes(b'{"version":"2.1.0","runs":[]}')
            self.assertEqual(sarif.split(source, output, policy), ())
            self.assertEqual(list(output.iterdir()), [])

    def test_invalid_structure_and_indices_are_rejected_even_for_unselected_drivers(self):
        for change in ("version", "driver", "results", "rules", "index", "identity", "extension", "finite", "invocation"):
            with self.subTest(change=change):
                source = document()
                run = source["runs"][0]
                if change == "version":
                    source["version"] = "2.0.0"
                if change == "driver":
                    run["tool"]["driver"]["name"] = ""
                if change == "results":
                    run["results"] = [1]
                if change == "rules":
                    run["tool"]["driver"]["rules"] = [{"id": "same"}, {"id": "same"}]
                if change == "index":
                    run["results"][0]["ruleIndex"] = True
                if change == "identity":
                    run["results"][0]["ruleId"] = "mismatch"
                if change == "extension":
                    run["results"][0]["rule"] = {"id": "foreign.rule", "toolComponent": {"index": 0}}
                if change == "finite":
                    run["properties"] = {"number": float("inf")}
                if change == "invocation":
                    run["invocations"] = [{"executionSuccessful": False}]
                with self.assertRaises(ValueError):
                    sarif.transform(source, sarif.SarifPolicy({"other": ()}))

    def test_cli_uses_explicit_consumer_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "input.sarif").write_bytes(json.dumps(document()).encode())
            (root / "pyproject.toml").write_bytes(
                b'[tool.research-repo-tools.sarif]\ncategory-prefix="review"\n[tool.research-repo-tools.sarif.drivers]\nScanner=["project."]\n'
            )
            self.assertEqual(main(["--root", str(root), "sarif", "split", "input.sarif", "--github-output", "outputs", "--output", "reports"]), 0)
            self.assertEqual(
                (root / "outputs").read_bytes(), f"SARIF_DIRECTORY={root / 'reports'}\nSARIF_HAS_UPLOADABLE_RUNS=true\nSARIF_RUN_COUNT=1\n".encode()
            )
            self.assertEqual(len(list((root / "reports").glob("*.sarif"))), 1)
            with patch("research_repo_tools.cli.config.load", return_value=config.parse({}, root=root)):
                self.assertEqual(main(["sarif", "split", "input.sarif", "--output", "reports"]), 1)

    def test_cli_command_file_hardlinks_to_outputs_fail_before_publishing(self):
        for member in ("old.sarif", "nested/old.sarif"):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                source = root / "input.sarif"
                original = json.dumps(document()).encode()
                source.write_bytes(original)
                destination = root / "reports"
                previous = destination / member
                previous.parent.mkdir(parents=True)
                previous.write_bytes(b"old generation")
                command_file, retained = root / "command-file", root / "retained.sarif"
                command_file.hardlink_to(previous)
                retained.hardlink_to(previous)
                self.assertTrue(command_file.samefile(previous))
                self.assertTrue(retained.samefile(previous))
                settings = config.parse({"sarif": {"drivers": {"Scanner": ["project."]}}}, root=root)
                errors, output = io.StringIO(), io.StringIO()
                with patch("research_repo_tools.cli.config.load", return_value=settings), redirect_stdout(output), redirect_stderr(errors):
                    self.assertEqual(
                        main(["--root", str(root), "sarif", "split", str(source), "--output", str(destination), "--github-output", str(command_file)]), 1
                    )
                self.assertIn("must not alias an output file", errors.getvalue())
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(
                    {path.relative_to(destination).as_posix(): path.read_bytes() for path in destination.rglob("*") if path.is_file()},
                    {member: b"old generation"},
                )
                for alias in (command_file, retained):
                    self.assertTrue(alias.samefile(previous))
                    self.assertEqual(alias.read_bytes(), b"old generation")

    def test_cli_command_file_aliases_fail_before_publishing(self):
        for kind in ("source", "output", "case", "unicode", "hardlink", "symlink", "parent-symlink", "relative-symlink", "relative-parent-symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                source = root / "input.sarif"
                original = json.dumps(document()).encode()
                source.write_bytes(original)
                destination = root / "réports"
                destination.mkdir()
                previous = destination / "old.sarif"
                previous.write_bytes(b"old generation")
                settings = config.parse({"sarif": {"drivers": {"Scanner": ["project."]}}}, root=root)
                command_file = source
                link_identity = None
                if kind == "output":
                    command_file = destination / "new.sarif"
                elif kind == "case":
                    command_file = root / "RÉPORTS" / "outputs"
                elif kind == "unicode":
                    command_file = root / "re\u0301ports" / "outputs"
                elif kind == "hardlink":
                    command_file = root / "command-file"
                    command_file.hardlink_to(source)
                    self.assertTrue(command_file.samefile(source))
                elif kind.endswith("symlink"):
                    alias = root / "alias"
                    target = destination if "parent" in kind else source
                    try:
                        alias.symlink_to(target.name if kind.startswith("relative") else target, target_is_directory="parent" in kind)
                    except OSError as error:
                        if getattr(error, "winerror", None) == 1314:
                            continue  # Other portable alias cases still execute without link privileges.
                        raise
                    self.assertTrue(alias.is_symlink())
                    link_identity = (alias, alias.readlink(), target)
                    command_file = alias / "outputs" if "parent" in kind else alias
                errors = io.StringIO()
                with patch("research_repo_tools.cli.config.load", return_value=settings), redirect_stdout(io.StringIO()), redirect_stderr(errors):
                    self.assertEqual(
                        main(["--root", str(root), "sarif", "split", str(source), "--output", str(destination), "--github-output", str(command_file)]), 1
                    )
                self.assertRegex(errors.getvalue(), r"duplicate target paths|overlapping target paths|must be outside|must not alias the source")
                if link_identity is not None:
                    alias, link_text, target = link_identity
                    self.assertTrue(alias.is_symlink())
                    self.assertEqual(alias.readlink(), link_text)
                    self.assertTrue(alias.samefile(target))
                if kind == "hardlink":
                    self.assertTrue(command_file.samefile(source))
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual({path.name: path.read_bytes() for path in destination.iterdir()}, {"old.sarif": b"old generation"})


if __name__ == "__main__":
    unittest.main()
