"""Public Cargo/process contracts, also executed from wheel and sdist installs."""

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cargo_examples import discover_examples, run_examples
from research_repo_tools.process import run_command


class TestLiveProcess(unittest.TestCase):
    def probe(self, body: str, *, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", body], input=stdin, capture_output=True, timeout=20, check=False)

    def test_inherited_and_asserted_streams_preserve_exact_bytes_and_stdin(self) -> None:
        payload = b"caf\xc3\xa9\r\nline\n\xff"
        child = "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stderr.buffer.write(b'err\\xff\\r\\n')"
        for markers in ((), (b"\xc3\xa9\r\nline",)):
            with self.subTest(markers=markers):
                result = self.probe(
                    "import sys\nfrom research_repo_tools.process import run_command_live\n"
                    f"r=run_command_live(sys.executable, ['-I','-X','utf8','-c',{child!r}], stdout_markers={markers!r})\n"
                    "assert r.returncode == 0 and r.stdout is None and r.stderr is None\n",
                    stdin=payload,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, payload)
                self.assertEqual(result.stderr, b"err\xff\r\n")

    def test_live_output_arrives_before_child_can_finish(self) -> None:
        # A handshake proves liveness without guessing how quickly a host starts
        # Python. The child cannot finish until the observer receives its stdout.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            child = (
                "import pathlib,sys,time\nprint('visible',flush=True)\n"
                "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.01)\nprint('finished',flush=True)\n"
            )
            for markers in ((), (b"visible", b"finished")):
                with self.subTest(markers=markers):
                    release.unlink(missing_ok=True)
                    body = (
                        "import sys\nfrom research_repo_tools.process import run_command_live\n"
                        f"run_command_live(sys.executable,['-I','-c',{child!r},{str(release)!r}],timeout=8,stdout_markers={markers!r})"
                    )
                    with subprocess.Popen([sys.executable, "-I", "-c", body], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as runner:
                        observed: list[bytes] = []
                        ready = threading.Event()

                        def read_line() -> None:
                            assert runner.stdout is not None
                            observed.append(runner.stdout.readline())
                            ready.set()

                        reader = threading.Thread(target=read_line)
                        reader.start()
                        try:
                            self.assertTrue(ready.wait(12), "no stdout before child deadline")
                            self.assertEqual(observed, [b"visible\r\n" if os.name == "nt" else b"visible\n"])
                            self.assertIsNone(runner.poll())
                            release.touch()
                            stdout, stderr = runner.communicate(timeout=12)
                            self.assertEqual(runner.returncode, 0, stderr)
                            self.assertIn(b"finished", stdout)
                        finally:
                            if runner.poll() is None:
                                runner.kill()
                            runner.wait(timeout=5)
                            reader.join(timeout=5)

    def test_markers_span_chunks_without_rewriting_output(self) -> None:
        payload = b"x" * 65534 + b"boundary\r\n" + b"y" * 65540
        # Keep the command below Windows' command-line length limit.
        child = "import sys;sys.stdout.buffer.write(b'x'*65534+b'boundary\\r\\n'+b'y'*65540)"
        result = self.probe(
            "import sys\nfrom research_repo_tools.process import run_command_live\n"
            f"run_command_live(sys.executable,['-I','-c',{child!r}],stdout_markers=(b'boundary\\r\\n',))"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, payload)

    def test_failure_precedes_missing_markers_and_preserves_status(self) -> None:
        for check in (True, False):
            with self.subTest(check=check):
                result = self.probe(
                    "import subprocess,sys\nfrom research_repo_tools.process import run_command_live\n"
                    "try:\n"
                    f" r=run_command_live(sys.executable,['-I','-c','raise SystemExit(19)'],check={check!r},stdout_markers=(b'absent',))\n"
                    " assert r.returncode == 19\n"
                    "except subprocess.CalledProcessError as e:\n assert e.returncode == 19 and e.stdout is None\n"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        result = self.probe(
            "import sys\nfrom research_repo_tools.process import run_command_live\n"
            "run_command_live(sys.executable,['-I','-c',\"print('stderr only',file=__import__('sys').stderr)\"],stdout_markers=(b'stderr only',))"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"missing expected stdout markers", result.stderr)

    def test_timeout_reaps_child_and_cleans_spool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = "import pathlib,sys,time;print('started',flush=True);time.sleep(3);pathlib.Path(sys.argv[1]).touch()"
            body = (
                "import subprocess,sys,tempfile\nfrom research_repo_tools.process import run_command_live\n"
                f"tempfile.tempdir={temporary!r}\n"
                "try:\n"
                f" run_command_live(sys.executable,['-I','-c',{child!r},{str(root / 'survived')!r}],timeout=1,stdout_markers=(b'absent',))\n"
                "except subprocess.TimeoutExpired as e:\n assert e.timeout == 1 and e.stdout is None\n"
                "else: raise AssertionError('timeout was lost')\n"
            )
            result = self.probe(body)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"started", result.stdout)
            self.assertEqual(list(root.iterdir()), [])
            # A later real command gives the killed child enough time to expose
            # an incorrect timeout implementation without a separate sleep test.
            run_command(sys.executable, ["-I", "-c", "import time;time.sleep(3)"])
            self.assertFalse((root / "survived").exists())

    def test_invalid_marker_options_never_launch(self) -> None:
        result = self.probe(
            "import sys\nfrom research_repo_tools.process import run_command_live\n"
            "for markers in (b'raw', 'text', [b''], [1]):\n"
            " try: run_command_live('missing-executable',stdout_markers=markers)\n"
            " except TypeError: pass\n"
            " else: raise AssertionError('invalid markers accepted')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stdout_backpressure_cannot_block_the_deadline(self) -> None:
        child = "import sys,time;sys.stdout.buffer.write(b'x'*1000000);sys.stdout.flush();time.sleep(30)"
        body = (
            "import subprocess,sys\nfrom research_repo_tools.process import run_command_live\n"
            "try:\n"
            f" run_command_live(sys.executable,['-I','-c',{child!r}],timeout=1,stdout_markers=(b'x',))\n"
            "except subprocess.TimeoutExpired as e:\n assert e.timeout == 1 and e.cmd[0] == sys.executable\n"
            "else: raise AssertionError('lost deadline')\n"
        )
        with subprocess.Popen([sys.executable, "-I", "-c", body], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as runner:
            try:
                # Deliberately do not read: the forwarding sink must fill up.
                runner.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Drain to release an old blocked implementation and its child
                # before reporting the regression; never leave a child behind.
                runner.communicate(timeout=10)
                self.fail("stdout backpressure bypassed the configured deadline")
            _stdout, stderr = runner.communicate(timeout=5)
            self.assertEqual(runner.returncode, 0, stderr)


class TestNativeCargo(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cargo-examples-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "consumer café"
        self.root.mkdir()
        self.write(
            "Cargo.toml",
            '[package]\nname="example-fixture"\nversion="0.0.0"\nedition="2021"\n'
            '[features]\ndefault=["ordinary"]\nordinary=[]\ndiagnostics=[]\ngated=[]\n'
            '[[example]]\nname="diagnostics"\npath="programs/diagnostics.rs"\n'
            '[[example]]\nname="gated"\nrequired-features=["gated"]\n',
        )
        self.write("src/lib.rs", "")
        self.write(".cargo/config.toml", '[build]\ntarget-dir="custom target"\n')
        self.write(
            "examples/flat.rs",
            'fn main() { assert!(cfg!(feature="ordinary")); '
            "let args: Vec<String> = std::env::args().skip(1).collect(); "
            'if args.first().map(String::as_str) == Some("fail") { std::process::exit(23); } '
            'if args.first().map(String::as_str) == Some("wait") { std::thread::sleep(std::time::Duration::from_secs(30)); } '
            'std::fs::write("flat-ran", args.join("|" )).unwrap(); println!("ordinary café"); }',
        )
        self.write(
            "examples/nested/main.rs", 'fn main() { assert!(cfg!(feature="ordinary")); std::fs::write("nested-ran", "yes").unwrap(); println!("nested"); }'
        )
        self.write(
            "programs/diagnostics.rs",
            'fn main() { let value = if cfg!(feature="diagnostics") {"real diagnostics"} else {"stub"}; '
            'std::fs::write("diagnostics-ran",value).unwrap(); println!("{}",value); }',
        )
        self.write(
            "examples/gated.rs",
            'fn main() { assert!(!cfg!(feature="ordinary")); assert!(cfg!(feature="gated")); std::fs::write("gated-ran","yes").unwrap(); println!("gated"); }',
        )
        # Ignore ambient target selection/output paths; the fixture itself owns
        # the path test and has no registry dependencies or network requirement.
        environment = {key: value for key, value in os.environ.items() if key not in {"CARGO_TARGET_DIR", "CARGO_BUILD_TARGET"}}
        self.enterContext(patch.dict(os.environ, environment, clear=True))
        run_command("cargo", ["generate-lockfile", "--offline"], cwd=self.root)

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def test_native_discovery_feature_builds_and_actual_artifact_paths(self) -> None:
        examples = discover_examples(self.root)
        self.assertEqual([item.name for item in examples], ["diagnostics", "flat", "gated", "nested"])
        by_name = {item.name: item for item in examples}
        self.assertEqual(by_name["nested"].source, self.root / "examples/nested/main.rs")
        self.assertEqual(by_name["diagnostics"].source, self.root / "programs/diagnostics.rs")
        self.assertEqual(by_name["gated"].required_features, ("gated",))
        self.write(
            "examples.toml",
            'schema=1\nprofile="dev"\n[examples.flat]\nargs=["a b; $(literal)", "", "café"]\nexpect=["ordinary café"]\n'
            '[examples.nested]\nexpect=["nested"]\n'
            '[examples.diagnostics]\nfeatures=["diagnostics"]\nexpect=["real diagnostics"]\n'
            '[examples.gated]\nfeatures=["gated"]\nno-default-features=true\nexpect=["gated"]\n',
        )
        original = subprocess.run
        commands: list[list[str]] = []

        def record(*args, **kwargs):
            commands.append(args[0])
            return original(*args, **kwargs)

        with patch("subprocess.run", side_effect=record):
            run_examples(self.root, "examples.toml")
        builds = [command for command in commands if len(command) > 1 and command[1] == "build"]
        self.assertEqual(len(builds), 3)
        self.assertEqual(sum("--examples" in command for command in builds), 1)
        self.assertNotIn("--features", builds[0])
        self.assertEqual((self.root / "flat-ran").read_text(encoding="utf-8"), "a b; $(literal)||café")
        self.assertEqual((self.root / "diagnostics-ran").read_text(encoding="utf-8"), "real diagnostics")
        self.assertTrue((self.root / "nested-ran").is_file())
        self.assertTrue((self.root / "gated-ran").is_file())
        self.assertTrue((self.root / "custom target").is_dir())
        self.assertFalse((self.root / "target").exists())

    def test_cli_exit_timeout_and_assertion_failures_remain_failures(self) -> None:
        for policy, message in (
            ('args=["fail"]', "exit status 23"),
            ('args=["wait"]\ntimeout=1', "timed out"),
            ('expect=["consumer-owned missing assertion"]', "missing expected stdout markers"),
        ):
            with self.subTest(policy=policy):
                self.write("examples.toml", 'schema=1\nprofile="dev"\ninclude=["flat","nested"]\n[examples.flat]\n' + policy + "\n")
                result = run_command(
                    sys.executable,
                    ["-I", "-X", "utf8", "-m", "research_repo_tools", "--root", str(self.root), "validation", "cargo-examples", "examples.toml"],
                    cwd=self.root,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse((self.root / "nested-ran").exists())

    def test_missing_required_feature_artifact_fails_before_execution(self) -> None:
        self.write("examples.toml", 'schema=1\nprofile="dev"\ninclude=["flat","gated"]\n')
        with self.assertRaisesRegex(ValueError, "omitted executable.*gated"):
            run_examples(self.root, "examples.toml")
        self.assertFalse((self.root / "flat-ran").exists())


if __name__ == "__main__":
    unittest.main()
