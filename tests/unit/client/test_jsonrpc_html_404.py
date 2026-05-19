"""Tests for HTML 404 detection in the JSON-RPC client (P2-10).

When an invalid model or method name reaches Odoo, the server can reply
with a Werkzeug HTML 404 page instead of a JSON-RPC error envelope. The
default ``.json()`` path leaks that HTML in the generic ``Non-JSON
response`` error. These tests verify the new pre-flight HTML detector
translates the response into a clean exception type.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from odoo_mcp_gateway.client.exceptions import (
    OdooConnectionError,
    OdooMissingError,
)
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient

_URL = "http://odoo:8069"


def _html_response(
    status: int = 404,
    body: str = "<!doctype html><html><body>Not Found</body></html>",
    content_type: str = "text/html; charset=utf-8",
) -> MagicMock:
    """Build a mock response that looks like a Werkzeug 404 page."""
    resp = MagicMock()
    resp.headers = {"content-type": content_type}
    resp.text = body
    resp.status_code = status
    resp.cookies = MagicMock()
    resp.cookies.get = MagicMock(return_value=None)
    # ``.json()`` would normally raise on HTML — our code must not reach
    # it for this test to be meaningful.
    resp.json = MagicMock(side_effect=ValueError("not json"))
    return resp


def _mock_http(responses: list[Any]) -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=responses)
    client.aclose = AsyncMock()
    return client


class TestHtml404Detection:
    async def test_404_raises_odoo_missing_error(self) -> None:
        resp = _html_response(status=404)
        client = JsonRpcClient(_URL, httpx_client=_mock_http([resp]))

        with pytest.raises(OdooMissingError) as exc_info:
            await client.execute_kw("nonexistent.model", "search", [[]])

        assert "404" in str(exc_info.value)
        assert "endpoint not found" in str(exc_info.value).lower()

    async def test_500_html_raises_connection_error(self) -> None:
        """A 500 HTML error page (Odoo crash with stack trace) is treated
        as a connection-level failure rather than leaked verbatim.
        """
        body = "<html><body>Internal Server Error: Traceback...</body></html>"
        resp = _html_response(status=500, body=body)
        client = JsonRpcClient(_URL, httpx_client=_mock_http([resp]))

        with pytest.raises(OdooConnectionError) as exc_info:
            await client.execute_kw("res.partner", "search", [[]])

        # The stack trace MUST NOT be in the user-facing exception.
        msg = str(exc_info.value)
        assert "Traceback" not in msg
        assert "<html>" not in msg
        assert "JSON-RPC endpoint" in msg or "HTML response" in msg

    async def test_detects_by_content_type_only(self) -> None:
        """If the body starts with whitespace but content-type says HTML,
        we still classify it as an HTML response.
        """
        resp = _html_response(
            status=404,
            body="    <html></html>",  # leading whitespace
            content_type="text/html",
        )
        client = JsonRpcClient(_URL, httpx_client=_mock_http([resp]))

        with pytest.raises(OdooMissingError):
            await client.execute_kw("missing.model", "search", [[]])

    async def test_detects_by_body_when_content_type_missing(self) -> None:
        """If headers don't have a content-type but the body looks like
        HTML, we still detect it (defensive fallback).
        """
        resp = MagicMock()
        resp.headers = {}
        resp.text = "<!doctype html><html>404</html>"
        resp.status_code = 404
        resp.cookies = MagicMock()
        resp.cookies.get = MagicMock(return_value=None)
        resp.json = MagicMock(side_effect=ValueError("not json"))
        client = JsonRpcClient(_URL, httpx_client=_mock_http([resp]))

        with pytest.raises(OdooMissingError):
            await client.execute_kw("missing.model", "search", [[]])

    async def test_valid_json_response_unaffected(self) -> None:
        """Regression guard: a well-formed JSON response is not impacted
        by the new HTML pre-flight.
        """
        resp = MagicMock()
        resp.headers = {"content-type": "application/json"}
        resp.text = '{"result": [1, 2]}'
        resp.status_code = 200
        resp.cookies = MagicMock()
        resp.cookies.get = MagicMock(return_value=None)
        resp.json = MagicMock(
            return_value={"jsonrpc": "2.0", "id": 1, "result": [1, 2]}
        )
        client = JsonRpcClient(_URL, httpx_client=_mock_http([resp]))

        result = await client.execute_kw("res.partner", "search", [[]])

        assert result == [1, 2]
