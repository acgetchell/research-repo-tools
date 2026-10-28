"""Small suite transport regressions, also run from installed wheel and sdist."""

import io
import json
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.cli import main
from research_repo_tools.criterion_baselines import load_saved_sample, validate_saved_sample
from research_repo_tools.criterion_inventory import CriterionInventory, serialize_inventory
from research_repo_tools.criterion_suites import SuiteRun, assemble_saved_suites, pack_saved_suite
from research_repo_tools.evidence import Provenance, deterministic_json
from research_repo_tools.host_metadata import HostMetadata, serialize_host

IDS = ("flat café", "group / size 2")
REVISION = "a" * 40
RUN = SuiteRun(
    Provenance(REVISION, context=(("command", "native bench"), ("features", "exact"))),
    "run-7-attempt-2",
    HostMetadata("Linux", "x86_64", "first CPU", 2, 4, None),
    10,
    20,
)


def prepare(root: Path, paths: tuple[str, str] = ("group/a", "group/b"), *, declared_paths: bool = True) -> None:
    inventory = CriterionInventory((("left", (IDS[0], "excluded")), ("right", (IDS[1],)), ("inactive", ("also excluded",))), ("excluded", "also excluded"))
    (root / "inventory.json").write_bytes(serialize_inventory(inventory))
    config = (
        'schema=2\ninventory="inventory.json"\nsaved-sample="v1.2.3"\n'
        'raw-files=["benchmark.json","estimates.json","sample.json","tukey.json"]\n'
        f"required={json.dumps(IDS)}\n[sample-counts]\n"
        '"flat café"=3\n"group / size 2"=4\n'
    )
    if declared_paths:
        config += "[storage-paths]\n" + "\n".join(f"{json.dumps(identity)}={json.dumps(path)}" for identity, path in zip(IDS, paths, strict=True)) + "\n"
    (root / "saved.toml").write_text(config, encoding="utf-8", newline="\n")
    for index, (suite, identity, storage) in enumerate(zip(("left", "right"), IDS, paths, strict=True)):
        values = {
            "benchmark.json": {"full_id": identity},
            "estimates.json": {
                statistic: {"point_estimate": 10, "confidence_interval": {"lower_bound": 8, "upper_bound": 12, "confidence_level": 0.95}}
                for statistic in ("mean", "median")
            },
            "sample.json": {"iters": list(range(1, 4 + index)), "times": [10 * value for value in range(1, 4 + index)]},
            "tukey.json": [1, 2, 3, 4],
        }
        for sample in ("new", "v1.2.3"):
            directory = root / suite / storage / sample
            directory.mkdir(parents=True)
            for name, value in values.items():
                (directory / name).write_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\r\n")
    (root / "host.json").write_bytes(serialize_host(RUN.host))


def package(root: Path) -> tuple[str, ...]:
    outputs = []
    for index, suite in enumerate(("left", "right")):
        run = replace(RUN, host=replace(RUN.host, cpu=f"CPU {index}"))
        output = f"{suite}.tar.gz"
        result = pack_saved_suite(root, "saved.toml", suite, output, run=run, criterion_dir=suite)
        assert result.policy.expected == (IDS[index],)
        outputs.append(output)
    return tuple(outputs)


def read_archive(path: Path) -> dict[str, bytes]:
    result = {}
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            source = archive.extractfile(member)
            assert source is not None
            result[member.name] = source.read()
    return result


def write_archive(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


def assemble(root: Path, archives: tuple[str, ...], **kwargs):
    return assemble_saved_suites(root, "saved.toml", archives, "complete.tar.gz", revision=REVISION, session=RUN.session, **kwargs)


class TestSuiteConsumer(unittest.TestCase):
    def test_round_trip_retains_exact_bytes_and_each_original_host_without_probes(self):
        with tempfile.TemporaryDirectory(prefix="suites café ") as temporary:
            root = Path(temporary)
            prepare(root)
            with patch("research_repo_tools.host_metadata.capture_host", side_effect=AssertionError("must retain original hosts")):
                archives = package(root)
                result = assemble(root, tuple(reversed(archives)))
            self.assertEqual(result.policy.expected, IDS)
            self.assertEqual([case.sample_count for case in result.cases], [3, 4])
            extract_archive(root / "complete.tar.gz", root / "output")
            full = load_saved_sample(root, "saved.toml")
            self.assertEqual(validate_saved_sample(root / "output", full.saved_sample, full.policy).policy, full.policy)
            for index, suite in enumerate(("left", "right")):
                original = read_archive(root / archives[index])
                manifest = root / f"output/release-suites/{index}.json"
                self.assertEqual(manifest.read_bytes(), original.pop("suite.json"))
                self.assertEqual(json.loads(manifest.read_bytes())["run"]["host"]["cpu"], f"CPU {index}")
                for name, payload in original.items():
                    self.assertEqual((root / "output" / name).read_bytes(), payload)
                    self.assertEqual((root / suite / name).read_bytes(), payload)
                    self.assertTrue(payload.endswith(b"\r\n"))
            self.assertEqual((root / "output/criterion-inventory.json").read_bytes(), (root / "inventory.json").read_bytes())

    def test_suite_selection_validates_full_policy_before_filtering_and_honors_exclusions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare(root)
            selected = load_saved_sample(root, "saved.toml", suite="right")
            self.assertEqual(selected.policy.sample_counts, ((IDS[1], 4),))
            self.assertEqual(selected.storage_paths, ((IDS[1], "group/b"),))
            for suite in ("missing", "inactive"):
                with self.subTest(suite=suite), self.assertRaises(ValueError):
                    load_saved_sample(root, "saved.toml", suite=suite)
            original = (root / "saved.toml").read_text(encoding="utf-8")
            for change in (
                original.replace("required=[", 'required=["missing",'),
                original.replace('"group / size 2"=4', '"group / size 2"=0'),
                original.replace('"group / size 2"=4\n', ""),
                original.replace('"group/b"', '"GROUP/A"'),
            ):
                (root / "saved.toml").write_text(change, encoding="utf-8", newline="\n")
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_saved_sample(root, "saved.toml", suite="left")
            (root / "saved.toml").write_text(original.replace('inventory="inventory.json"', f"expected={json.dumps(IDS)}"), encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "requires a retained inventory"):
                load_saved_sample(root, "saved.toml", suite="left")
            with self.assertRaisesRegex(ValueError, "requires a retained inventory"):
                package(root)

    def test_incomplete_duplicate_or_mixed_attempts_never_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare(root)
            archives = package(root)
            for inputs in ((), archives[:1], (*archives, archives[0]), (archives[0], archives[0])):
                with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                    assemble(root, inputs)
                self.assertFalse((root / "complete.tar.gz").exists())
            for revision, session in (("b" * 40, RUN.session), (REVISION, "run-7-attempt-1")):
                with self.subTest(revision=revision, session=session), self.assertRaisesRegex(ValueError, "revision or collection session"):
                    assemble_saved_suites(root, "saved.toml", archives, "complete.tar.gz", revision=revision, session=session)
            (root / "complete.tar.gz").write_bytes(b"previous evidence")
            with self.assertRaisesRegex(ValueError, "already exists"):
                assemble(root, archives)
            self.assertEqual((root / "complete.tar.gz").read_bytes(), b"previous evidence")
            before = (root / archives[0]).read_bytes()
            with self.assertRaisesRegex(ValueError, "already exists"):
                pack_saved_suite(root, "saved.toml", "left", archives[0], run=RUN, criterion_dir="left")
            self.assertEqual((root / archives[0]).read_bytes(), before)

    def test_corrupt_contract_provenance_files_and_digests_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare(root)
            archives = package(root)
            original = read_archive(root / archives[0])
            changes = (
                ("schema", None, "other"),
                ("suite", None, "missing"),
                ("contract", "saved_sample", "v1.2.4"),
                ("contract", "raw_files", ["benchmark.json", "sample.json", "estimates.json"]),
                ("contract", "inventory", {"suites": {"left": [IDS[0]]}}),
                ("contract", "policy", {}),
                ("contract", "storage_paths", {}),
                ("run", "session", "previous-attempt"),
                ("run", "finished_at", 1),
                ("run", "started_at", True),
                ("run", "host", {}),
                ("run", "provenance", {}),
                ("files", None, {}),
            )
            for section, field, value in changes:
                manifest = json.loads(original["suite.json"])
                if field is None:
                    manifest[section] = value
                else:
                    manifest[section][field] = value
                write_archive(root / archives[0], {**original, "suite.json": deterministic_json(manifest)})
                with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                    assemble(root, archives)
                self.assertFalse((root / "complete.tar.gz").exists())
            for mode in ("missing", "extra", "digest", "fresh-saved", "non-json"):
                files = dict(original)
                path = "group/a/new/tukey.json"
                if mode == "missing":
                    del files[path]
                elif mode == "extra":
                    files["unexpected.txt"] = b"extra"
                elif mode == "digest":
                    for sample in ("new", "v1.2.3"):
                        files[f"group/a/{sample}/tukey.json"] = b"[5,6,7,8]"
                else:
                    files[path] = b"[5,6,7,8]" if mode == "fresh-saved" else b"invalid JSON"
                write_archive(root / archives[0], files)
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    assemble(root, archives)
                self.assertFalse((root / "complete.tar.gz").exists())

    def test_cross_suite_portable_aliases_and_overlap_fail_without_overwriting(self):
        for paths in (("Group/a", "group/b"), ("café/a", "cafe\u0301/b"), ("same", "same"), ("group", "group/new/benchmark.json/other")):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                prepare(root, paths, declared_paths=False)
                archives = package(root)
                with self.assertRaises(ValueError):
                    assemble(root, archives)
                self.assertFalse((root / "complete.tar.gz").exists())

    def test_unsafe_members_and_limits_use_the_shared_extractor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare(root)
            archives = package(root)
            original = read_archive(root / archives[0])
            with self.assertRaisesRegex(ValueError, "combined suite content"):
                assemble(root, archives, limits=ArchiveLimits(members=12))
            self.assertFalse((root / "complete.tar.gz").exists())
            for name in ("../escape", "CON/file", "Group/a/new/extra", "group/a/new/extra."):
                write_archive(root / archives[0], {**original, name: b"unsafe"})
                with tarfile.open(root / archives[0]) as archive:
                    self.assertIn(name, archive.getnames())
                with self.subTest(name=name), self.assertRaises(ValueError):
                    assemble(root, archives)
                self.assertFalse((root / "complete.tar.gz").exists())
                self.assertFalse((root / "escape").exists())
            write_archive(root / archives[0], original)
            with tarfile.open(root / archives[0], "w") as archive:
                member = tarfile.TarInfo("link")
                member.type = tarfile.SYMTYPE
                member.linkname = "group/a"
                archive.addfile(member)
            with self.assertRaises(ValueError):
                assemble(root, archives)
            for limits in (ArchiveLimits(members=1), ArchiveLimits(content_bytes=1), ArchiveLimits(archive_bytes=1)):
                with self.subTest(limits=limits), self.assertRaises(ValueError):
                    pack_saved_suite(root, "saved.toml", "left", "too-large.tar.gz", run=RUN, criterion_dir="left", limits=limits)
                self.assertFalse((root / "too-large.tar.gz").exists())

    def test_public_cli_verifies_packs_and_assembles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare(root)
            base = ["--root", str(root), "performance"]
            with redirect_stdout(io.StringIO()):
                for suite in ("left", "right"):
                    self.assertEqual(main([*base, "verify-saved", "saved.toml", "--suite", suite, "--criterion-dir", suite]), 0)
                    self.assertEqual(
                        main(
                            [
                                *base,
                                "pack-suite",
                                "saved.toml",
                                suite,
                                "--criterion-dir",
                                suite,
                                "--output",
                                f"{suite}.tar.gz",
                                "--revision",
                                REVISION,
                                "--session",
                                RUN.session,
                                "--host",
                                "host.json",
                                "--started-at",
                                "10",
                                "--finished-at",
                                "20",
                                "--context",
                                "features=exact",
                                "--context",
                                "command=native bench",
                            ]
                        ),
                        0,
                    )
                self.assertEqual(
                    main(
                        [
                            *base,
                            "assemble-suites",
                            "saved.toml",
                            "left.tar.gz",
                            "right.tar.gz",
                            "--output",
                            "all.tar.gz",
                            "--revision",
                            REVISION,
                            "--session",
                            RUN.session,
                        ]
                    ),
                    0,
                )
            self.assertTrue((root / "all.tar.gz").is_file())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main([*base, "verify-saved", "saved.toml", "--suite", "inactive"]), 1)
            for value in (True, -1, 21):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    replace(RUN, started_at=value)


if __name__ == "__main__":
    unittest.main()
