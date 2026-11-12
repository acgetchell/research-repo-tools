"""Bounded retries for exact-URL public registry GETs, never mutations."""

import math
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from research_repo_tools._http import closing_http_errors
from research_repo_tools.dependency_progress import report

_LOCK = threading.Lock()
_requested_at: float | None = None
_TRANSIENT = frozenset({408, 429, 500, 502, 503, 504})
_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org", "crates.io", "static.crates.io"})


def _retry_after(value: str | None) -> float:
    if value is None:
        return 0.0
    try:
        delay = float(value) if value.isascii() and value.isdecimal() else (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
    except ValueError, TypeError, OverflowError:
        raise ValueError("registry Retry-After is invalid; stop and retry later") from None
    if not math.isfinite(delay) or delay > 10:
        raise ValueError("registry Retry-After exceeds the ten-second retry bound; retry later")
    return max(0.0, delay)


def _request(url: str, timeout: float, limit: int) -> bytes:
    from research_repo_tools import registry

    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "research-repo-tools"})
    with closing_http_errors(), registry._OPENER.open(request, timeout=timeout) as response:
        if response.status != 200 or response.url != url:
            raise ValueError("registry returned an unexpected status or redirect")
        payload = response.read(limit + 1)
        if not isinstance(payload, bytes) or len(payload) > limit:
            raise ValueError("registry response exceeds its byte limit or is not bytes")
        return payload


def read_registry(
    url: str,
    *,
    limit: int,
    transport: Callable[[str, float, int], bytes] | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bytes:
    """At most three attempts within a 60-second retry budget, 30s per I/O.

    Preserve the exact URL and response limits on every attempt. Every crates.io
    API attempt participates in process-wide one-second pacing. No observation
    survives this call. Integrity/parse errors are never transient failures.
    """
    transport = transport if transport is not None else _request
    clock = clock if clock is not None else time.monotonic
    sleep = sleep if sleep is not None else time.sleep
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in _HOSTS or parsed.query or parsed.fragment or "\\" in url or any(c.isspace() for c in url):
        raise ValueError("dependency registry URL must use an exact supported public HTTPS origin without credentials or query")
    deadline = clock() + 60.0

    def request() -> bytes:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("registry retry deadline exhausted")
        report("request", "registry", detail=parsed.netloc)
        payload = transport(url, min(30.0, remaining), limit)
        if clock() >= deadline:
            raise TimeoutError("registry retry deadline exhausted")
        if not isinstance(payload, bytes) or len(payload) > limit:
            raise ValueError("registry response exceeds its byte limit or is not bytes")
        return payload

    last_failure = "connection/read timeout"
    for attempt in range(3):
        delay = float(2**attempt)
        try:
            if parsed.netloc == "crates.io" and parsed.path.startswith("/api/"):
                global _requested_at
                remaining = deadline - clock()
                if remaining <= 0 or not _LOCK.acquire(timeout=remaining):
                    raise TimeoutError("registry retry deadline exhausted")
                try:
                    if _requested_at is not None:
                        wait = max(0.0, 1.0 - (clock() - _requested_at))
                        if wait >= deadline - clock():
                            raise TimeoutError("registry retry deadline exhausted")
                        if wait:
                            sleep(wait)
                    _requested_at = clock()
                    return request()
                finally:
                    _LOCK.release()
            return request()
        except urllib.error.HTTPError as error:
            with closing_http_errors():
                try:
                    if error.url != url or error.code not in _TRANSIENT:
                        raise
                    last_failure = f"HTTP {error.code}"
                    delay = max(delay, _retry_after(error.headers.get("Retry-After")))
                finally:
                    error.close()
        except OSError as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            if isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN:
                last_failure = "temporary DNS lookup failure"
            elif isinstance(reason, (TimeoutError, ConnectionError)):
                last_failure = "connection/read timeout"
            else:
                raise ValueError("registry GET failed; availability is unproven") from None
        if attempt == 2 or delay >= deadline - clock():
            break
        report("retry", "registry", detail=f"{parsed.netloc}; attempt {attempt + 2}/3 after {delay:g}s")
        sleep(delay)
    raise ValueError(
        f"registry GET exhausted three attempts or its 60-second retry budget; last failure: {last_failure}; "
        "availability is unproven; retry verification with retained evidence"
    )
