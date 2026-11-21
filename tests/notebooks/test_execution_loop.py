"""Scoped execution-loop ownership; real kernel coverage lives in consumers."""

import asyncio
import threading
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from unittest.mock import Mock

import nbformat
import pytest
import zmq

from research_repo_tools import notebooks


@pytest.mark.parametrize(
    ("configured", "writable", "expected"),
    [
        ("/scratch", {"/scratch"}, "/scratch"),
        ("/" + "é" * 50, {"/" + "é" * 50, "/tmp"}, "/tmp"),
        ("/scratch", {"/tmp"}, "/tmp"),
        ("/scratch", set(), None),
    ],
)
def test_socket_directory_uses_a_writable_parent_with_room_for_utf8_channel_names(monkeypatch, configured, writable, expected):
    """Model host temp locations and the byte limit independently of the test runner's filesystem."""
    attempted = []

    def temporary_directory(*, prefix, dir):
        attempted.append(dir)
        if dir not in writable:
            raise PermissionError(dir)
        return nullcontext(f"{dir}/{prefix}abcdefgh")

    monkeypatch.setattr(notebooks.tempfile, "gettempdir", lambda: configured)
    monkeypatch.setattr(notebooks.tempfile, "TemporaryDirectory", temporary_directory)
    monkeypatch.setattr(zmq, "IPC_PATH_MAX_LEN", 100, raising=False)
    if expected is None:
        with pytest.raises(OSError, match="Set TMPDIR to a short writable path"), notebooks._kernel_socket_directory():
            pytest.fail("no suitable socket parent")
    else:
        with notebooks._kernel_socket_directory() as selected:
            assert selected.startswith(f"{expected}/")
    assert attempted == ([configured] if expected == configured else [configured, "/tmp"])


@pytest.mark.parametrize("failure", [False, True])
def test_execution_closes_its_loop_and_preserves_running_caller(tmp_path, monkeypatch, failure):
    observed = []
    caller_thread = threading.get_ident()

    async def execute(*args, **kwargs):
        observed.append(asyncio.get_running_loop())
        assert threading.get_ident() != caller_thread
        if failure:
            raise ValueError("kernel failure")

    monkeypatch.setattr(notebooks, "_execute_async", execute)

    def forbidden(*args):
        pytest.fail("execution changed global event-loop state")

    async def caller():
        caller_loop = asyncio.get_running_loop()
        with monkeypatch.context() as scoped:
            scoped.setattr(asyncio, "set_event_loop", forbidden)
            scoped.setattr(asyncio, "set_event_loop_policy", forbidden)
            if failure:
                with pytest.raises(ValueError, match="kernel failure"):
                    notebooks._execute(nbformat.v4.new_notebook(), cwd=tmp_path, timeout=1, env={}, progress=[])
            else:
                notebooks._execute(nbformat.v4.new_notebook(), cwd=tmp_path, timeout=1, env={}, progress=[])
        assert asyncio.get_running_loop() is caller_loop
        assert len(observed) == 1 and observed[0] is not caller_loop and observed[0].is_closed()
        await asyncio.sleep(0)

    with asyncio.Runner(loop_factory=asyncio.EventLoop) as runner:
        runner.run(caller())


@pytest.mark.parametrize("boundary", ["result", "submit"])
def test_interrupt_waits_for_cancellation_cleanup_and_closes_loop(tmp_path, monkeypatch, boundary):
    started = threading.Event()
    cleaned = threading.Event()
    cancelled = threading.Event()
    observed = []

    async def execute(*args, **kwargs):
        observed.append(asyncio.get_running_loop())
        try:
            started.set()
            await asyncio.sleep(5)  # Bound the regression if cancellation is missed.
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finally:
            cleaned.set()

    def interrupt(self, timeout=None):
        assert started.wait(5)
        raise KeyboardInterrupt

    submit = ThreadPoolExecutor.submit

    def interrupt_submission(self, *args, **kwargs):
        submit(self, *args, **kwargs)
        interrupt(self)

    monkeypatch.setattr(notebooks, "_execute_async", execute)
    if boundary == "submit":
        monkeypatch.setattr(ThreadPoolExecutor, "submit", interrupt_submission)
    else:
        monkeypatch.setattr(Future, "result", interrupt)
    with pytest.raises(KeyboardInterrupt):
        notebooks._execute(nbformat.v4.new_notebook(), cwd=tmp_path, timeout=1, env={}, progress=[])
    assert cancelled.is_set() and cleaned.is_set() and observed[0].is_closed()
    assert not any(thread.name.startswith("notebook-execution") for thread in threading.enumerate())


def test_caller_warning_policy_reaches_execution_worker(tmp_path, monkeypatch):
    async def execute(*args, **kwargs):
        warnings.warn("Proactor event loop does not implement add_reader", RuntimeWarning)

    monkeypatch.setattr(notebooks, "_execute_async", execute)
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Proactor event loop does not implement add_reader", category=RuntimeWarning)
        with pytest.raises(RuntimeWarning, match="Proactor"):
            notebooks._execute(nbformat.v4.new_notebook(), cwd=tmp_path, timeout=1, env={}, progress=[])


def test_failed_submission_does_not_create_a_loop(tmp_path, monkeypatch):
    runner = Mock(side_effect=AssertionError("rejected submission created an event-loop runner"))

    def rejected(*args, **kwargs):
        raise RuntimeError("worker submission rejected")

    monkeypatch.setattr(asyncio, "Runner", runner)
    monkeypatch.setattr(ThreadPoolExecutor, "submit", rejected)
    with pytest.raises(RuntimeError, match="worker submission rejected"):
        notebooks._execute(nbformat.v4.new_notebook(), cwd=tmp_path, timeout=1, env={}, progress=[])
    runner.assert_not_called()
    assert not any(thread.name.startswith("notebook-execution") for thread in threading.enumerate())
