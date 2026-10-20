"""Discovery and saved-copy contracts exercised from isolated distributions too."""

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cli import main
from research_repo_tools.complete_runs import CompletePolicy
from research_repo_tools.criterion_baselines import SavedSamplePlan, load_saved_sample, validate_saved_sample
from research_repo_tools.criterion_inventory import (
    CriterionInventory,
    DiscoveryCommand,
    DiscoveryPlan,
    discover_inventory,
    load_discovery,
    parse_benchmark_list,
    parse_inventory,
    serialize_inventory,
)
from research_repo_tools.evidence import deterministic_json

IDS = ("flat café", "group / size 2")
RAW_FILES = ("benchmark.json", "estimates.json", "sample.json", "tukey.json")


def samples(root: Path) -> CompletePolicy:
    for index, identity in enumerate(IDS):
        fresh = root / f"sanitized_{index}" / "new"
        fresh.mkdir(parents=True)
        data = {
            "benchmark.json": {"full_id": identity},
            "estimates.json": {
                stat: {"point_estimate": 10, "confidence_interval": {"lower_bound": 8, "upper_bound": 12, "confidence_level": 0.95}}
                for stat in ("mean", "median")
            },
            "sample.json": {"iters": [1, 2, 3], "times": [10, 20, 30]},
            "tukey.json": [1, 2, 3, 4],
        }
        for filename, value in data.items():
            (fresh / filename).write_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\r\n")
        shutil.copytree(fresh, fresh.parent / "v1.2.3")
    return CompletePolicy(IDS, sample_count=3)


class TestCriterionConsumer(unittest.TestCase):
    def test_saved_runtime_names_required_inventory_and_declared_storage(self):
        with tempfile.TemporaryDirectory(prefix="saved café ") as directory:
            root = Path(directory)
            policy = samples(root / "release output")
            inventory = CriterionInventory((("native", IDS),))
            (root / "inventory.json").write_bytes(serialize_inventory(inventory))
            configuration = """schema=2
inventory="inventory.json"
required=["flat café", "group / size 2"]
raw-files=["benchmark.json", "estimates.json", "sample.json", "tukey.json"]
sample-count=3
[storage-paths]
"flat café"="sanitized_0"
"group / size 2"="sanitized_1"
"""
            (root / "saved.toml").write_text(configuration, encoding="utf-8", newline="\n")
            argv = ["--root", str(root), "performance", "verify-saved", "saved.toml", "--saved-sample", "v1.2.3", "--criterion-dir", "release output"]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 0)
            plan = load_saved_sample(root, "saved.toml", saved_sample="v1.2.3", criterion_dir="release output")
            self.assertEqual(plan.policy, policy)
            for value in ("NEW", "../v1", "bad name", "v1;echo"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    load_saved_sample(root, "saved.toml", saved_sample=value)
            for paths in (
                ((IDS[0], "wrong"),),
                ((IDS[0], "sanitized_1"), (IDS[1], "sanitized_0")),
                ((IDS[0], "same"), (IDS[1], "SAME")),
            ):
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    validate_saved_sample(root / "release output", "v1.2.3", policy, storage_paths=paths)
            # Fresh and saved agree with each other after the whole directory moves.
            (root / "release output/sanitized_0").rename(root / "release output/misplaced")
            with redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main(argv), 1)
            self.assertIn("required storage path", errors.getvalue())
            (root / "inventory.json").write_bytes(serialize_inventory(CriterionInventory((("native", (IDS[0],)),))))
            with self.assertRaisesRegex(ValueError, "omits required"):
                load_saved_sample(root, "saved.toml", saved_sample="v1.2.3")

    def test_native_record_boundaries_preserve_unicode_and_reject_controls(self):
        identities = ("group\u0085case", "group\u2028case", "group\u2029case")
        for newline in ("\n", "\r\n"):
            self.assertEqual(parse_benchmark_list(newline.join(f"{identity}: benchmark" for identity in identities)), identities)
        for separator in ("\v", "\f", "\x1c", "\x1d", "\x1e", "\r"):
            with self.subTest(separator=separator), self.assertRaises(ValueError):
                parse_benchmark_list(f"prefix{separator}case: benchmark\n")
        with tempfile.TemporaryDirectory() as directory:
            code = "import json, sys; sys.stdout.buffer.write(json.loads(sys.argv[1]).encode('utf-8'))"
            listing = "\r\n".join(f"{identity}: benchmark" for identity in identities) + "\r\n"
            plan = DiscoveryPlan((DiscoveryCommand("unicode", (sys.executable, "-I", "-c", code, json.dumps(listing))),))
            self.assertEqual(discover_inventory(Path(directory), plan).expected, identities)

    def test_mixed_case_path_overlays_reach_native_children(self):
        python = Path(sys.executable)
        selected = str(python.parent)
        code = (
            "import os, subprocess, sys; assert os.environ['PATH'] == sys.argv[1]; "
            "subprocess.run([sys.argv[2], '-I', '-c', 'pass'], check=True); print('case: benchmark')"
        )
        with tempfile.TemporaryDirectory(prefix="path café ") as directory:
            root = Path(directory)
            inherited = {key: value for key, value in os.environ.items() if key.casefold() != "path"}
            for spelling in ("Path", "path", "inherited"):
                with self.subTest(spelling=spelling):
                    environment = () if spelling == "inherited" else ((spelling, selected),)
                    base = inherited | ({"Path": selected} if spelling == "inherited" else {"PATH": str(root / "absent")})
                    command = DiscoveryCommand("path", (python.name, "-I", "-c", code, selected, python.name), environment=environment)
                    self.assertEqual(discover_inventory(root, DiscoveryPlan((command,)), env=base).expected, ("case",))
            ambiguous = inherited | {"PATH": selected, "Path": str(root / "absent")}
            with self.assertRaisesRegex(ValueError, "unambiguous"):
                discover_inventory(root, DiscoveryPlan((replace(command, environment=()),)), env=ambiguous)
            self.assertEqual(discover_inventory(root, DiscoveryPlan((replace(command, environment=(("path", selected),)),)), env=ambiguous).expected, ("case",))

    def test_raw_filenames_reject_case_and_unicode_aliases(self):
        policy = CompletePolicy(("case",), sample_count=3)
        for names in (("tukey.json", "TUKEY.json"), ("caf\u00e9.json", "cafe\u0301.json")):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "unique"):
                SavedSamplePlan("criterion", "v1.2.3", policy, raw_files=(*RAW_FILES[:3], *names))
        self.assertEqual(
            SavedSamplePlan("criterion", "v1.2.3", policy, raw_files=(*RAW_FILES[:3], "café.json")).raw_files,
            ("benchmark.json", "café.json", "estimates.json", "sample.json"),
        )

    def test_nonregular_core_files_are_rejected_before_reading(self):
        original = Path.is_file
        for sample in ("new", "v1.2.3"):
            for filename in RAW_FILES[:3]:
                with self.subTest(sample=sample, filename=filename), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    policy = samples(root)
                    target = root / "sanitized_0" / sample / filename

                    def regular(path):
                        return False if path == target else original(path)

                    with patch.object(Path, "is_file", regular), self.assertRaisesRegex(ValueError, "regular"):
                        validate_saved_sample(root, "v1.2.3", policy)

    def test_native_fifo_core_file_rejection_is_bounded(self):
        mkfifo = getattr(os, "mkfifo", None)
        if mkfifo is None:
            self.skipTest("native FIFO creation is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples(root)
            target = root / "sanitized_0/v1.2.3/sample.json"
            target.unlink()
            mkfifo(target)
            self.assertTrue(stat.S_ISFIFO(target.stat().st_mode))
            self.assertFalse(target.is_file())
            code = (
                "import sys\nfrom pathlib import Path\n"
                "from research_repo_tools.complete_runs import CompletePolicy\n"
                "from research_repo_tools.criterion_baselines import validate_saved_sample\n"
                "try:\n    validate_saved_sample(Path(sys.argv[1]), 'v1.2.3', CompletePolicy(('flat café','group / size 2'), sample_count=3))\n"
                "except ValueError as error:\n    assert 'regular' in str(error); sys.stdout.buffer.write(b'rejected nonregular core\\n')\n"
                "else:\n    raise AssertionError('FIFO accepted')\n"
            )
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(root)], capture_output=True, timeout=5, check=True)
            self.assertEqual(result.stdout, b"rejected nonregular core\n")
            self.assertTrue(stat.S_ISFIFO(target.stat().st_mode))

    def test_list_semantic_ids_noise_newlines_and_rejections(self):
        for newline in ("\n", "\r\n"):
            output = newline.join(["Compiling fixture", "Gnuplot unavailable", *(f"{identity}: benchmark" for identity in reversed(IDS)), "", "test: test"])
            self.assertEqual(parse_benchmark_list(output), IDS)
        for output in (
            "noise only",
            ": benchmark",
            " x: benchmark",
            "x:benchmark",
            "x: benchmark extra",
            "x: benchmark ",
            "x\0: benchmark",
            "x: benchmark\nx: benchmark",
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                parse_benchmark_list(output)
        with self.assertRaises(TypeError):
            parse_benchmark_list(b"x: benchmark")

    def test_native_discovery_cwd_environment_and_private_output(self):
        with tempfile.TemporaryDirectory(prefix="inventory café ") as directory:
            root = Path(directory)
            work = root / "working café"
            work.mkdir()
            retained = root / "retained"
            retained.mkdir()
            evidence = retained / "old.json"
            evidence.write_bytes(b"old evidence\r\n")
            script = work / "list.py"
            script.write_text(
                "import os, sys\nfrom pathlib import Path\n"
                "assert Path.cwd().name == 'working café'\nassert os.environ['SELECTED'] == 'suite'\n"
                "home=Path(os.environ['CRITERION_HOME'])\n(home/'discovery.json').write_bytes(b'private')\n"
                "sys.stdout.buffer.write('noise\\r\\ngroup / size 2: benchmark\\r\\nflat café: benchmark\\r\\n'.encode('utf-8'))\n"
                "sys.stderr.buffer.write('warning café\\r\\n'.encode('utf-8'))\n",
                encoding="utf-8",
                newline="\n",
            )
            config = f"""schema=1
required=["flat café"]
[[suites]]
name="suite"
command=[{json.dumps(sys.executable)}, "-I", "-X", "utf8", "list.py"]
cwd="working café"
timeout=20
environment={{SELECTED="suite", criterion_home={json.dumps(str(retained))}}}
"""
            (root / "discovery.toml").write_text(config, encoding="utf-8", newline="\n")
            plan = load_discovery(root, "discovery.toml")
            inventory = discover_inventory(
                root,
                plan,
                env=os.environ | {"SELECTED": "parent", "CRITERION_HOME": str(retained), "PYTHONUTF8": "0", "PYTHONIOENCODING": "ascii"},
            )
            self.assertEqual(inventory.expected, IDS)
            self.assertEqual(parse_inventory(serialize_inventory(inventory)), inventory)
            self.assertEqual(evidence.read_bytes(), b"old evidence\r\n")
            self.assertEqual(list(retained.iterdir()), [evidence])
            for candidate, message in ((replace(plan, required=("missing",)), "omits required"), (replace(plan, excluded=("missing",)), "excluded IDs")):
                with self.assertRaisesRegex(ValueError, message):
                    discover_inventory(root, candidate)
            duplicate = DiscoveryPlan((plan.suites[0], replace(plan.suites[0], name="another")))
            with self.assertRaisesRegex(ValueError, "across suites"):
                discover_inventory(root, duplicate)
            excluded = discover_inventory(root, replace(plan, required=(), excluded=(IDS[0],)))
            self.assertEqual(excluded.expected, (IDS[1],))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--root", str(root), "performance", "discover", "discovery.toml", "--output", "inventory.json"]), 0)
            original = (root / "inventory.json").read_bytes()
            with redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main(["--root", str(root), "performance", "discover", "discovery.toml", "--output", "inventory.json"]), 1)
            self.assertIn("already exists", errors.getvalue())
            self.assertEqual((root / "inventory.json").read_bytes(), original)

    def test_discovery_failures_and_configuration_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = DiscoveryCommand("suite", (sys.executable, "-c", "import sys; print('x: benchmark'); sys.exit(2)"), timeout=10)
            with self.assertRaises(subprocess.CalledProcessError):
                discover_inventory(root, DiscoveryPlan((command,)))
            slow = replace(command, command=(sys.executable, "-c", "import time; time.sleep(10)"), timeout=1)
            with self.assertRaises(subprocess.TimeoutExpired):
                discover_inventory(root, DiscoveryPlan((slow,)))
            invalid = (
                lambda: replace(command, command="native --list"),
                lambda: replace(command, cwd="../outside"),
                lambda: replace(command, timeout=True),
                lambda: replace(command, environment=(("Flags", "1"), ("FLAGS", "2"))),
                lambda: replace(command, environment=(("INVALID=KEY", "1"),)),
                lambda: replace(command, environment=(("KEY", "\0"),)),
                lambda: DiscoveryPlan((command, command)),
                lambda: CriterionInventory((("suite", ("x", "x")),)),
                lambda: CriterionInventory((("suite", ("x",)),), ("x",)),
            )
            with patch("research_repo_tools.criterion_inventory.run_command") as run:
                for build in invalid:
                    with self.assertRaises(ValueError):
                        build()
                (root / "bad.toml").write_bytes(b"schema=1\nsuites=[]\nunknown=1\n")
                with self.assertRaises(ValueError):
                    load_discovery(root, "bad.toml")
                run.assert_not_called()
            raw = json.loads(serialize_inventory(CriterionInventory((("suite", ("x",)),))))
            for key, value in (("schema", "unsupported"), ("suites", {"suite": "x"}), ("excluded", ["missing"]), ("extra", True)):
                with self.assertRaises(ValueError):
                    parse_inventory(deterministic_json(raw | {key: value}))

    def test_saved_complete_bytes_and_cli_inventory_configuration(self):
        with tempfile.TemporaryDirectory(prefix="saved café ") as directory:
            root = Path(directory)
            policy = samples(root / "criterion")
            result = validate_saved_sample(root / "criterion", "v1.2.3", policy, raw_files=RAW_FILES)
            self.assertEqual(tuple(case.full_id for case in result.cases), IDS)
            self.assertEqual(result.cases[0].storage_path, "sanitized_0")
            self.assertTrue(result.cases[0].benchmark.endswith(b"\r\n"))
            (root / "inventory.json").write_bytes(serialize_inventory(CriterionInventory((("suite", IDS),))))
            (root / "saved.toml").write_bytes(
                b'schema=1\ncriterion-dir="criterion"\nsaved-sample="v1.2.3"\ninventory="inventory.json"\nsample-count=3\n'
                b'raw-files=["benchmark.json","estimates.json","sample.json","tukey.json"]\n'
            )
            self.assertEqual(load_saved_sample(root, "saved.toml").policy, policy)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--root", str(root), "performance", "verify-saved", "saved.toml"]), 0)
            self.assertIn("Verified 2", output.getvalue())

    def test_failed_saved_copy_after_success_and_each_raw_file(self):
        for filename in RAW_FILES:
            for failure in ("missing", "malformed", "newline"):
                with self.subTest(filename=filename, failure=failure), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    policy = samples(root)
                    destination = root / "sanitized_0/v1.2.3" / filename
                    if failure == "missing":
                        # Native benchmark success cannot prove that its save succeeded.
                        subprocess.run([sys.executable, "-c", "pass"], check=True)
                        destination.unlink()
                    elif failure == "malformed":
                        destination.write_bytes(b"{broken")
                    else:
                        destination.write_bytes(destination.read_bytes().replace(b"\r\n", b"\n"))
                    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
                    with self.assertRaises(ValueError):
                        validate_saved_sample(root, "v1.2.3", policy, raw_files=RAW_FILES)
                    self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)

    def test_stale_extra_samples_and_statistical_policy_on_both_samples(self):
        for sample in ("new", "v1.2.3"):
            for failure in ("extra", "identity", "count", "interval", "tukey"):
                with self.subTest(sample=sample, failure=failure), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    policy = samples(root)
                    selected = root / "sanitized_0" / sample
                    if failure == "extra":
                        extra = root / "stale" / sample
                        shutil.copytree(selected, extra)
                        (extra / "benchmark.json").write_bytes(b'{"full_id":"stale"}')
                    elif failure == "identity":
                        (selected / "benchmark.json").write_bytes(b'{"full_id":"stale"}')
                    elif failure == "count":
                        (selected / "sample.json").write_bytes(b'{"iters":[1,2],"times":[10,20]}')
                    elif failure == "interval":
                        data = json.loads((selected / "estimates.json").read_bytes())
                        data["mean"]["confidence_interval"]["confidence_level"] = 0.9
                        (selected / "estimates.json").write_bytes(deterministic_json(data))
                    else:
                        for name in ("new", "v1.2.3"):
                            (root / "sanitized_0" / name / "tukey.json").write_bytes(b"invalid identical JSON")
                    with self.assertRaises(ValueError):
                        validate_saved_sample(root, "v1.2.3", policy, raw_files=RAW_FILES)

    def test_unsafe_names_raw_files_and_missing_inventory_never_modify_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = samples(root)
            for name in ("new", "NEW", "base", "change", "report", "../escape", "a/b", "a\\b", "a:b", "CON", "trailing.", "with space"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    validate_saved_sample(root, name, policy)
            for files in (("sample.json",), (*RAW_FILES, "tukey.json"), (*RAW_FILES, "../other.json"), (*RAW_FILES, "binary.bin")):
                with self.assertRaises(ValueError):
                    SavedSamplePlan("criterion", "v1.2.3", policy, raw_files=files)
            with self.assertRaisesRegex(ValueError, "distinct"):
                validate_saved_sample(root, "v1.2.3", policy, fresh_sample="V1.2.3")
            shutil.rmtree(root / "sanitized_0/v1.2.3")
            with self.assertRaisesRegex(ValueError, "inventory differs"):
                validate_saved_sample(root, "v1.2.3", policy)


if __name__ == "__main__":
    unittest.main()
