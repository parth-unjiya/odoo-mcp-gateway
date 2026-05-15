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
    OdooSessionExpiredError,
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
        assert client._password.reveal() == "p"

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
    async def test_retries_once_on_session_expired(self) -> None:
        """Canonical session-expiry exception triggers one auto-retry."""
        auth_resp = _make_response(
            _success_body(_auth_body()),
            session_id="s1",
        )
        expired_resp = _make_response(
            _error_body(
                "odoo.http.SessionExpiredException",
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

    async def test_retries_on_message_marker_session_expired(self) -> None:
        """Unmapped exc name but session-expiry message still triggers retry."""
        auth_resp = _make_response(
            _success_body(_auth_body()),
            session_id="s1",
        )
        # No canonical exc name — Odoo sometimes only signals via message.
        expired_resp = _make_response(
            _error_body("", "Session expired, please log in again")
        )
        reauth_resp = _make_response(
            _success_body(_auth_body()),
            session_id="s2",
        )
        ok_resp = _make_response(_success_body([7]))

        mock = _mock_http([auth_resp, expired_resp, reauth_resp, ok_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        result = await client.execute_kw("res.partner", "search", [[]])

        assert result == [7]
        assert mock.post.call_count == 4

    async def test_access_denied_does_not_retry(self) -> None:
        """AccessDenied should propagate as OdooAuthError WITHOUT retry."""
        auth_resp = _make_response(
            _success_body(_auth_body()),
            session_id="s1",
        )
        denied_resp = _make_response(
            _error_body("odoo.exceptions.AccessDenied", "Access denied")
        )

        mock = _mock_http([auth_resp, denied_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")

        with pytest.raises(OdooAuthError, match="Access denied"):
            await client.execute_kw("res.partner", "search", [[]])

        # Authenticate (1) + failing call (1) = 2 POSTs, no retry.
        assert mock.post.call_count == 2

    async def test_no_retry_without_stored_credentials(self) -> None:
        """Session-expired propagates when no credentials are stored."""
        expired_resp = _make_response(
            _error_body("odoo.http.SessionExpiredException", "Session expired")
        )
        mock = _mock_http([expired_resp])
        client = JsonRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooSessionExpiredError):
            await client.execute_kw("res.partner", "search", [[]])

    async def test_retry_only_on_session_expired_not_access_denied(self) -> None:
        """OdooAuthError without session-expiry signal should NOT trigger retry."""
        from odoo_mcp_gateway.client.base import Credential

        client = JsonRpcClient(base_url="http://test:8069")
        client._db = "testdb"
        client._login = "user"
        client._password = Credential("pwd")

        # Mock _rpc to always raise OdooAuthError (not session expired)
        client._rpc = AsyncMock(side_effect=OdooAuthError("Access denied"))

        with pytest.raises(OdooAuthError, match="Access denied"):
            await client.execute_kw("res.partner", "search_read", [[]], {})
        # Should be called exactly once (no retry)
        assert client._rpc.call_count == 1

    async def test_retry_on_session_expired(self) -> None:
        """OdooSessionExpiredError SHOULD trigger one retry after re-authenticate."""
        from odoo_mcp_gateway.client.base import Credential

        client = JsonRpcClient(base_url="http://test:8069")
        client._db = "testdb"
        client._login = "user"
        client._password = Credential("pwd")

        # First call: session expired. Second call (after re-auth): success.
        call_count = {"n": 0}

        async def fake_rpc(*args: Any, **kwargs: Any) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OdooSessionExpiredError("Session expired")
            return [{"id": 1, "name": "Test"}]

        client._rpc = fake_rpc
        # Mock authenticate so the retry-reauth step doesn't hit network
        client.authenticate = AsyncMock(return_value=None)

        result = await client.execute_kw("res.partner", "search_read", [[]], {})
        assert result == [{"id": 1, "name": "Test"}]
        assert call_count["n"] == 2  # First failed, retry succeeded


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

    async def test_close_clears_credentials(self) -> None:
        """close() must zero out password, login, db, uid, and session_id."""
        resp = _make_response(
            _success_body(_auth_body()),
            session_id="sess_secret",
        )
        mock = _mock_http([resp])
        client = JsonRpcClient(_URL, httpx_client=mock)
        await client.authenticate("db", "u", "secret_pwd")

        await client.close()

        assert client._password.reveal() is None
        assert client._session_id.reveal() is None
        assert client._login is None
        assert client._db is None
        assert client._uid is None


# ------------------------------------------------------------------
# Credential leak protection
# ------------------------------------------------------------------


class TestCredentialLeakProtection:
    async def test_password_not_in_repr(self) -> None:
        """Credential should never appear in repr() of the client."""
        from odoo_mcp_gateway.client.base import Credential
        from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient

        client = JsonRpcClient(base_url="http://test:8069")
        # Manually set password (don't actually authenticate)
        client._password = Credential("super_secret_pwd_12345")

        # Neither repr nor str should leak it
        assert "super_secret_pwd_12345" not in repr(client._password)
        assert "super_secret_pwd_12345" not in str(client._password)
        assert "***" in repr(client._password) or "Credential" in repr(client._password)

    async def test_session_id_not_in_repr(self) -> None:
        """Session id is also wrapped and should not leak via repr/str."""
        from odoo_mcp_gateway.client.base import Credential
        from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient

        client = JsonRpcClient(base_url="http://test:8069")
        client._session_id = Credential("session_token_xyz_secret")

        assert "session_token_xyz_secret" not in repr(client._session_id)
        assert "session_token_xyz_secret" not in str(client._session_id)


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
