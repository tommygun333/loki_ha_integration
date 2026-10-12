"""Contract tests for read-only LokiClient calls using an in-memory HTTP stub."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from loki_monitor.api import LokiAuthError, LokiClient, LokiQueryError, validate_url
from loki_monitor.const import CONF_AUTH_TYPE, CONF_URL


class MockResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else {"status": "success", "data": {"resultType": "vector", "result": [{"value": [1, "1"]}]}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def json(self, content_type=None):
        return self.body


class MockSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_connect_uses_read_only_instant_query():
    session = MockSession(MockResponse())
    api = LokiClient(session, {CONF_URL: "http://loki:3100", CONF_AUTH_TYPE: "none"})
    await api.async_test_connection()
    assert session.calls[0][0] == "http://loki:3100/loki/api/v1/query"
    assert session.calls[0][1]["params"] == {"query": "vector(1)"}
    assert session.calls[0][1]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_auth_error_does_not_leak_secrets():
    api = LokiClient(MockSession(MockResponse(status=401)), {CONF_URL: "http://loki:3100", CONF_AUTH_TYPE: "bearer", "token": "secret"})
    with pytest.raises(LokiAuthError, match="authorization rejected"):
        await api.async_test_connection()


@pytest.mark.asyncio
async def test_bad_query_error():
    api = LokiClient(MockSession(MockResponse(status=400)), {CONF_URL: "http://loki:3100", CONF_AUTH_TYPE: "none"})
    with pytest.raises(LokiQueryError):
        await api.async_query("{bad")


@pytest.mark.parametrize("url", ["loki:3100", "ftp://loki", "http://user:pass@loki", "http://loki?token=123", "http://loki/#frag"])
def test_bad_url_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)
