"""Installed host observations, Windows ABI models and native query timings."""

import ctypes
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from research_repo_tools import _windows_hardware as windows
from research_repo_tools.host_metadata import _hardware_counts, _probe, capture_host


def record(size: int = 48) -> bytes:
    # Literal Windows header followed by opaque processor/group data. Records
    # can differ in size; counting must not assume a fixed C structure stride.
    return struct.pack("<II", 0, size) + bytes(size - 8)


def kernel(payload: bytes, *, threads: int = 128, memory: int = 2**36):
    def topology(relationship, buffer, length):
        assert relationship == 0
        required = ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))
        capacity = required.contents.value
        required.contents.value = len(payload)
        if buffer is None or capacity < len(payload):
            return 0
        ctypes.memmove(buffer, payload, len(payload))
        return 1

    def active(group):
        assert group == 0xFFFF, "counts must include every processor group"
        return threads

    def status(pointer):
        # Write the documented ABI by offset, independently of the Python
        # structure's field names and layout.
        length = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        assert length == 64
        ctypes.memmove(pointer, struct.pack("<II7Q", 64, 50, memory, 123, 456, 789, 111, 222, 0), 64)
        return 1

    return SimpleNamespace(
        GetLogicalProcessorInformationEx=Mock(side_effect=topology),
        GetActiveProcessorCount=Mock(side_effect=active),
        GlobalMemoryStatusEx=Mock(side_effect=status),
    )


class TestWindowsHardware(unittest.TestCase):
    def test_fresh_system_counts_cover_variable_records_and_all_processor_groups(self):
        first = kernel(record(48) * 64 + record(64) * 8)
        second = kernel(record(44) * 2, threads=4, memory=2**33)
        with (
            patch.object(windows, "_kernel32", side_effect=[first, second]),
            patch("research_repo_tools.host_metadata._probe", side_effect=AssertionError("native observations must not launch a shell")),
        ):
            self.assertEqual(_hardware_counts("Windows", Path.cwd(), {}, 1), (72, 128, 2**36))
            self.assertEqual(_hardware_counts("Windows", Path.cwd(), {}, 1), (2, 4, 2**33))
        self.assertEqual(first.GetLogicalProcessorInformationEx.call_count, 2)
        self.assertEqual(second.GetLogicalProcessorInformationEx.call_count, 2)

    def test_invalid_topology_never_publishes_partial_core_counts(self):
        for payload in (
            b"",
            b"short",
            record() + b"short",
            struct.pack("<II", 0, 0),
            struct.pack("<II", 0, 4096) + bytes(40),
            struct.pack("<II", 1, 48) + bytes(40),
        ):
            with self.subTest(payload=payload), patch.object(windows, "_kernel32", return_value=kernel(payload)):
                self.assertEqual(windows.counts(), (None, 128, 2**36))

    def test_topology_growth_retries_without_reading_failed_buffer(self):
        native = kernel(record() * 3)
        query = native.GetLogicalProcessorInformationEx.side_effect
        calls = 0

        def growing(relationship, buffer, length):
            nonlocal calls
            calls += 1
            if calls == 1:
                ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32)).contents.value = 48
                return 0
            return query(relationship, buffer, length)

        native.GetLogicalProcessorInformationEx.side_effect = growing
        with patch.object(windows, "_kernel32", return_value=native):
            self.assertEqual(windows.counts(), (3, 128, 2**36))
        self.assertEqual(calls, 3)

    def test_api_failure_and_unbounded_growth_remain_unknown(self):
        for mode in ("failure", "oversized", "growth", "invalid-success"):
            native = kernel(record())

            def failed(relationship, buffer, length):
                value = ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))
                value.contents.value = 2**31 if mode == "oversized" else value.contents.value + 48
                return int(mode == "invalid-success" and buffer is not None)

            native.GetLogicalProcessorInformationEx.side_effect = failed if mode != "failure" else lambda *args: 0
            native.GetActiveProcessorCount.side_effect = lambda group: 0
            native.GlobalMemoryStatusEx.side_effect = lambda pointer: 0
            with self.subTest(mode=mode), patch.object(windows, "_kernel32", return_value=native):
                self.assertEqual(windows.counts(), (None, None, None))
            self.assertLessEqual(native.GetLogicalProcessorInformationEx.call_count, 4)
        for error in (OSError("unavailable"), AttributeError("missing API")):
            with self.subTest(error=error), patch.object(windows, "_kernel32", side_effect=error):
                self.assertEqual(windows.counts(), (None, None, None))

    def test_missing_native_fields_remain_unknown_without_starting_a_shell(self):
        for native in ((None, None, None), (4, None, 4096), (None, 128, None)):
            with (
                self.subTest(native=native),
                patch.object(windows, "counts", return_value=native),
                patch("research_repo_tools.host_metadata._probe", side_effect=AssertionError("missing counts must not start PowerShell")),
            ):
                self.assertEqual(_hardware_counts("Windows", Path.cwd(), {}, 1), (native[0], native[1] or os.cpu_count(), native[2]))

    def test_native_windows_queries_capture_real_hardware_without_shells(self):
        if sys.platform != "win32":
            self.assertEqual(windows.counts(), (None, None, None))
            return
        with tempfile.TemporaryDirectory(prefix="host café ") as directory:
            root = Path(directory)
            started = time.monotonic()
            native = windows.counts()
            native_elapsed = time.monotonic() - started
            self.assertTrue(all(type(value) is int and value > 0 for value in native), native)
            cores, threads, memory = native
            assert cores is not None and threads is not None and memory is not None
            self.assertLessEqual(cores, threads)
            with patch("research_repo_tools.host_metadata._probe", side_effect=AssertionError("native capture must not launch PowerShell")):
                host = capture_host(root)
            self.assertEqual((host.physical_cores, host.logical_threads, host.memory_bytes), native)
            # One historical query per artifact provides native timing evidence;
            # core-count compatibility is deliberately not required. Firmware
            # totals can include cores disabled in firmware or at OS boot.
            script = (
                "$ErrorActionPreference='Stop'; $cpu=@(Get-CimInstance Win32_Processor); "
                "@{cores=[long]($cpu | Measure-Object NumberOfCores -Sum).Sum; "
                "threads=[long]($cpu | Measure-Object NumberOfLogicalProcessors -Sum).Sum; "
                "memory=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory} | ConvertTo-Json -Compress"
            )
            started = time.monotonic()
            shell = "pwsh" if shutil.which("pwsh") else "powershell"
            output = _probe((shell, "-NoProfile", "-NonInteractive", "-Command", script), root, os.environ, 30)
            self.assertIsNotNone(output, "native CI must exercise the comparison query")
            observed = json.loads(output or "null")
            self.assertEqual(native[2], observed["memory"])
            print(f"Windows host query: native={native_elapsed:.6f}s {native}; CIM={time.monotonic() - started:.6f}s {observed}", flush=True)


if __name__ == "__main__":
    unittest.main()
