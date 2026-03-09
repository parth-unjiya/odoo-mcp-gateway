"""Tests for the JSON-RPC client."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from odoo_mcp_gateway.client.exceptions import (
    OdooAccessError,
    OdooAuthError,
    OdooConnectionError,
    OdooMissingError,
    OdooUserError,
    OdooValidationError,
)
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient

# URL used throughout tests.
_URL = "http://odoo:8069"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _auth_body(
    uid: int | bool = 1,
    ctx: dict[str, Any] | None = None,
    admin: bool = False,
    username: str = "u",
) -> dict[str, Any]:
    """Shortcut for a /web/session/authenticate result body."""
    return {
        "uid": uid,
        "user_context": ctx or {},
        "is_admin": admin,
        "username": username,
    }


def _make_response(
    body: dict[str, Any],
    session_id: str | None = None,
) -> MagicMock:
    """Build a mock response with .json() and .cookies.get()."""
    resp = MagicMock()
    resp.json.return_value = body

    cookies: dict[str, str | None] = {
        "session_id": session_id,
    }
    resp.cookies = MagicMock()
    resp.cookies.get = MagicMock(
        side_effect=lambda k, default=None: cookies.get(k, default)
    )
    return resp


def _success_body(result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def _error_body(exc_name: str, message: str = "Server error") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": 200,
            "message": "Odoo Server Error",
            "data": {"name": exc_name, "message": message},
        },
    }


def _mock_http(responses: list[MagicMock]) -> AsyncMock:
    """Return an AsyncClient mock whose post() yields responses."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=responses)
    client.aclose = AsyncMock()
    return client


# ------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------


class TestAuthenticate:
    async def test_success(self) -> None:
        resp = _make_response(
            _success_body(
                _auth_body(
                    uid=2,
                    ctx={"lang": "en_US"},
                    admin=True,
                    username="admin",
                )
            ),
            session_id="abc123",
        )
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        result = await client.authenticate("mydb", "admin", "secret")

        assert result.uid == 2
        assert result.username == "admin"
        assert result.database == "mydb"
        assert result.is_admin is True
        assert result.user_context == {"lang": "en_US"}

    async def test_invalid_credentials(self) -> None:
        resp = _make_response(_success_body({"uid": False}))
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooAuthError, match="invalid credentials"):
            await client.authenticate("mydb", "bad", "creds")

    async def test_uid_zero(self) -> None:
        resp = _make_response(_success_body({"uid": 0}))
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooAuthError):
            await client.authenticate("mydb", "user", "pass")

    async def test_stores_credentials(self) -> None:
        resp = _make_response(
            _success_body(_auth_body()),
            session_id="s1",
        )
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")

        assert client._db == "db"
        assert client._login == "u"
        assert client._password == "p"

    async def test_session_id_extracted(self) -> None:
        resp = _make_response(
            _success_body(_auth_body(uid=5)),
            session_id="sess42",
        )
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        result = await client.authenticate("db", "u", "p")

        assert result.session_id == "sess42"


# ------------------------------------------------------------------
# execute_kw
# ------------------------------------------------------------------


class TestExecuteKw:
    async def test_success(self) -> None:
        auth_resp = _make_response(
            _success_body(_auth_body()),
            session_id="s",
        )
        kw_resp = _make_response(_success_body([{"id": 1, "name": "Partner"}]))
        mock = _mock_http([auth_resp, kw_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        result = await client.execute_kw(
            "res.partner",
            "search_read",
            [[]],
            {"fields": ["name"]},
        )

        assert result == [{"id": 1, "name": "Partner"}]

    async def test_passes_kwargs(self) -> None:
        auth_resp = _make_response(_success_body(_auth_body()))
        kw_resp = _make_response(_success_body(True))
        mock = _mock_http([auth_resp, kw_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        await client.execute_kw("res.partner", "write", [[1], {"name": "X"}])

        call_args = mock.post.call_args_list[1]
        payload = call_args.kwargs.get("json")
        assert payload["params"]["model"] == "res.partner"
        assert payload["params"]["method"] == "write"

    async def test_none_kwargs_becomes_empty_dict(self) -> None:
        auth_resp = _make_response(_success_body(_auth_body()))
        kw_resp = _make_response(_success_body(42))
        mock = _mock_http([auth_resp, kw_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        result = await client.execute_kw("ir.model", "search_count", [[]])

        assert result == 42


# ------------------------------------------------------------------
# Error classification
# ------------------------------------------------------------------


class TestErrorClassification:
    @pytest.mark.parametrize(
        ("exc_name", "expected_cls"),
        [
            ("odoo.exceptions.AccessDenied", OdooAuthError),
            ("odoo.exceptions.AccessError", OdooAccessError),
            (
                "odoo.exceptions.ValidationError",
                OdooValidationError,
            ),
            ("odoo.exceptions.UserError", OdooUserError),
            ("odoo.exceptions.MissingError", OdooMissingError),
        ],
    )
    async def test_error_mapped(
        self, exc_name: str, expected_cls: type[Exception]
    ) -> None:
        resp = _make_response(_error_body(exc_name, "test error"))
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(expected_cls, match="test error"):
            await client.get_version()

    async def test_unknown_error_becomes_user_error(self) -> None:
        resp = _make_response(_error_body("odoo.exceptions.SomethingNew", "boom"))
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooUserError, match="boom"):
            await client.get_version()

    async def test_error_code_preserved(self) -> None:
        resp = _make_response(_error_body("odoo.exceptions.AccessError", "no access"))
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooAccessError) as exc_info:
            await client.get_version()
        assert exc_info.value.code == ("odoo.exceptions.AccessError")


# ------------------------------------------------------------------
# Session expiry auto-retry
# ------------------------------------------------------------------


class TestSessionRetry:
    async def test_retries_once_on_auth_error(self) -> None:
        auth_resp = _make_response(
            _success_body(_auth_body()),
            session_id="s1",
        )
        expired_resp = _make_response(
            _error_body(
                "odoo.exceptions.AccessDenied",
                "Session expired",
            )
        )
        reauth_resp = _make_response(
            _success_body(_auth_body()),
            session_id="s2",
        )
        ok_resp = _make_response(_success_body([1, 2, 3]))

        mock = _mock_http([auth_resp, expired_resp, reauth_resp, ok_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        result = await client.execute_kw("res.partner", "search", [[]])

        assert result == [1, 2, 3]
        assert mock.post.call_count == 4

    async def test_no_retry_without_stored_credentials(self) -> None:
        """Auth error propagates when no credentials stored."""
        expired_resp = _make_response(
            _error_body("odoo.exceptions.AccessDenied", "expired")
        )
        mock = _mock_http([expired_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooAuthError):
            await client.execute_kw("res.partner", "search", [[]])


# ------------------------------------------------------------------
# Connection errors
# ------------------------------------------------------------------


class TestConnectionErrors:
    async def test_connect_error(self) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock.aclose = AsyncMock()
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooConnectionError, match="Cannot connect"):
            await client.get_version()

    async def test_timeout_error(self) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock.aclose = AsyncMock()
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooConnectionError, match="Timeout"):
            await client.get_version()


# ------------------------------------------------------------------
# get_version
# ------------------------------------------------------------------


class TestGetVersion:
    async def test_returns_dict(self) -> None:
        resp = _make_response(_success_body({"server_version": "17.0"}))
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        result = await client.get_version()

        assert result["server_version"] == "17.0"


# ------------------------------------------------------------------
# close
# ------------------------------------------------------------------


class TestClose:
    async def test_close_own_client(self) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.aclose = AsyncMock()
        client = JsonRpcClient(_URL, httpx_client=mock)
        client._owns_client = False
        await client.close()
        mock.aclose.assert_not_called()

    async def test_close_owned_client(self) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.aclose = AsyncMock()
        client = JsonRpcClient(_URL, httpx_client=mock)
        client._owns_client = True
        await client.close()
        mock.aclose.assert_called_once()


# ------------------------------------------------------------------
# RPC payload structure
# ------------------------------------------------------------------


class TestRpcPayload:
    async def test_jsonrpc_version_in_payload(self) -> None:
        resp = _make_response(_success_body({"server_version": "17.0"}))
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.get_version()

        call_args = mock.post.call_args
        payload = call_args.kwargs.get("json")
        assert payload["jsonrpc"] == "2.0"
        assert "id" in payload
        assert payload["method"] == "call"

    async def test_auth_payload_structure(self) -> None:
        resp = _make_response(_success_body(_auth_body()))
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("mydb", "admin", "pass123")

        call_args = mock.post.call_args
        payload = call_args.kwargs.get("json")
        params = payload["params"]
        assert params["db"] == "mydb"
        assert params["login"] == "admin"
        assert params["password"] == "pass123"

    async def test_execute_kw_payload_structure(self) -> None:
        auth_resp = _make_response(_success_body(_auth_body()))
        kw_resp = _make_response(_success_body(True))
        mock = _mock_http([auth_resp, kw_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        await client.execute_kw("sale.order", "read", [[1]], {"fields": ["name"]})

        call_args = mock.post.call_args_list[1]
        payload = call_args.kwargs.get("json")
        params = payload["params"]
        assert params["model"] == "sale.order"
        assert params["method"] == "read"
        assert params["args"] == [[1]]
        assert params["kwargs"] == {"fields": ["name"]}
