"""Exact endpoint errors never become permission to retry uploads."""

import io
import json
import sys
import urllib.error
from email.message import Message

import pytest

from research_repo_tools import registry


def crate():
    return {"version": {"crate": "sample", "num": "1.2.3", "yanked": False, "checksum": "a" * 64}}


class Response(io.BytesIO):
    status = 200
    url = "https://crates.io/api/v1/crates/sample/1.2.3"


def test_exact_lookup_and_proven_absence(monkeypatch):
    monkeypatch.setattr(registry.urllib.request, "urlopen", lambda *args, **kwargs: Response(json.dumps(crate()).encode()))
    assert registry.lookup_version("crates-io", "sample", "1.2.3").present

    def absent(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 404, "missing", Message(), None)

    monkeypatch.setattr(registry.urllib.request, "urlopen", absent)
    assert not registry.lookup_version("crates-io", "sample", "1.2.3").present


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_http_failure_is_not_absence(monkeypatch, code):
    def failed(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, code, "unavailable", Message(), None)

    monkeypatch.setattr(registry.urllib.request, "urlopen", failed)
    with pytest.raises(registry.RegistryLookupError, match=f"HTTP {code}"):
        registry.wait_for_version("crates-io", "sample", "1.2.3", attempts=3)


@pytest.mark.parametrize(
    "payload", [b"null", b"[]", b"{", b"\xff", b"x" * (registry.LIMIT + 1)], ids=["null", "array", "truncated", "invalid-utf8", "oversized"]
)
def test_malformed_and_oversized_json_are_not_absence(monkeypatch, payload):
    monkeypatch.setattr(registry.urllib.request, "urlopen", lambda *args, **kwargs: Response(payload))
    with pytest.raises(registry.RegistryLookupError):
        registry.lookup_version("crates-io", "sample", "1.2.3")


@pytest.mark.parametrize("damage", ["duplicate-identity", "nonfinite", "deeply-nested"])
def test_ambiguous_or_nonfinite_registry_json_is_not_publication_evidence(monkeypatch, damage):
    payload = json.dumps(crate()).encode()
    if damage == "duplicate-identity":
        payload = payload.replace(b'"crate": "sample"', b'"crate": "other", "crate": "sample"')
    elif damage == "nonfinite":
        payload = payload[:-1] + b', "extra": NaN}'
    else:
        depth = sys.getrecursionlimit() + 100
        payload = payload[:-1] + b', "extra": ' + b"[" * depth + b"0" + b"]" * depth + b"}"
    monkeypatch.setattr(registry.urllib.request, "urlopen", lambda *args, **kwargs: Response(payload))
    with pytest.raises(registry.RegistryLookupError, match="malformed JSON"):
        registry.lookup_version("crates-io", "sample", "1.2.3")


@pytest.mark.parametrize("code", [404, 429])
def test_http_error_response_is_closed_on_absence_and_failure(monkeypatch, code):
    body = io.BytesIO(b"registry response")
    error = urllib.error.HTTPError(Response.url, code, "unavailable", Message(), body)

    def failed(request, **kwargs):
        assert request.full_url == error.url
        raise error

    monkeypatch.setattr(registry.urllib.request, "urlopen", failed)
    if code == 404:
        assert not registry.lookup_version("crates-io", "sample", "1.2.3").present
    else:
        with pytest.raises(registry.RegistryLookupError, match="HTTP 429"):
            registry.lookup_version("crates-io", "sample", "1.2.3")
    assert body.closed


def test_transport_and_redirect_failures_are_not_absence(monkeypatch):
    def failed(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(registry.urllib.request, "urlopen", failed)
    with pytest.raises(registry.RegistryLookupError):
        registry.lookup_version("crates-io", "sample", "1.2.3")

    def redirect(*args, **kwargs):
        response = Response(json.dumps(crate()).encode())
        response.url = "https://other.invalid"
        return response

    monkeypatch.setattr(registry.urllib.request, "urlopen", redirect)
    with pytest.raises(registry.RegistryLookupError):
        registry.lookup_version("crates-io", "sample", "1.2.3")


@pytest.mark.parametrize("field,value", [("crate", "other"), ("num", "1.2.4"), ("yanked", True), ("checksum", "invalid")])
def test_crate_identity_and_archive_evidence(monkeypatch, field, value):
    data = crate()
    data["version"][field] = value
    monkeypatch.setattr(registry, "_json", lambda url: data)
    with pytest.raises(registry.RegistryLookupError):
        registry.lookup_version("crates-io", "sample", "1.2.3")


def test_pypi_requires_both_distributions_and_exact_identity(monkeypatch):
    data = {"info": {"name": "sample", "version": "1.2.3"}, "urls": [{"packagetype": kind, "yanked": False} for kind in ["sdist", "bdist_wheel"]]}
    monkeypatch.setattr(registry, "_json", lambda url: data)
    assert registry.lookup_version("pypi", "sample", "1.2.3").present
    data["urls"].pop()
    with pytest.raises(registry.RegistryLookupError, match="both wheel and sdist"):
        registry.lookup_version("pypi", "sample", "1.2.3")
    data["info"]["version"] = "1.2.4"
    with pytest.raises(registry.RegistryLookupError, match="identity"):
        registry.lookup_version("pypi", "sample", "1.2.3")


def test_wait_retries_only_absence_and_stops_at_bound(monkeypatch):
    visible = registry.RegistryVersion("crates-io", "sample", "1.2.3", True)
    absent = registry.RegistryVersion("crates-io", "sample", "1.2.3", False)
    values = iter([absent, absent, visible])
    monkeypatch.setattr(registry, "lookup_version", lambda *args: next(values))
    waits = []
    monkeypatch.setattr(registry.time, "sleep", waits.append)
    assert registry.wait_for_version("crates-io", "sample", "1.2.3", attempts=3, interval=1).present
    assert waits == [1, 1]
    monkeypatch.setattr(registry, "lookup_version", lambda *args: absent)
    assert not registry.wait_for_version("crates-io", "sample", "1.2.3", attempts=2, interval=0).present
    for attempts, interval in [(0, 0), (32, 0), (1, 11), (True, 0)]:
        with pytest.raises(ValueError):
            registry.wait_for_version("crates-io", "sample", "1.2.3", attempts=attempts, interval=interval)
