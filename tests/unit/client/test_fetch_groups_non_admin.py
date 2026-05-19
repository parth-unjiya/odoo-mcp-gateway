"""Regression tests for the non-admin XML-ID lookup fix (H1).

Before v0.2.2-final, ``_fetch_groups`` called ``ir.model.data.search_read``
to enrich ``group_xml_ids`` — that ACL requires ``base.group_erp_manager``,
so non-admin users silently received ``group_xml_ids = []``. RBAC configs
keyed on technical XML IDs (``base.group_user``,
``sales_team.group_sale_manager``) then never matched for the actual
business users they were intended for, over-blocking everything.

The fix routes the membership read through ``res.users.read`` (which any
authenticated user can call on themselves) and the XML ID lookup through
``res.groups.get_external_id`` (sudo'd in Odoo).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient
from odoo_mcp_gateway.core.auth.manager import AuthManager


def _result(uid: int = 5) -> AuthResult:
    return AuthResult(
        uid=uid,
        session_id="s",
        user_context={},
        is_admin=False,
        groups=[],
        username="user",
        database="db",
    )


def _build_router(
    group_ids: list[int],
    display_names: dict[int, str] | None = None,
    external_ids: dict[int, str] | Exception | None = None,
    ir_model_data: list[dict[str, str]] | Exception | None = None,
    v19_field_name: bool = False,
):
    """Build a side_effect router that mimics the v0.2.2-final RPC shape."""

    async def _route(model: str, method: str, args: list, kw: Any = None) -> Any:
        if model == "res.users" and method == "read":
            requested = args[1] if len(args) >= 2 else []
            if v19_field_name:
                # v19 has all_group_ids (preferred) and group_ids,
                # but NOT groups_id.
                if "all_group_ids" in requested:
                    return [{"id": args[0][0], "all_group_ids": group_ids}]
                if "groups_id" in requested:
                    raise RuntimeError("Invalid field res.users.groups_id")
                if "group_ids" in requested:
                    return [{"id": args[0][0], "group_ids": group_ids}]
            else:
                # v17/v18: groups_id is the canonical field; the
                # probe's first attempt (all_group_ids) fails.
                if "all_group_ids" in requested:
                    raise RuntimeError("Invalid field res.users.all_group_ids")
                if "groups_id" in requested:
                    return [{"id": args[0][0], "groups_id": group_ids}]
            return []
        if model == "res.groups" and method == "read":
            rec_ids = args[0]
            return [
                {"id": gid, "full_name": (display_names or {}).get(gid, "")}
                for gid in rec_ids
            ]
        if model == "res.groups" and method == "get_external_id":
            if isinstance(external_ids, Exception):
                raise external_ids
            return external_ids or {}
        if model == "ir.model.data" and method == "search_read":
            if isinstance(ir_model_data, Exception):
                raise ir_model_data
            return ir_model_data or []
        if model == "res.users" and method == "has_group":
            return False
        return []

    return _route


class TestNonAdminXmlIdLookup:
    async def test_get_external_id_used_for_non_admin(self) -> None:
        """Non-admin users get XML IDs via the sudo'd get_external_id path."""
        result = _result(uid=7)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)
        json_client.execute_kw = AsyncMock(
            side_effect=_build_router(
                group_ids=[11],
                display_names={11: "User types / Internal User"},
                external_ids={11: "base.group_user"},
            )
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "db")

        assert "base.group_user" in auth.group_xml_ids
        assert "base.group_user" in auth.groups
        assert "User types / Internal User" in auth.groups

    async def test_get_external_id_empty_falls_back_to_ir_model_data(self) -> None:
        """When get_external_id returns blank entries, legacy fallback runs."""
        result = _result(uid=7)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)
        json_client.execute_kw = AsyncMock(
            side_effect=_build_router(
                group_ids=[11],
                display_names={11: "Custom Group"},
                external_ids={11: ""},
                ir_model_data=[],
            )
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "db")
        assert auth.group_xml_ids == []
        assert "Custom Group" in auth.groups

    async def test_get_external_id_failure_falls_back_to_ir_model_data(self) -> None:
        """On non-standard Odoo forks, fallback path is exercised."""
        result = _result(uid=7)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)
        json_client.execute_kw = AsyncMock(
            side_effect=_build_router(
                group_ids=[11],
                display_names={11: "Internal User"},
                external_ids=RuntimeError("method 'get_external_id' not found"),
                ir_model_data=[{"module": "base", "name": "group_user"}],
            )
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "db")
        assert "base.group_user" in auth.group_xml_ids

    async def test_both_paths_fail_login_still_succeeds(self) -> None:
        """Login must NOT fail when XML-ID enrichment is impossible."""
        result = _result(uid=7)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)
        json_client.execute_kw = AsyncMock(
            side_effect=_build_router(
                group_ids=[11],
                display_names={11: "Internal User"},
                external_ids=RuntimeError("method missing"),
                ir_model_data=RuntimeError("no access to ir.model.data"),
            )
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "db")
        assert auth.group_xml_ids == []
        assert "Internal User" in auth.groups

    async def test_odoo_19_field_rename_handled(self) -> None:
        """Odoo 19+: res.users.groups_id was renamed to group_ids."""
        result = _result(uid=7)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)
        json_client.execute_kw = AsyncMock(
            side_effect=_build_router(
                group_ids=[11],
                display_names={11: "Internal User"},
                external_ids={11: "base.group_user"},
                v19_field_name=True,
            )
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "db")
        assert "base.group_user" in auth.group_xml_ids
