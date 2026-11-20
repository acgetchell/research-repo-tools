"""Release response resources owned by failed urllib requests."""

import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager


def close_http_error(error: urllib.error.HTTPError) -> None:
    """Close a failed response while retaining both errors if cleanup fails."""
    try:
        error.close()
    except BaseException as cleanup:
        raise BaseExceptionGroup("HTTP request and response cleanup failed", [error, cleanup]) from None


@contextmanager
def closing_http_errors() -> Iterator[None]:
    """Close HTTPError's response even when opening never enters a with block.

    Keep the original exception for callers that distinguish HTTP statuses. If
    closing also fails, retain both failures instead of losing the request error.
    Successful responses remain owned by the caller's response context manager.
    """
    try:
        yield
    except urllib.error.HTTPError as error:
        close_http_error(error)
        raise
