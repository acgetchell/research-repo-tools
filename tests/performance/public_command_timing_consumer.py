"""Public exploratory timing contracts, run from source and both distributions."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools.command_timing import COMMAND_TIMING_SCHEMA, measure_commands, publish_command_timing, summarize_commands, validate_command_timing
from research_repo_tools.process import run_command


class TestCommandTiming(unittest.TestCase):
    @override
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="command timing café ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.script = self.root / "work café.py"
        self.script.write_text(
            "import json,os,pathlib,sys\n"
            "assert sys.argv[2:] == ['a b; $(literal)', '', 'café']\n"
            "assert os.environ['TIMING_VAR'] == 'café'\n"
            "with pathlib.Path('events').open('a',encoding='utf-8',newline='\\n') as f: f.write(json.dumps(sys.argv[1:])+ '\\n')\n",
            encoding="utf-8",
            newline="\n",
        )
        self.declare()

    def command(self, label: str) -> list[str]:
        return [sys.executable, "-I", "-X", "utf8", str(self.script), label, "a b; $(literal)", "", "café"]

    def declare(self, *, warmups: int = 1, repeats: int = 3, timeout: float = 5) -> None:
        def literal(value):
            return json.dumps(value, ensure_ascii=False)

        (self.root / "timing.toml").write_text(
            f'schema=1\nsources=["work café.py"]\nwarmups={warmups}\nrepeats={repeats}\ntimeout={timeout}\n'
            f"preflight=[{literal(self.command('prepare'))}]\n"
            '[environment]\nTIMING_VAR="café"\n'
            f"[probes]\npython={literal([sys.executable, '--version'])}\n"
            f'[[cases]]\nid="z-last"\ncommand={literal(self.command("last"))}\n'
            f'[[cases]]\nid="a-first"\ncommand={literal(self.command("first"))}\n',
            encoding="utf-8",
            newline="\n",
        )

    def test_complete_raw_samples_policy_and_provenance(self) -> None:
        payload = measure_commands(self.root, "timing.toml")
        validate_command_timing(payload)
        raw = json.loads(payload)
        self.assertEqual(raw["schema"], COMMAND_TIMING_SCHEMA)
        self.assertEqual((raw["purpose"], raw["unit"]), ("exploratory-only", "ns"))
        self.assertEqual(len(raw["observations"]), 9)
        self.assertEqual(
            [row["stage"] for row in raw["observations"]], ["preflight", "warmup", "sample", "sample", "sample", "warmup", "sample", "sample", "sample"]
        )
        events = [json.loads(line)[0] for line in (self.root / "events").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events, ["prepare"] + ["first"] * 4 + ["last"] * 4)
        for row in raw["observations"]:
            self.assertIs(type(row["elapsed_ns"]), int)
            self.assertGreater(row["elapsed_ns"], 0)
            self.assertEqual((row["status"], row["returncode"]), ("success", 0))
        self.assertEqual(raw["commands"]["case/a-first"]["argv"], self.command("first"))
        self.assertEqual(raw["source"]["work café.py"]["sha256"], hashlib.sha256(self.script.read_bytes()).hexdigest())
        self.assertEqual(raw["commands"]["case/a-first"]["sha256"], hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest())
        self.assertEqual(raw["policy"]["environment"], {"TIMING_VAR": "café"})
        self.assertEqual(raw["timer"]["name"], "time.perf_counter_ns")
        self.assertTrue(raw["timer"]["monotonic"])
        self.assertIn("Python", __import__("base64").b64decode(raw["tools"]["python"]["stdout_base64"]).decode())
        self.assertTrue(raw["host"]["os"])
        self.assertTrue(raw["runner"]["package_version"])
        self.assertEqual(list(raw), sorted(raw))
        self.assertEqual(payload, (json.dumps(raw, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode())
        with (
            patch.object(Path, "stat", side_effect=AssertionError("offline reader touched filesystem")),
            patch.object(Path, "resolve", side_effect=AssertionError("offline reader resolved source paths")),
        ):
            self.assertIn("a-first\t3\t", summarize_commands(payload))
        # Independent numeric oracle for summary; warmups/preflight are excluded.
        for row in raw["observations"]:
            row["elapsed_ns"] = (row["index"] + 1) * 1000000000
        self.assertEqual(
            summarize_commands(json.dumps(raw).encode()),
            "Exploratory command timing (wall seconds; no comparability claim)\ncase\tsamples\tmin_s\tmedian_s\tmax_s\na-first\t3\t1.000000000\t2.000000000\t3.000000000\nz-last\t3\t1.000000000\t2.000000000\t3.000000000\n",
        )

    def test_cli_publishes_fresh_report_and_rejects_existing_output(self) -> None:
        command = [
            "-I",
            "-X",
            "utf8",
            "-m",
            "research_repo_tools",
            "--root",
            str(self.root),
            "performance",
            "command-time",
            "timing.toml",
            "--output",
            "results/new café.json",
        ]
        result = run_command(sys.executable, command, cwd=self.root)
        self.assertIn("a-first\t3\t", result.stdout)
        output = self.root / "results/new café.json"
        payload, events = output.read_bytes(), (self.root / "events").read_bytes()
        validate_command_timing(payload)
        again = run_command(sys.executable, command, cwd=self.root, check=False)
        self.assertEqual(again.returncode, 1)
        self.assertIn("already exists", again.stderr)
        self.assertEqual(output.read_bytes(), payload)
        self.assertEqual((self.root / "events").read_bytes(), events)
        for name in ("timing.toml", "work café.py", "WORK CAFÉ.PY"):
            with self.assertRaises(ValueError):
                publish_command_timing(self.root, "timing.toml", name)

    def test_failed_preflight_warmup_sample_probe_and_timeout_never_publish(self) -> None:
        for stage in ("preflight", "warmup", "sample", "probe", "timeout"):
            with self.subTest(stage=stage):
                self.declare()
                (self.root / "events").unlink(missing_ok=True)
                source = self.script.read_text(encoding="utf-8")
                # Each child writes an ordinal before failing, independently
                # detecting the requested stage without a mock runner.
                injected = "\ncount=len(pathlib.Path('events').read_text(encoding='utf-8').splitlines())\n"
                if stage == "timeout":
                    injected += "import time;time.sleep(30)\npathlib.Path('survived').touch()\n"
                    self.declare(timeout=0.5)
                else:
                    ordinal = {"preflight": 1, "warmup": 2, "sample": 3, "probe": 1}[stage]
                    injected += f"if count=={ordinal}: raise SystemExit(27)\n"
                self.script.write_text(source + injected, encoding="utf-8", newline="\n")
                if stage == "probe":
                    text = (self.root / "timing.toml").read_text(encoding="utf-8")
                    text = text.replace(
                        json.dumps([sys.executable, "--version"], ensure_ascii=False),
                        json.dumps([sys.executable, "-c", "raise SystemExit(27)"], ensure_ascii=False),
                    )
                    # Probe failure must occur after a successful preflight.
                    self.script.write_text(source, encoding="utf-8", newline="\n")
                    (self.root / "timing.toml").write_text(text, encoding="utf-8", newline="\n")
                with self.assertRaises(subprocess.TimeoutExpired if stage == "timeout" else subprocess.CalledProcessError) as caught:
                    publish_command_timing(self.root, "timing.toml", "result.json")
                if stage != "timeout":
                    assert isinstance(caught.exception, subprocess.CalledProcessError)
                    self.assertEqual(caught.exception.returncode, 27)
                self.assertFalse((self.root / "result.json").exists())
                self.assertFalse((self.root / "survived").exists())
                self.script.write_text(source, encoding="utf-8", newline="\n")

    def test_malformed_and_incomplete_observations_are_rejected_offline(self) -> None:
        self.declare(warmups=0, repeats=1)
        original = json.loads(measure_commands(self.root, "timing.toml"))
        bad = []
        for field, value in (
            ("elapsed_ns", 0),
            ("elapsed_ns", -1),
            ("elapsed_ns", True),
            ("elapsed_ns", float("nan")),
            ("elapsed_ns", float("inf")),
            ("elapsed_ns", 1.5),
            ("returncode", 2),
            ("returncode", False),
            ("status", "failed"),
            ("command", "case/unknown"),
            ("index", True),
            ("stage", "warmup"),
        ):
            raw = copy.deepcopy(original)
            raw["observations"][-1][field] = value
            bad.append(raw)
        for field, value in (
            ("observations", original["observations"][:-1]),
            ("observations", original["observations"] * 2),
            ("schema", "research-repo-tools/complete-run/v3"),
            ("unit", "s"),
            ("purpose", "release"),
            ("commands", {}),
            ("source", {}),
            ("timer", {}),
            ("tools", {}),
            ("root", "relative"),
            ("execution", {**original["execution"], "shell": 0}),
        ):
            raw = copy.deepcopy(original)
            raw[field] = value
            bad.append(raw)
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    summarize_commands(json.dumps(raw).encode())
        with self.assertRaises(ValueError):
            validate_command_timing(b'{"schema":1,"schema":1}')

    def test_native_globs_and_offline_windows_case_matching(self) -> None:
        self.declare(warmups=0, repeats=1)
        declaration = self.root / "timing.toml"
        pattern = "*.PY" if sys.platform == "win32" else "*.py"
        declaration.write_text(declaration.read_text(encoding="utf-8").replace('["work café.py"]', json.dumps([pattern])), encoding="utf-8", newline="\n")
        raw = json.loads(measure_commands(self.root, "timing.toml"))
        self.assertIn("work café.py", raw["source"])
        self.assertEqual(raw["runner"]["platform"], sys.platform)
        # Deterministic retained-Windows model supplements the native glob run;
        # it never changes this process's OS identity or touches source paths.
        raw["runner"]["platform"] = "win32"
        raw["policy"]["sources"] = ["*.PY"]
        with patch.object(Path, "stat", side_effect=AssertionError("offline glob validation touched filesystem")):
            self.assertIn("a-first\t1\t", summarize_commands(json.dumps(raw).encode()))
        raw["runner"]["platform"] = "darwin"
        with self.assertRaisesRegex(ValueError, "omits a declared pattern"):
            validate_command_timing(json.dumps(raw).encode())

    def test_invalid_clock_observations_never_publish(self) -> None:
        import research_repo_tools.command_timing as timing

        for readings in ((10, 10), (10, 9), (10, float("nan")), (10, float("inf"))):
            with self.subTest(readings=readings):
                with patch.object(timing.time, "perf_counter_ns", side_effect=readings):
                    with self.assertRaises(ValueError):
                        publish_command_timing(self.root, "timing.toml", "result.json")
                self.assertFalse((self.root / "result.json").exists())

    def test_packaged_declarations_and_thin_recipe(self) -> None:
        import tomllib
        from importlib.resources import files

        from research_repo_tools.just_inspect import dry_run, inspect_justfile

        resources = files("research_repo_tools").joinpath("templates")
        for name in ("command-timing.toml", "cargo-examples.toml"):
            self.assertEqual(tomllib.loads(resources.joinpath(name).read_text(encoding="utf-8"))["schema"], 1)
        justfile = self.root / "justfile"
        justfile.write_bytes(resources.joinpath("justfile").read_bytes())
        snapshot = inspect_justfile(self.root)
        self.assertEqual([item["name"] for item in snapshot.recipes["command-time"]["parameters"]], ["configuration", "output"])
        command = dry_run(self.root, "command-time", ["timing.toml", "new café.json"])
        self.assertEqual(command.returncode, 0)
        self.assertIn('performance command-time "$1" --output "$2"', command.stderr)
        self.assertFalse((self.root / "new café.json").exists())

    def test_invalid_policy_fails_before_commands(self) -> None:
        original = (self.root / "timing.toml").read_text(encoding="utf-8")
        for before, after in (
            ("warmups=1", "warmups=-1"),
            ("warmups=1", "warmups=101"),
            ("repeats=3", "repeats=0"),
            ("repeats=3", "repeats=1001"),
            ("timeout=5", "timeout=nan"),
            ("timeout=5", "timeout=inf"),
            ("timeout=5", "timeout=true"),
            ("schema=1", "schema=true"),
            ('id="z-last"', 'id="a-first"'),
            ('id="z-last"', 'id="bad ID"'),
            ('sources=["work café.py"]', "sources=[]"),
            ('sources=["work café.py"]', 'sources=["missing"]'),
            ('sources=["work café.py"]', 'sources=["../*.py"]'),
            ('sources=["work café.py"]', r'sources=["bad\\*.py"]'),
            ("[environment]", "unknown=true\n[environment]"),
        ):
            with self.subTest(after=after):
                (self.root / "timing.toml").write_text(original.replace(before, after), encoding="utf-8", newline="\n")
                with self.assertRaises(ValueError):
                    measure_commands(self.root, "timing.toml")
                self.assertFalse((self.root / "events").exists())

    def test_changed_inputs_and_publication_races_reject_without_output(self) -> None:
        for changed in ("source", "declaration", "executable", "staging", "output"):
            with self.subTest(changed=changed):
                self.declare(warmups=0, repeats=1)
                import research_repo_tools.command_timing as timing
                import research_repo_tools.files as publication_files

                execute = timing.run_command_live
                recorded_file = timing._file
                stage_bytes = publication_files._stage_bytes

                def mutate(*args, **kwargs):
                    result = execute(*args, **kwargs)
                    if changed == "source":
                        self.script.write_bytes(self.script.read_bytes() + b"\n# concurrent edit\n")
                    elif changed == "declaration":
                        path = self.root / "timing.toml"
                        path.write_bytes(path.read_bytes() + b"\n# concurrent edit\n")
                    return result

                def stage(path, payload):
                    candidate = stage_bytes(path, payload)
                    if changed == "staging":
                        self.script.write_bytes(self.script.read_bytes() + b"\n# concurrent staging edit\n")
                    elif changed == "output":
                        (self.root / "result.json").write_bytes(b"another writer")
                    return candidate

                calls = 0

                def file_receipt(path):
                    nonlocal calls
                    receipt = recorded_file(path)
                    if path == Path(sys.executable):
                        calls += 1
                        if calls > 1:
                            receipt = {**receipt, "sha256": "a" * 64}
                    return receipt

                with (
                    patch.object(timing, "run_command_live", side_effect=mutate),
                    patch.object(publication_files, "_stage_bytes", side_effect=stage),
                    patch.object(timing, "_file", side_effect=file_receipt if changed == "executable" else recorded_file),
                ):
                    with self.assertRaises(ValueError):
                        publish_command_timing(self.root, "timing.toml", "result.json")
                if changed == "output":
                    self.assertEqual((self.root / "result.json").read_bytes(), b"another writer")
                    (self.root / "result.json").unlink()
                else:
                    self.assertFalse((self.root / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
