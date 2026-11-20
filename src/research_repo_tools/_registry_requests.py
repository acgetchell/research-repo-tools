"""Bounded retries for exact-URL public registry GETs, never mutations."""

import http.client
import io
import math
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from queue import Empty, SimpleQueue
from typing import cast, override
from urllib.parse import SplitResult, urlsplit

from research_repo_tools._http import close_http_error
from research_repo_tools.dependency_progress import report

_LOCK = threading.Lock()
_requested_at: float | None = None
_TRANSIENT = frozenset({408, 429, 500, 502, 503, 504})
_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org", "crates.io", "static.crates.io"})
_TIMEOUT_ENV = "RRT_REGISTRY_TIMEOUT_SECONDS"
_CONNECT_TIMEOUT = 30.0
_READ_TIMEOUT = 30.0
_DNS_SLOTS = threading.BoundedSemaphore(4)
_CHUNK_SIZE = 64 * 1024
_remaining: ContextVar[Callable[[], float] | None] = ContextVar("registry_deadline", default=None)


def registry_timeout() -> int:
    """Read the total per-GET budget, in whole seconds, including retries."""
    value = os.environ.get(_TIMEOUT_ENV, "60")
    if not (value.isascii() and value.isdecimal() and len(value) <= 3 and 1 <= int(value) <= 900):
        raise ValueError(f"{_TIMEOUT_ENV} must be a whole number of seconds from 1 to 900 (default 60)")
    return int(value)


def _identity(parsed: SplitResult) -> str:
    path = parsed.path.strip("/").split("/")
    if parsed.netloc == "pypi.org" and path[:1] == ["pypi"] and path[-1:] == ["json"]:
        path = path[1:-1]
    elif parsed.netloc == "crates.io" and path[:3] == ["api", "v1", "crates"]:
        path = path[3:]
    else:
        path = path[-1:]
    return f"{parsed.netloc} {'/'.join(path)}".rstrip()


class _BudgetExpired(TimeoutError):
    pass


class _StalledRead(TimeoutError):
    pass


def _io_timeout(remaining: Callable[[], float], cap: float) -> float:
    seconds = remaining()
    if seconds <= 0:
        raise _BudgetExpired
    return min(cap, seconds)


type _AddressInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes]]


def _resolve_addresses(address: tuple[str, int], cap: float, remaining: Callable[[], float]) -> Sequence[_AddressInfo]:
    # Native DNS is not cancellable. Bound the number of in-flight lookups so
    # timed-out callers can leave without accumulating resolver threads.
    if not _DNS_SLOTS.acquire(timeout=_io_timeout(remaining, cap)):
        _io_timeout(remaining, cap)
        raise TimeoutError("registry DNS resolver slots are busy")
    result: SimpleQueue[Sequence[_AddressInfo] | BaseException] = SimpleQueue()

    def resolve() -> None:
        try:
            result.put(socket.getaddrinfo(*address, 0, socket.SOCK_STREAM))
        except BaseException as error:
            result.put(error)
        finally:
            _DNS_SLOTS.release()

    try:
        _io_timeout(remaining, cap)
        threading.Thread(target=resolve, daemon=True).start()
    except BaseException:
        _DNS_SLOTS.release()
        raise
    try:
        observed = result.get(timeout=_io_timeout(remaining, cap))
    except Empty:
        _io_timeout(remaining, cap)
        raise TimeoutError("registry DNS lookup timed out") from None
    _io_timeout(remaining, cap)
    if isinstance(observed, BaseException):
        raise observed
    return observed


def _connect_tcp(address: tuple[str, int], cap: float, source_address: tuple[str, int] | None, *, remaining: Callable[[], float]) -> socket.socket:
    last_error: OSError = OSError("registry DNS lookup returned no TCP addresses")
    for family, kind, protocol, _, endpoint in _resolve_addresses(address, cap, remaining):
        timeout = _io_timeout(remaining, cap)
        sock = None
        try:
            sock = socket.socket(family, kind, protocol)
            sock.settimeout(timeout)
            if source_address is not None:
                sock.bind(source_address)
            sock.connect(endpoint)
            sock.settimeout(_io_timeout(remaining, cap))
            return sock
        except BaseException as error:
            if sock is not None:
                sock.close()
            if not isinstance(error, OSError) or isinstance(error, _BudgetExpired):
                raise
            last_error = error
    raise last_error


class _DeadlineReader(io.RawIOBase):
    """Apply the remaining budget to each socket read, including HTTP framing."""

    def __init__(self, stream: io.RawIOBase, sock: socket.socket, remaining: Callable[[], float]):
        self._stream = stream
        self._socket = sock
        self._remaining = remaining

    @override
    def readable(self) -> bool:
        return True

    @override
    def readinto(self, buffer) -> int | None:
        self._socket.settimeout(_io_timeout(self._remaining, _READ_TIMEOUT))
        return cast("int | None", self._stream.readinto(buffer))

    @override
    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            super().close()


class RegistryHTTPSHandler(urllib.request.HTTPSHandler):
    """Keep urllib TLS, proxy and redirect behavior while budgeting socket reads."""

    @override
    def do_open(self, http_class: Callable[..., http.client.HTTPConnection], req: urllib.request.Request, **http_conn_args):
        remaining = _remaining.get()
        if remaining is None:
            return super().do_open(http_class, req, **http_conn_args)

        class Response(http.client.HTTPResponse):
            def __init__(self, sock: socket.socket, *args, **kwargs):
                super().__init__(sock, *args, **kwargs)
                stream = self.fp
                assert stream is not None  # HTTPResponse initializes this from the connected socket.
                self.fp = io.BufferedReader(_DeadlineReader(stream.detach(), sock, remaining))

        def connection(host, timeout=None, **kwargs) -> http.client.HTTPConnection:
            cap = _CONNECT_TIMEOUT if timeout is None else timeout
            connected = http_class(host, timeout=_io_timeout(remaining, cap), **kwargs)
            connected.response_class = Response
            native_tunnel = cast("Callable[[], None]", getattr(connected, "_tunnel"))
            native_connect = connected.connect

            def connect(address, timeout, source_address):
                return _connect_tcp(address, timeout, source_address, remaining=remaining)

            def refresh_timeout() -> None:
                sock = connected.sock
                assert sock is not None  # Successful native connection steps leave the socket open.
                sock.settimeout(_io_timeout(remaining, cap))

            def tunnel() -> None:
                native_tunnel()
                refresh_timeout()

            def connect_for_request() -> None:
                native_connect()
                refresh_timeout()

            setattr(connected, "_create_connection", connect)
            setattr(connected, "_tunnel", tunnel)
            setattr(connected, "connect", connect_for_request)
            return connected

        return super().do_open(connection, req, **http_conn_args)


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


def _request(url: str, timeout: float, limit: int, *, remaining: Callable[[], float]) -> bytes:
    from research_repo_tools import registry

    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "research-repo-tools"})
    token = _remaining.set(remaining)
    try:
        response = registry._OPENER.open(request, timeout=timeout)
    finally:
        _remaining.reset(token)
    with response:
        if response.status != 200 or response.url != url:
            raise ValueError("registry returned an unexpected status or redirect")
        payload = bytearray()
        while True:
            if remaining() <= 0:
                raise _BudgetExpired
            try:
                chunk = response.read1(min(_CHUNK_SIZE, limit + 1 - len(payload)))
            except TimeoutError:
                if remaining() <= 0:
                    raise _BudgetExpired from None
                raise _StalledRead from None
            if remaining() <= 0:
                raise _BudgetExpired
            if not isinstance(chunk, bytes) or len(payload) + len(chunk) > limit:
                raise ValueError("registry response exceeds its byte limit or is not bytes")
            if not chunk:
                if isinstance(response, http.client.HTTPResponse) and response.length:
                    raise http.client.IncompleteRead(bytes(payload), response.length)
                return bytes(payload)
            payload.extend(chunk)


def read_registry(
    url: str,
    *,
    limit: int,
    transport: Callable[[str, float, int], bytes] | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bytes:
    """At most three attempts within the configured total GET budget.

    Preserve the exact URL and response limits on every attempt. Every crates.io
    API attempt participates in process-wide one-second pacing. No observation
    survives this call. Integrity/parse errors are never transient failures.
    """
    from research_repo_tools.registry import TransientRegistryError

    clock = clock if clock is not None else time.monotonic
    sleep = sleep if sleep is not None else time.sleep
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in _HOSTS or parsed.query or parsed.fragment or "\\" in url or any(c.isspace() for c in url):
        raise ValueError("dependency registry URL must use an exact supported public HTTPS origin without credentials or query")
    if type(limit) is not int or limit < 1:
        raise ValueError("registry response byte limit must be a positive integer")
    identity = _identity(parsed)
    budget = registry_timeout()
    deadline = clock() + budget

    def remaining() -> float:
        return deadline - clock()

    def request() -> bytes:
        seconds = remaining()
        if seconds <= 0:
            raise _BudgetExpired
        timeout = min(_CONNECT_TIMEOUT, seconds)
        payload = _request(url, timeout, limit, remaining=remaining) if transport is None else transport(url, timeout, limit)
        if remaining() <= 0:
            raise _BudgetExpired
        if not isinstance(payload, bytes) or len(payload) > limit:
            raise ValueError("registry response exceeds its byte limit or is not bytes")
        return payload

    last_failure = "connection timeout or failure"
    for attempt in range(3):
        delay = float(2**attempt)
        # Observer failures belong to the caller, never to transport retries.
        # Report before taking the pacing lock; request() includes callback and
        # pacing time in the remaining I/O budget.
        report("request", "registry", detail=parsed.netloc)
        try:
            if parsed.netloc == "crates.io" and parsed.path.startswith("/api/"):
                global _requested_at
                seconds = remaining()
                if seconds <= 0 or not _LOCK.acquire(timeout=seconds):
                    raise _BudgetExpired
                try:
                    if _requested_at is not None:
                        wait = max(0.0, 1.0 - (clock() - _requested_at))
                        if wait >= remaining():
                            raise _BudgetExpired
                        if wait:
                            sleep(wait)
                    _requested_at = clock()
                    return request()
                finally:
                    _LOCK.release()
            return request()
        except urllib.error.HTTPError as error:
            close_http_error(error)
            if remaining() <= 0:
                last_failure = "total download budget exhausted"
                break
            if error.url != url or error.code not in _TRANSIENT:
                raise
            last_failure = f"HTTP {error.code}"
            try:
                delay = max(delay, _retry_after(error.headers.get("Retry-After")))
            except ValueError as retry_error:
                raise ValueError(f"registry GET {identity}: {retry_error}") from None
        except _BudgetExpired:
            last_failure = "total download budget exhausted"
            break
        except _StalledRead:
            last_failure = "stalled body read"
        except OSError as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            if isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN:
                last_failure = "temporary DNS lookup failure"
            elif isinstance(reason, (TimeoutError, ConnectionError)):
                last_failure = "total download budget exhausted" if remaining() <= 0 else "connection or response-header timeout/failure"
            else:
                raise ValueError(f"registry GET {identity} failed; availability is unproven") from None
        except ValueError as error:
            raise ValueError(f"registry GET {identity}: {error}") from None
        if attempt == 2 or delay >= remaining():
            break
        report("retry", "registry", detail=f"{parsed.netloc}; attempt {attempt + 2}/3 after {delay:g}s")
        sleep(delay)
    raise TransientRegistryError(
        f"registry GET {identity} exhausted three attempts or its {budget}-second retry budget; last failure: {last_failure}; "
        f"availability is unproven; retry verification with retained evidence (set {_TIMEOUT_ENV} to increase the total budget)"
    )
