"""Scoped execution-loop ownership; real kernel coverage lives in consumers."""

import asyncio
import threading
import warnings
from concurrent.futures import Future

import nbformat
import pytest

from research_repo_tools import notebooks


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


def test_interrupt_waits_for_cancellation_cleanup_and_closes_loop(tmp_path, monkeypatch):
    started = threading.Event()
    cleaned = threading.Event()
    observed = []

    async def execute(*args, **kwargs):
        observed.append(asyncio.get_running_loop())
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    def interrupt(self, timeout=None):
        assert started.wait(5)
        raise KeyboardInterrupt

    monkeypatch.setattr(notebooks, "_execute_async", execute)
    monkeypatch.setattr(Future, "result", interrupt)
    with pytest.raises(KeyboardInterrupt):
        notebooks._execute(nbformat.v4.new_notebook(), cwd=tmp_path, timeout=1, env={}, progress=[])
    assert cleaned.is_set() and observed[0].is_closed()
    assert not any(thread.name.startswith("notebook-execution") for thread in threading.enumerate())


def test_caller_warning_policy_reaches_execution_worker(tmp_path, monkeypatch):
    async def execute(*args, **kwargs):
        warnings.warn("Proactor event loop does not implement add_reader", RuntimeWarning)

    monkeypatch.setattr(notebooks, "_execute_async", execute)
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Proactor event loop does not implement add_reader", category=RuntimeWarning)
        with pytest.raises(RuntimeWarning, match="Proactor"):
            notebooks._execute(nbformat.v4.new_notebook(), cwd=tmp_path, timeout=1, env={}, progress=[])
