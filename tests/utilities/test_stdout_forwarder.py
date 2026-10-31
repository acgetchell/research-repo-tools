"""Completion visibility must not depend on reopening a notification file."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from research_repo_tools._stdout_forwarder import forward


@pytest.mark.parametrize("endpoint", [0, 3, 11])
def test_visible_notification_does_not_require_a_read_open(tmp_path, endpoint):
    payload = b"caf\xc3\xa9\r\n\xffend"
    assert len(payload) == 11
    spool = tmp_path / "spool"
    spool.write_bytes(payload + b"later descendant bytes")
    done = tmp_path / "stderr"
    # The legacy-shaped entry is a decoy, and makes the defective reader's
    # conflicting open fail immediately instead of waiting for a notification.
    done.write_bytes(str(endpoint).encode("ascii"))
    for name in (f"stderr.{endpoint}.done", "stdout.0.done", "stderr.invalid.done", "stderr.-1.done", "stderr.999.tmp"):
        (tmp_path / name).touch()
    original_open = Path.open
    opened = []

    def guard(path, *args, **kwargs):
        if path.parent == tmp_path and path.name.startswith(("stderr", "stdout")):
            opened.append(path.name)
            raise PermissionError("visible notification still has a conflicting publisher handle")
        return original_open(path, *args, **kwargs)

    with tempfile.TemporaryFile() as output, patch.object(Path, "open", guard):
        forward(spool, done, output.fileno())
        output.seek(0)
        assert output.read() == payload[:endpoint]
    assert opened == []
