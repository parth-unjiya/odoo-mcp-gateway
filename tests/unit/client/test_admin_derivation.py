"""Tests for is_admin derivation from group membership in AuthManager._fetch_groups."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient
from odoo_mcp_gateway.core.auth.manager import AuthManager

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _auth_result(**overrides: Any) -> AuthResult:
    defaults: dict[str, Any] = {
        "uid": 1,
        "session_id": None,
        "user_context": {},
        "is_admin": False,
        "groups": [],
        "username": "test",
        "database": "testdb",
    }
    defaults.update(overrides)
    return AuthResult(**defaults)


def _make_manager(
    *,
    auth_result: AuthResult,
    execute_kw_result: Any = None,
    has_group_result: bool = False,
    strategy: str = "api_key",
) -> AuthManager:
    """Build an AuthManager with mocked clients and controlled group data.

    ``execute_kw`` is dispatched by method name so a single helper can serve
    both ``search_read`` (groups) and ``has_group`` (admin verification).
    """
    json_client = AsyncMock(spec=JsonRpcClient)
    xml_client = AsyncMock(spec=XmlRpcClient)

    json_client.authenticate = AsyncMock(return_value=auth_result)
    xml_client.authenticate = AsyncMock(return_value=auth_result)

    groups = execute_kw_result if execute_kw_result is not None else []

    async def _dispatch(model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if method == "has_group":
            return has_group_result
        # Default: groups search_read
        return groups

    json_client.execute_kw = AsyncMock(side_effect=_dispatch)
    xml_client.execute_kw = AsyncMock(side_effect=_dispatch)

    # Session strategy needs _rpc
    json_client._rpc = AsyncMock(return_value={"uid": 0})

    return AuthManager(
        jsonrpc_client=json_client,
        xmlrpc_client=xml_client,
    )


# ------------------------------------------------------------------
# is_admin derivation from groups
# ------------------------------------------------------------------


class TestAdminDerivationFromGroups:
    """Verify _fetch_groups sets is_admin based on group membership."""

    async def test_group_system_sets_admin(self) -> None:
        """When groups include 'base.group_system' AND has_group confirms it,
        is_admin becomes True."""
        result = _auth_result(is_admin=False)
        groups = [
            {"full_name": "base.group_user"},
            {"full_name": "base.group_system"},
        ]
        mgr = _make_manager(
            auth_result=result,
            execute_kw_result=groups,
            has_group_result=True,
        )

        auth = await mgr.login("api_key", "test", "key", "testdb")

        assert auth.is_admin is True
        assert "base.group_system" in auth.groups

    async def test_erp_manager_groups_alone_does_not_grant_admin(self) -> None:
        """ERP manager group membership without group_system membership
        results in is_admin=False because the verification step only
        checks ``base.group_system`` (fail-closed)."""
        result = _auth_result(is_admin=False)
        groups = [
            {"full_name": "base.group_user"},
            {"full_name": "base.group_erp_manager"},
        ]
        # has_group_result=False — user is NOT in base.group_system.
        mgr = _make_manager(
            auth_result=result,
            execute_kw_result=groups,
            has_group_result=False,
        )

        auth = await mgr.login("api_key", "test", "key", "testdb")

        # Verification step overrides ERP manager → is_admin stays False.
        assert auth.is_admin is False
        assert "base.group_erp_manager" in auth.groups

    async def test_no_admin_groups_stays_false(self) -> None:
        """When groups don't include admin indicators, is_admin stays False."""
        result = _auth_result(is_admin=False)
        groups = [
            {"full_name": "base.group_user"},
            {"full_name": "base.group_portal"},
            {"full_name": "sales.group_sale_salesman"},
        ]
        mgr = _make_manager(auth_result=result, execute_kw_result=groups)

        auth = await mgr.login("api_key", "test", "key", "testdb")

        assert auth.is_admin is False
        assert "base.group_user" in auth.groups
        assert "base.group_portal" in auth.groups

    async def test_tampered_is_admin_overridden_by_verify(self) -> None:
        """When is_admin is True in the auth response (potentially tampered)
        but ``has_group`` returns False, the verified value wins (fail-closed).
        """
        result = _auth_result(is_admin=True)
        groups = [
            {"full_name": "base.group_user"},
        ]
        mgr = _make_manager(
            auth_result=result,
            execute_kw_result=groups,
            has_group_result=False,
            strategy="password",
        )

        auth = await mgr.login("password", "admin", "pass", "testdb")

        # The forged is_admin=True is overridden by the verified False.
        assert auth.is_admin is False
        # Groups should still be populated
        assert "base.group_user" in auth.groups

    async def test_already_admin_confirmed_by_has_group(self) -> None:
        """When is_admin is True AND has_group confirms it, stays True."""
        result = _auth_result(is_admin=True)
        groups = [
            {"full_name": "base.group_system"},
        ]
        mgr = _make_manager(
            auth_result=result,
            execute_kw_result=groups,
            has_group_result=True,
        )

        auth = await mgr.login("api_key", "admin", "key", "testdb")

        assert auth.is_admin is True

    async def test_empty_groups_no_admin(self) -> None:
        """Empty groups list should not grant admin."""
        result = _auth_result(is_admin=False)
        mgr = _make_manager(auth_result=result, execute_kw_result=[])

        auth = await mgr.login("api_key", "test", "key", "testdb")

        assert auth.is_admin is False
        assert auth.groups == []

    async def test_group_fetch_failure_preserves_original_admin(self) -> None:
        """If group fetching fails, the original is_admin value is preserved."""
        result = _auth_result(is_admin=False)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)

        xml_client.authenticate = AsyncMock(return_value=result)
        # execute_kw raises to simulate network failure
        xml_client.execute_kw = AsyncMock(side_effect=RuntimeError("network error"))
        json_client._rpc = AsyncMock(return_value={"uid": 0})

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("api_key", "test", "key", "testdb")

        assert auth.is_admin is False
        assert auth.groups == []
