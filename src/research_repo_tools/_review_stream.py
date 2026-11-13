"""Live bounded CodeRabbit byte transport, without a shell or stdin consent."""

import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from research_repo_tools.review_protocol import AgentEvents, CompletedReview, ConsentRequired, FailedReview


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def stream_review(
    command: Path,
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    output: Callable[[bytes], None],
    timeout: float | None,
) -> CompletedReview | ConsentRequired | FailedReview:
    events = AgentEvents()
    # Separate file offsets avoid pipe deadlocks and preserve Windows bytes.
    # A descendant retaining stdout cannot prevent the direct child timeout.
    with tempfile.TemporaryDirectory(prefix="rrt-review-") as directory:
        path = Path(directory) / "stdout"
        with path.open("wb") as writer, path.open("rb") as reader:
            process = subprocess.Popen([str(command), *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=writer)
            started = time.monotonic()
            total = 0
            finished = False
            try:
                while True:
                    chunk = reader.read(65536)
                    if chunk:
                        total += len(chunk)
                        if total > 64 * 1024 * 1024:
                            raise ValueError("CodeRabbit stream exceeds the byte limit")
                        output(chunk)
                        events.feed(chunk)
                    elif finished:
                        return events.finish(process.returncode)
                    else:
                        finished = process.poll() is not None
                        if not finished:
                            time.sleep(0.01)
                    if not finished and timeout is not None and time.monotonic() - started >= timeout:
                        _stop(process)
                        return FailedReview(124, "CodeRabbit timed out; review is unavailable")
            except KeyboardInterrupt:
                _stop(process)
                return FailedReview(130, "CodeRabbit review interrupted")
            finally:
                _stop(process)
