"""Native Just inspection contract, also run from installed wheel and sdist."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from research_repo_tools.just_inspect import dry_run, inspect_justfile
from research_repo_tools.process import ExecutableNotFoundError, resolve_executable


class TestJustInspection(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Just consumer 分析 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.just = resolve_executable("just")
        self.file = self.root / "commands" / "commands with spaces.just"
        self.file.parent.mkdir()
        self.file.write_text(
            'value := env("RRT_JUST_VALUE")\n'
            "alias preview := render\n"
            "prepare:\n    echo prepared > forbidden.txt\n"
            'render label="default": prepare\n    echo "{{value}}" "{{label}}" >> forbidden.txt\n'
            'location:\n    echo "{{invocation_directory()}}"\n',
            encoding="utf-8",
            newline="\n",
        )

    def test_metadata_preserves_native_parameters_dependencies_and_aliases(self):
        snapshot = inspect_justfile(self.root, justfile=self.file.relative_to(self.root), executable=self.just)
        self.assertEqual(set(snapshot.recipes), {"prepare", "render", "location"})
        recipe = snapshot.recipes["render"]
        self.assertEqual(recipe["parameters"][0]["name"], "label")
        self.assertEqual(recipe["parameters"][0]["default"], "default")
        self.assertEqual([item["recipe"] for item in recipe["dependencies"]], ["prepare"])
        self.assertEqual(snapshot.aliases, {"preview": "render"})
        (self.root / "justfile").write_bytes(self.file.read_bytes())
        self.assertEqual(inspect_justfile(self.root), snapshot)
        self.assertFalse((self.root / "forbidden.txt").exists())

    def test_native_dry_run_preserves_arguments_and_does_not_run_bodies(self):
        before = dict(os.environ)
        original = self.file.read_bytes()
        for argument in ("spaces and 分析", "--help", 'literal "quote"', ""):
            result = dry_run(
                self.root,
                "preview",
                [argument],
                justfile=self.file,
                executable=self.just,
                env={**os.environ, "RRT_JUST_VALUE": "child-only"},
            )
            self.assertEqual((result.returncode, result.stdout), (0, ""))
            self.assertIn(f'echo "child-only" "{argument}"', result.stderr)
            self.assertLess(result.stderr.index("echo prepared"), result.stderr.index('echo "child-only"'))
            self.assertFalse((self.root / "forbidden.txt").exists())
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(dict(os.environ), before)
        location = dry_run(self.root, "location", justfile=self.file, executable=self.just, env={**os.environ, "RRT_JUST_VALUE": "present"})
        self.assertEqual(Path(location.stderr.strip().removeprefix('echo "').removesuffix('"')), self.root)

    def test_native_evaluation_and_errors_are_not_hidden_by_dry_run(self):
        env = {key: value for key, value in os.environ.items() if key != "RRT_JUST_VALUE"}
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            dry_run(self.root, "render", justfile=self.file, executable=self.just, env=env)
        self.assertIn(b"RRT_JUST_VALUE", raised.exception.stderr)
        env["RRT_JUST_VALUE"] = "present"
        with self.assertRaises(subprocess.CalledProcessError):
            dry_run(self.root, "render", ["too", "many"], justfile=self.file, executable=self.just, env=env)
        self.assertFalse((self.root / "forbidden.txt").exists())

    def test_missing_inputs_and_executables_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Justfile does not exist"):
            inspect_justfile(self.root)
        with self.assertRaises(ExecutableNotFoundError):
            inspect_justfile(self.root, justfile=self.file, executable=self.root / "missing-just")
        for recipe in ("", "value=override", "bad\0name"):
            with self.subTest(recipe=recipe), self.assertRaises(ValueError):
                dry_run(self.root, recipe, justfile=self.file, executable=self.just)


if __name__ == "__main__":
    unittest.main()
