"""Fresh system-wide Windows counts without starting a shell or querying WMI."""

import ctypes
import struct
import sys
from typing import Any


class _MemoryStatus(ctypes.Structure):
    # MEMORYSTATUSEX uses fixed-width Windows types, including on 64-bit hosts.
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("load", ctypes.c_uint32),
        *[
            (name, ctypes.c_uint64)
            for name in ("total_physical", "available_physical", "total_pagefile", "available_pagefile", "total_virtual", "available_virtual", "extended")
        ],
    ]


def _kernel32() -> Any:
    if sys.platform != "win32":
        raise OSError("Windows hardware APIs are unavailable")
    return ctypes.WinDLL("kernel32")


def _core_records(payload: bytes) -> int | None:
    """Count variable-sized RelationProcessorCore records across every group."""
    offset = cores = 0
    while offset < len(payload):
        if len(payload) - offset < 8:
            return None
        relationship, size = struct.unpack_from("<II", payload, offset)
        # Header plus PROCESSOR_RELATIONSHIP's fixed fields; affinity masks
        # follow and vary with architecture/group count, so advance by Size.
        if relationship != 0 or size < 32 or size > len(payload) - offset:
            return None
        cores += 1
        offset += size
    return cores or None


def _physical_cores(query: Any) -> int | None:
    size = ctypes.c_uint32()
    query(0, None, ctypes.byref(size))
    # A topology change can grow the required buffer between the two calls.
    # Bound both retries and allocation, and never parse a failed API result.
    for _ in range(3):
        if not 32 <= size.value <= 16 * 1024 * 1024:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if query(0, buffer, ctypes.byref(size)):
            return _core_records(buffer.raw[: size.value]) if size.value <= len(buffer) else None
        if size.value <= len(buffer):
            return None
    return None


def counts() -> tuple[int | None, int | None, int | None]:
    """Observe active cores/threads across all groups and total physical RAM.

    No observations are cached. Missing APIs and failed queries stay unknown.
    """
    try:
        kernel = _kernel32()
        topology = kernel.GetLogicalProcessorInformationEx
        topology.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        topology.restype = ctypes.c_int32
        active = kernel.GetActiveProcessorCount
        active.argtypes = [ctypes.c_uint16]
        active.restype = ctypes.c_uint32
        memory = kernel.GlobalMemoryStatusEx
        memory.argtypes = [ctypes.POINTER(_MemoryStatus)]
        memory.restype = ctypes.c_int32
        cores = _physical_cores(topology)
        threads = int(active(0xFFFF)) or None  # ALL_PROCESSOR_GROUPS
        status = _MemoryStatus()
        status.length = ctypes.sizeof(status)
        physical = (int(status.total_physical) or None) if memory(ctypes.byref(status)) else None
        return cores, threads, physical
    except AttributeError, OSError:
        return None, None, None
