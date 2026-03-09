"""Tests for the AuthManager."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.client.exceptions import OdooAuthError
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient
from odoo_mcp_gateway.core.auth.manager import AuthManager

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _auth_result(**overrides: Any) -> AuthResult:
    defaults: dict[str, Any] = {
        "uid": 1,
        "session_id": "s1",
        "user_context": {"lang": "en_US"},
        "is_admin": False,
        "groups": [],
        "username": "admin",
        "database": "testdb",
    }
    defaults.update(overrides)
    return AuthResult(**defaults)


def _make_manager(
    jsonrpc_auth: AuthResult | Exception | None = None,
    xmlrpc_auth: AuthResult | Exception | None = None,
    execute_kw_result: Any = None,
    session_info: dict[str, Any] | Exception | None = None,
) -> AuthManager:
    """Build an AuthManager with mocked clients."""
    json_client = AsyncMock(spec=JsonRpcClient)
    xml_client = AsyncMock(spec=XmlRpcClient)

    if isinstance(jsonrpc_auth, Exception):
        json_client.authenticate = AsyncMock(side_effect=jsonrpc_auth)
    else:
        json_client.authenticate = AsyncMock(return_value=jsonrpc_auth)

    if isinstance(xmlrpc_auth, Exception):
        xml_client.authenticate = AsyncMock(side_effect=xmlrpc_auth)
    else:
        xml_client.authenticate = AsyncMock(return_value=xmlrpc_auth)

    # execute_kw is used for group fetching
    if isinstance(execute_kw_result, Exception):
        json_client.execute_kw = AsyncMock(side_effect=execute_kw_result)
        xml_client.execute_kw = AsyncMock(side_effect=execute_kw_result)
    else:
        json_client.execute_kw = AsyncMock(return_value=execute_kw_result or [])
        xml_client.execute_kw = AsyncMock(return_value=execute_kw_result or [])

    # For session strategy
    if isinstance(session_info, Exception):
        json_client._rpc = AsyncMock(side_effect=session_info)
    elif session_info is not None:
        json_client._rpc = AsyncMock(return_value=session_info)
    else:
        json_client._rpc = AsyncMock(return_value={"uid": 0})

    return AuthManager(
        jsonrpc_client=json_client,
        xmlrpc_client=xml_client,
    )


# ------------------------------------------------------------------
# API Key strategy
# ------------------------------------------------------------------


class TestApiKeyStrategy:
    async def test_success(self) -> None:
        result = _auth_result(uid=10)
        mgr = _make_manager(xmlrpc_auth=result)

        auth = await mgr.login("api_key", "admin", "my-api-key", "testdb")

        assert auth.uid == 10
        assert mgr.get_active_client() is mgr._xmlrpc

    async def test_invalid_key_raises(self) -> None:
        mgr = _make_manager(
            xmlrpc_auth=OdooAuthError("bad key"),
        )

        with pytest.raises(OdooAuthError, match="bad key"):
            await mgr.login("api_key", "admin", "wrong", "testdb")

    async def test_groups_fetched(self) -> None:
        result = _auth_result(uid=5)
        groups = [
            {"full_name": "base.group_user"},
            {"full_name": "sales.group_sale_manager"},
        ]
        mgr = _make_manager(
            xmlrpc_auth=result,
            execute_kw_result=groups,
        )

        auth = await mgr.login("api_key", "admin", "key", "testdb")

        assert "base.group_user" in auth.groups
        assert "sales.group_sale_manager" in auth.groups

    async def test_group_fetch_failure_does_not_break(
        self,
    ) -> None:
        result = _auth_result(uid=5)
        mgr = _make_manager(
            xmlrpc_auth=result,
            execute_kw_result=RuntimeError("network"),
        )

        auth = await mgr.login("api_key", "admin", "key", "testdb")
        # Should succeed despite group fetch failure
        assert auth.uid == 5
        assert auth.groups == []


# ------------------------------------------------------------------
# Password strategy
# ------------------------------------------------------------------


class TestPasswordStrategy:
    async def test_success(self) -> None:
        result = _auth_result(uid=2, session_id="sess-abc")
        mgr = _make_manager(jsonrpc_auth=result)

        auth = await mgr.login("password", "admin", "secret", "testdb")

        assert auth.uid == 2
        assert auth.session_id == "sess-abc"
        assert mgr.get_active_client() is mgr._jsonrpc

    async def test_invalid_password_raises(self) -> None:
        mgr = _make_manager(
            jsonrpc_auth=OdooAuthError("invalid credentials"),
        )

        with pytest.raises(OdooAuthError, match="invalid credentials"):
            await mgr.login("password", "admin", "wrong", "testdb")

    async def test_groups_fetched(self) -> None:
        result = _auth_result(uid=7)
        groups = [{"full_name": "base.group_system"}]
        mgr = _make_manager(
            jsonrpc_auth=result,
            execute_kw_result=groups,
        )

        auth = await mgr.login("password", "admin", "pass", "testdb")

        assert "base.group_system" in auth.groups

    async def test_auth_result_stored(self) -> None:
        result = _auth_result(uid=3)
        mgr = _make_manager(jsonrpc_auth=result)

        await mgr.login("password", "admin", "pass", "testdb")

        assert mgr.auth_result is not None
        assert mgr.auth_result.uid == 3


# ------------------------------------------------------------------
# Session strategy
# ------------------------------------------------------------------


class TestSessionStrategy:
    async def test_success(self) -> None:
        session_info: dict[str, Any] = {
            "uid": 42,
            "user_context": {"lang": "fr_FR"},
            "is_admin": True,
            "username": "admin",
        }
        mgr = _make_manager(session_info=session_info)

        auth = await mgr.login("session", "", "session-token-xyz", "testdb")

        assert auth.uid == 42
        assert auth.user_context == {"lang": "fr_FR"}
        assert auth.is_admin is True
        assert mgr.get_active_client() is mgr._jsonrpc

    async def test_invalid_session_raises(self) -> None:
        mgr = _make_manager(session_info={"uid": 0})

        with pytest.raises(OdooAuthError, match="invalid or expired"):
            await mgr.login("session", "", "bad-token", "testdb")

    async def test_network_error_raises_auth_error(
        self,
    ) -> None:
        mgr = _make_manager(session_info=RuntimeError("connection reset"))

        with pytest.raises(OdooAuthError, match="validation failed"):
            await mgr.login("session", "", "token", "testdb")

    async def test_session_id_passed_to_client(self) -> None:
        session_info: dict[str, Any] = {
            "uid": 1,
            "user_context": {},
            "is_admin": False,
            "username": "u",
        }
        mgr = _make_manager(session_info=session_info)

        await mgr.login("session", "", "my-session-id", "testdb")

        assert mgr._jsonrpc._session_id == "my-session-id"

    async def test_groups_fetched_after_session(self) -> None:
        session_info: dict[str, Any] = {
            "uid": 8,
            "user_context": {},
            "is_admin": False,
            "username": "u",
        }
        groups = [{"full_name": "base.group_portal"}]
        mgr = _make_manager(
            session_info=session_info,
            execute_kw_result=groups,
        )

        auth = await mgr.login("session", "", "tok", "testdb")

        assert "base.group_portal" in auth.groups


# ------------------------------------------------------------------
# Unknown strategy
# ------------------------------------------------------------------


class TestUnknownStrategy:
    async def test_raises(self) -> None:
        mgr = _make_manager()
        with pytest.raises(OdooAuthError, match="Unknown auth method"):
            await mgr.login("magic", "u", "p", "db")


# ------------------------------------------------------------------
# get_active_client
# ------------------------------------------------------------------


class TestGetActiveClient:
    def test_not_authenticated(self) -> None:
        mgr = _make_manager()
        with pytest.raises(OdooAuthError, match="Not authenticated"):
            mgr.get_active_client()

    async def test_returns_xmlrpc_after_api_key(self) -> None:
        result = _auth_result()
        mgr = _make_manager(xmlrpc_auth=result)
        await mgr.login("api_key", "u", "k", "db")
        assert mgr.get_active_client() is mgr._xmlrpc

    async def test_returns_jsonrpc_after_password(
        self,
    ) -> None:
        result = _auth_result()
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")
        assert mgr.get_active_client() is mgr._jsonrpc


# ------------------------------------------------------------------
# auth_result property
# ------------------------------------------------------------------


class TestAuthResultProperty:
    def test_none_before_login(self) -> None:
        mgr = _make_manager()
        assert mgr.auth_result is None

    async def test_set_after_login(self) -> None:
        result = _auth_result(uid=99)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")
        assert mgr.auth_result is not None
        assert mgr.auth_result.uid == 99


# ------------------------------------------------------------------
# close()
# ------------------------------------------------------------------


class TestAuthManagerClose:
    async def test_close_closes_both_clients(self) -> None:
        """close() should call close on both JSON-RPC and XML-RPC clients."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")

        await mgr.close()

        mgr._jsonrpc.close.assert_called_once()
        mgr._xmlrpc.close.assert_called_once()

    async def test_close_resets_active_client(self) -> None:
        """close() should reset _active_client to None."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")

        assert mgr._active_client is not None
        await mgr.close()
        assert mgr._active_client is None

    async def test_close_resets_auth_result(self) -> None:
        """close() should reset _auth_result to None."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")

        assert mgr.auth_result is not None
        await mgr.close()
        assert mgr.auth_result is None

    async def test_close_handles_jsonrpc_close_error(self) -> None:
        """close() should not raise if JSON-RPC client close fails."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")
        mgr._jsonrpc.close = AsyncMock(side_effect=RuntimeError("close fail"))

        await mgr.close()  # should not raise

        # XML-RPC close should still be called
        mgr._xmlrpc.close.assert_called_once()
        # State should still be cleaned up
        assert mgr._active_client is None
        assert mgr.auth_result is None

    async def test_close_handles_xmlrpc_close_error(self) -> None:
        """close() should not raise if XML-RPC client close fails."""
        result = _auth_result(uid=1)
        mgr = _make_manager(xmlrpc_auth=result)
        await mgr.login("api_key", "u", "k", "db")
        mgr._xmlrpc.close = AsyncMock(side_effect=RuntimeError("close fail"))

        await mgr.close()  # should not raise

        # JSON-RPC close should still have been attempted
        mgr._jsonrpc.close.assert_called_once()
        assert mgr._active_client is None

    async def test_close_handles_both_clients_failing(self) -> None:
        """close() should not raise even if both clients fail to close."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")
        mgr._jsonrpc.close = AsyncMock(side_effect=RuntimeError("fail 1"))
        mgr._xmlrpc.close = AsyncMock(side_effect=RuntimeError("fail 2"))

        await mgr.close()  # should not raise

        assert mgr._active_client is None
        assert mgr.auth_result is None

    async def test_close_idempotent(self) -> None:
        """close() should be safe to call multiple times."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")

        await mgr.close()
        await mgr.close()  # second call should not raise

        assert mgr._active_client is None
        assert mgr.auth_result is None

    async def test_close_before_login(self) -> None:
        """close() should work even if no login was done."""
        mgr = _make_manager()

        await mgr.close()

        assert mgr._active_client is None
        assert mgr.auth_result is None
