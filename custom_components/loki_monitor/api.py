"""Read-only asynchronous Loki HTTP API client."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import BasicAuth, ClientError, ClientSession, ClientTimeout

from .const import (
    AUTH_BASIC,
    AUTH_BEARER,
    CONF_AUTH_TYPE,
    CONF_PASSWORD,
    CONF_TENANT_ID,
    CONF_TOKEN,
    CONF_URL,
    CONF_USERNAME,
)
from .logic import validate_label_name


class LokiError(Exception):
    """A Loki API or connection error."""


class LokiAuthError(LokiError):
    """Invalid or expired Loki proxy credentials."""


class LokiQueryError(LokiError):
    """A query or an API response was invalid."""


class LokiClient:
    """A read-only client; no push, delete, or administrative endpoints."""

    def __init__(self, session: ClientSession, config: Mapping[str, Any]) -> None:
        self._session = session
        self._base_url = config[CONF_URL].rstrip("/")
        auth_type = config.get(CONF_AUTH_TYPE, "none")
        self._auth = (
            BasicAuth(config[CONF_USERNAME], config[CONF_PASSWORD])
            if auth_type == AUTH_BASIC
            else None
        )
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if auth_type == AUTH_BEARER:
            self._headers["Authorization"] = f"Bearer {config[CONF_TOKEN]}"
        if tenant := config.get(CONF_TENANT_ID):
            self._headers["X-Scope-OrgID"] = str(tenant)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Issue a GET without logging URL params or sensitive response content."""
        try:
            async with self._session.get(
                f"{self._base_url}/{path.lstrip('/')}",
                params=params,
                headers=self._headers,
                auth=self._auth,
                timeout=ClientTimeout(total=25),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise LokiAuthError("Loki authorization rejected")
                if response.status == 400:
                    raise LokiQueryError("Loki rejected the query (HTTP 400)")
                if response.status != 200:
                    raise LokiError(f"Loki returned HTTP {response.status}")
                try:
                    body = await response.json(content_type=None)
                except (ValueError, TypeError) as err:
                    raise LokiQueryError("Loki did not return valid JSON") from err
        except (asyncio.TimeoutError, ClientError) as err:
            raise LokiError("Could not reach Loki") from err
        if not isinstance(body, dict) or body.get("status") != "success":
            raise LokiQueryError("Unexpected Loki response")
        return body.get("data")

    async def async_test_connection(self) -> None:
        """Test an actual Loki query (rather than only TCP/ready)."""
        result = await self.async_query("vector(1)")
        if result.get("resultType") != "vector":
            raise LokiQueryError("The endpoint is not a Loki query API")

    async def async_query(self, query: str) -> dict[str, Any]:
        data = await self._get("loki/api/v1/query", {"query": query})
        if not isinstance(data, dict) or "resultType" not in data:
            raise LokiQueryError("Missing Loki resultType")
        return data

    async def async_query_range(
        self,
        query: str,
        *,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ) -> dict[str, Any]:
        data = await self._get(
            "loki/api/v1/query_range",
            {
                "query": query,
                "start": str(int(start.timestamp() * 1_000_000_000)),
                "end": str(int(end.timestamp() * 1_000_000_000)),
                "direction": "backward",
                "limit": limit,
            },
        )
        if not isinstance(data, dict) or "resultType" not in data:
            raise LokiQueryError("Missing Loki range resultType")
        return data

    async def async_query_streams_ns(
        self, query: str, *, start_ns: int, end_ns: int, limit: int
    ) -> dict[str, Any]:
        """Fetch one backward page of log streams at nanosecond precision."""
        if not 1 <= limit <= 101 or start_ns > end_ns:
            raise ValueError("Invalid bounded log query range")
        data = await self._get(
            "loki/api/v1/query_range",
            {
                "query": query,
                "start": str(start_ns),
                "end": str(end_ns),
                "direction": "backward",
                "limit": limit,
            },
        )
        if not isinstance(data, dict) or data.get("resultType") != "streams":
            raise LokiQueryError("Loki log viewer requires stream results")
        return data

    async def async_labels(self, since: str = "1h") -> list[str]:
        result = await self._get("loki/api/v1/labels", {"since": since})
        if not isinstance(result, list):
            raise LokiQueryError("Invalid label list")
        return [value for value in result if isinstance(value, str)]

    async def async_label_values(self, label: str, since: str = "1h") -> list[str]:
        validate_label_name(label)
        result = await self._get(
            f"loki/api/v1/label/{quote(label, safe='')}/values", {"since": since}
        )
        if not isinstance(result, list):
            raise LokiQueryError("Invalid label values")
        return [value for value in result if isinstance(value, str)]

    async def async_recent_logs(
        self, query: str, *, minutes: int, limit: int
    ) -> dict[str, Any]:
        end = datetime.now(UTC)
        return await self.async_query_range(
            query, start=end - timedelta(minutes=minutes), end=end, limit=limit
        )


def validate_url(value: str) -> str:
    """Require a server root URL and reject credentials or URL query strings."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Expected an http(s) Loki base URL without credentials")
    return value.strip().rstrip("/")
