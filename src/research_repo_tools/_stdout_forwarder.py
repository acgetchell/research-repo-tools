"""Private isolated stdout forwarder; its parent owns deadlines and cleanup.

Executed by filename with -I -S so both installed distributions work without
import-path or optional-dependency assumptions. No package imports belong here.
"""

import os
import sys
import time
from pathlib import Path
from typing import BinaryIO


def open_spool(spool: Path) -> BinaryIO:
    """Reopen a private delete-on-close spool with independent read position."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_TEMPORARY", 0)
    return os.fdopen(os.open(spool, flags), "rb", buffering=0)


def forward(spool: Path, done: Path) -> None:
    """Forward exact spool bytes, stopping at the parent's published endpoint."""
    with open_spool(spool) as reader, os.fdopen(os.dup(1), "wb", buffering=0) as output:
        endpoint = None
        while True:
            if endpoint is None and done.exists():
                endpoint = int(done.read_bytes())
            count = 65536 if endpoint is None else min(65536, endpoint - reader.tell())
            if count <= 0:
                return
            chunk = reader.read(count)
            if not chunk:
                time.sleep(0.02)
                continue
            pending = memoryview(chunk)
            while pending:
                written = output.write(pending)
                if not written:
                    raise OSError("stdout accepted no bytes")
                pending = pending[written:]


if __name__ == "__main__":
    forward(Path(sys.argv[1]), Path(sys.argv[2]))
