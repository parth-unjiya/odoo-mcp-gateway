"""Tests for the ConnectionManager with circuit breaker and retry."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from odoo_mcp_gateway.client.exceptions import OdooConnectionError
from odoo_mcp_gateway.core.connection.manager import (
    CircuitState,
    ConnectionManager,
)

_BACKOFF_PATH = "odoo_mcp_gateway.core.connection.manager.ConnectionManager._backoff"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _ok_response() -> httpx.Response:
    return httpx.Response(200, content=b'{"ok": true}')


def _server_error() -> httpx.Response:
    return httpx.Response(500, content=b"Internal Server Error")


def _not_found() -> httpx.Response:
    return httpx.Response(404, content=b"Not Found")


# ------------------------------------------------------------------
# Basic request
# ------------------------------------------------------------------


class TestBasicRequest:
    async def test_passes_through(self) -> None:
        mgr = ConnectionManager("http://odoo:8069", max_retries=0)
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(return_value=_ok_response())
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("POST", "/web/test")

        assert resp.status_code == 200
        mgr._client.request.assert_called_once_with("POST", "/web/test")

    async def test_4xx_not_retried(self) -> None:
        mgr = ConnectionManager("http://odoo:8069", max_retries=3)
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(return_value=_not_found())
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("GET", "/missing")

        assert resp.status_code == 404
        assert mgr._client.request.call_count == 1

    async def test_returns_response_object(self) -> None:
        mgr = ConnectionManager("http://odoo:8069", max_retries=0)
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(return_value=_ok_response())
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("POST", "/web/dataset/call_kw")

        assert isinstance(resp, httpx.Response)


# ------------------------------------------------------------------
# Circuit breaker states
# ------------------------------------------------------------------


class TestCircuitBreaker:
    async def test_starts_closed(self) -> None:
        mgr = ConnectionManager("http://odoo:8069")
        assert mgr.state == CircuitState.CLOSED

    async def test_opens_after_threshold(self) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            failure_threshold=3,
            max_retries=0,
        )
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(side_effect=httpx.ConnectError("fail"))
        mgr._client.aclose = AsyncMock()

        for _ in range(3):
            with pytest.raises(OdooConnectionError):
                await mgr.request("POST", "/test")

        assert mgr.state == CircuitState.OPEN

    async def test_blocks_when_open(self) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            failure_threshold=1,
            recovery_timeout=999,
            max_retries=0,
        )
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(side_effect=httpx.ConnectError("fail"))
        mgr._client.aclose = AsyncMock()

        # Trip the breaker
        with pytest.raises(OdooConnectionError):
            await mgr.request("POST", "/test")

        assert mgr.state == CircuitState.OPEN

        # Next request should be blocked immediately
        with pytest.raises(OdooConnectionError, match="OPEN"):
            await mgr.request("POST", "/test")

    async def test_half_open_after_timeout(self) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            failure_threshold=1,
            recovery_timeout=0.0,
            max_retries=0,
        )
        mgr._state = CircuitState.OPEN
        mgr._last_failure_time = time.monotonic() - 1.0

        assert mgr.state == CircuitState.HALF_OPEN

    async def test_closes_on_success_after_half_open(
        self,
    ) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            failure_threshold=1,
            recovery_timeout=0.0,
            max_retries=0,
        )
        mgr._state = CircuitState.OPEN
        mgr._last_failure_time = time.monotonic() - 1.0
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(return_value=_ok_response())
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("POST", "/test")

        assert resp.status_code == 200
        assert mgr.state == CircuitState.CLOSED
        assert mgr._failure_count == 0

    async def test_reopens_on_failure_in_half_open(
        self,
    ) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            failure_threshold=1,
            recovery_timeout=60.0,
            max_retries=0,
        )
        # Manually set to HALF_OPEN to simulate a probe attempt
        mgr._state = CircuitState.HALF_OPEN
        mgr._failure_count = 0
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(side_effect=httpx.ConnectError("still down"))
        mgr._client.aclose = AsyncMock()

        with pytest.raises(OdooConnectionError):
            await mgr.request("POST", "/test")

        assert mgr._state == CircuitState.OPEN

    async def test_failure_count_resets_on_success(
        self,
    ) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            failure_threshold=5,
            max_retries=0,
        )
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.aclose = AsyncMock()

        # Accumulate some failures
        mgr._client.request = AsyncMock(side_effect=httpx.ConnectError("fail"))
        for _ in range(3):
            with pytest.raises(OdooConnectionError):
                await mgr.request("POST", "/test")
        assert mgr._failure_count == 3

        # A success resets
        mgr._client.request = AsyncMock(return_value=_ok_response())
        await mgr.request("POST", "/test")
        assert mgr._failure_count == 0


# ------------------------------------------------------------------
# Retry with backoff
# ------------------------------------------------------------------


class TestRetry:
    @patch(_BACKOFF_PATH, new_callable=AsyncMock)
    async def test_retries_on_connect_error(self, mock_backoff: AsyncMock) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            max_retries=2,
            failure_threshold=100,
        )
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(
            side_effect=[
                httpx.ConnectError("fail1"),
                httpx.ConnectError("fail2"),
                _ok_response(),
            ]
        )
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("POST", "/test")

        assert resp.status_code == 200
        assert mgr._client.request.call_count == 3
        assert mock_backoff.call_count == 2

    @patch(_BACKOFF_PATH, new_callable=AsyncMock)
    async def test_retries_on_timeout(self, mock_backoff: AsyncMock) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            max_retries=1,
            failure_threshold=100,
        )
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(
            side_effect=[
                httpx.TimeoutException("slow"),
                _ok_response(),
            ]
        )
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("GET", "/test")

        assert resp.status_code == 200

    @patch(_BACKOFF_PATH, new_callable=AsyncMock)
    async def test_exhausted_retries_raises(self, mock_backoff: AsyncMock) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            max_retries=2,
            failure_threshold=100,
        )
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(side_effect=httpx.ConnectError("down"))
        mgr._client.aclose = AsyncMock()

        with pytest.raises(OdooConnectionError, match="failed after"):
            await mgr.request("POST", "/test")

        # initial + 2 retries
        assert mgr._client.request.call_count == 3

    async def test_no_retry_on_4xx(self) -> None:
        mgr = ConnectionManager("http://odoo:8069", max_retries=3)
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(return_value=_not_found())
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("GET", "/missing")

        assert resp.status_code == 404
        assert mgr._client.request.call_count == 1

    @patch(_BACKOFF_PATH, new_callable=AsyncMock)
    async def test_5xx_retried(self, mock_backoff: AsyncMock) -> None:
        mgr = ConnectionManager(
            "http://odoo:8069",
            max_retries=1,
            failure_threshold=100,
        )
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(side_effect=[_server_error(), _ok_response()])
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("POST", "/test")

        assert resp.status_code == 200
        assert mgr._client.request.call_count == 2

    @patch(_BACKOFF_PATH, new_callable=AsyncMock)
    async def test_5xx_last_attempt_returned(self, mock_backoff: AsyncMock) -> None:
        """On last retry, a 5xx is returned rather than raising."""
        mgr = ConnectionManager(
            "http://odoo:8069",
            max_retries=0,
            failure_threshold=100,
        )
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.request = AsyncMock(return_value=_server_error())
        mgr._client.aclose = AsyncMock()

        resp = await mgr.request("POST", "/test")
        assert resp.status_code == 500


# ------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------


class TestHealthCheck:
    async def test_healthy(self) -> None:
        mgr = ConnectionManager("http://odoo:8069")
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.post = AsyncMock(return_value=httpx.Response(200))
        mgr._client.aclose = AsyncMock()

        assert await mgr.health_check() is True

    async def test_unhealthy_connect_error(self) -> None:
        mgr = ConnectionManager("http://odoo:8069")
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mgr._client.aclose = AsyncMock()

        assert await mgr.health_check() is False

    async def test_unhealthy_timeout(self) -> None:
        mgr = ConnectionManager("http://odoo:8069")
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))
        mgr._client.aclose = AsyncMock()

        assert await mgr.health_check() is False

    async def test_unhealthy_non_200(self) -> None:
        mgr = ConnectionManager("http://odoo:8069")
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.post = AsyncMock(return_value=httpx.Response(503))
        mgr._client.aclose = AsyncMock()

        assert await mgr.health_check() is False


# ------------------------------------------------------------------
# Close
# ------------------------------------------------------------------


class TestClose:
    async def test_closes_client(self) -> None:
        mgr = ConnectionManager("http://odoo:8069")
        mgr._client = AsyncMock(spec=httpx.AsyncClient)
        mgr._client.aclose = AsyncMock()

        await mgr.close()

        mgr._client.aclose.assert_called_once()


# ------------------------------------------------------------------
# Circuit state enum
# ------------------------------------------------------------------


class TestCircuitStateEnum:
    def test_values(self) -> None:
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"
