"""Tests for the elicitation helpers (ADR-008 Sprint 4)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext
from odoo_mcp_gateway.tools.elicitation import (
    _build_elicit_schema,
    detect_missing_required_fields,
    elicit_missing_fields,
)


def _gateway() -> GatewayContext:
    settings = Settings(
        odoo_url="http://localhost:8069",
        odoo_db="test",
        odoo_username="admin",
        odoo_api_key=SecretStr(""),
    )
    cfg = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(settings, cfg)


def _field(
    field_type: str = "char",
    required: bool = False,
    readonly: bool = False,
    string: str = "Field",
    selection: list | None = None,
    relation: str | None = None,
) -> Any:
    return SimpleNamespace(
        field_type=field_type,
        required=required,
        readonly=readonly,
        string=string,
        selection=selection or [],
        relation=relation,
        help_text=None,
    )


class TestDetectMissingRequiredFields:
    @pytest.mark.asyncio
    async def test_returns_missing_required(self) -> None:
        gw = _gateway()
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={
                "name": _field(required=True, string="Name"),
                "partner_id": _field(
                    field_type="many2one", required=True, relation="res.partner"
                ),
                "note": _field(required=False),
            }
        )
        missing = await detect_missing_required_fields(
            gw, client, "sale.order", values={"name": "Acme"}
        )
        # name is supplied; note is optional; partner_id is required + missing.
        assert missing == ["partner_id"]

    @pytest.mark.asyncio
    async def test_supplied_fields_are_not_missing_even_if_empty(self) -> None:
        """Empty-string check belongs to _validate_writable_fields, NOT
        to elicitation. Elicitation is only for fields the caller
        didn't mention at all."""
        gw = _gateway()
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={
                "name": _field(required=True),
            }
        )
        missing = await detect_missing_required_fields(
            gw, client, "sale.order", values={"name": ""}
        )
        assert missing == []

    @pytest.mark.asyncio
    async def test_readonly_required_fields_excluded(self) -> None:
        """Odoo populates readonly fields server-side — caller can't
        supply them, so they're not missing-from-the-caller."""
        gw = _gateway()
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={
                "create_date": _field(required=True, readonly=True),
                "name": _field(required=True),
            }
        )
        missing = await detect_missing_required_fields(
            gw, client, "sale.order", values={}
        )
        assert "create_date" not in missing
        assert "name" in missing

    @pytest.mark.asyncio
    async def test_field_inspection_failure_returns_empty(self) -> None:
        """If the schema can't be fetched, fall through silently —
        Odoo will surface the real error when create runs."""
        gw = _gateway()
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(side_effect=RuntimeError("network"))
        missing = await detect_missing_required_fields(
            gw, client, "sale.order", values={}
        )
        assert missing == []


# ── v0.3.3 follow-up: RBAC pre-filter (audit #1 finding #13) ───────


class TestDetectMissingRequiredFieldsRbacFilter:
    """Eliciting a required field the caller can't write per RBAC
    leads to a guaranteed downstream rejection.  ``detect_missing_*``
    must pre-filter those out so capable clients aren't asked to fill
    in fields they have no power to set.
    """

    def _gw_with_rbac_blocked(self, blocked_field: str) -> Any:
        """Build a gateway whose ``check_field_write`` blocks
        ``blocked_field`` for non-admins (admin path is unrestricted).
        """
        gw = _gateway()

        def _check(model: str, field: str, is_admin: bool) -> str | None:
            if is_admin:
                return None
            if field == blocked_field:
                return f"Field '{field}' requires admin access"
            return None

        gw.restrictions.check_field_write = _check  # type: ignore[method-assign]
        return gw

    @pytest.mark.asyncio
    async def test_nonadmin_excludes_rbac_blocked_required_field(self) -> None:
        gw = self._gw_with_rbac_blocked("sensitive_field")
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={
                "name": _field(required=True),
                "sensitive_field": _field(required=True),
            }
        )
        missing = await detect_missing_required_fields(
            gw, client, "sale.order", values={}, is_admin=False
        )
        # ``sensitive_field`` is RBAC-blocked for non-admin → omitted.
        assert "sensitive_field" not in missing
        assert "name" in missing

    @pytest.mark.asyncio
    async def test_admin_sees_all_required_fields(self) -> None:
        """Admin users bypass the RBAC pre-filter (check_field_write
        returns None for is_admin=True)."""
        gw = self._gw_with_rbac_blocked("sensitive_field")
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={
                "name": _field(required=True),
                "sensitive_field": _field(required=True),
            }
        )
        missing = await detect_missing_required_fields(
            gw, client, "sale.order", values={}, is_admin=True
        )
        assert sorted(missing) == ["name", "sensitive_field"]

    @pytest.mark.asyncio
    async def test_writable_required_field_included_for_nonadmin(self) -> None:
        """A required field that is NOT RBAC-blocked must still be in
        the returned list for non-admins."""
        gw = self._gw_with_rbac_blocked("sensitive_field")
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={
                "writable_field": _field(required=True),
            }
        )
        missing = await detect_missing_required_fields(
            gw, client, "sale.order", values={}, is_admin=False
        )
        assert missing == ["writable_field"]

    @pytest.mark.asyncio
    async def test_check_field_write_exception_does_not_propagate(self) -> None:
        """If the RBAC check itself raises, treat the field as
        writable (don't break elicitation on a transient failure)."""
        gw = _gateway()

        def _boom(model: str, field: str, is_admin: bool) -> str | None:
            raise RuntimeError("RBAC subsystem unreachable")

        gw.restrictions.check_field_write = _boom  # type: ignore[method-assign]
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={"name": _field(required=True)}
        )
        missing = await detect_missing_required_fields(
            gw, client, "sale.order", values={}, is_admin=False
        )
        assert missing == ["name"]


class TestBuildElicitSchema:
    def test_selection_field_gets_enum(self) -> None:
        gw = _gateway()
        field_info = {
            "state": _field(
                field_type="selection",
                required=True,
                string="State",
                selection=[("draft", "Draft"), ("sale", "Confirmed")],
            ),
        }
        schema = _build_elicit_schema(gw, "sale.order", ["state"], field_info)
        assert schema["type"] == "object"
        assert schema["required"] == ["state"]
        assert schema["properties"]["state"]["type"] == "string"
        assert schema["properties"]["state"]["enum"] == ["draft", "sale"]

    def test_many2one_field_gets_relation_hint(self) -> None:
        gw = _gateway()
        field_info = {
            "partner_id": _field(
                field_type="many2one",
                required=True,
                string="Customer",
                relation="res.partner",
            ),
        }
        schema = _build_elicit_schema(gw, "sale.order", ["partner_id"], field_info)
        assert schema["properties"]["partner_id"]["type"] == "integer"
        assert "res.partner" in schema["properties"]["partner_id"]["description"]


class TestElicitMissingFields:
    @pytest.mark.asyncio
    async def test_none_ctx_returns_none(self) -> None:
        gw = _gateway()
        client = MagicMock()
        result = await elicit_missing_fields(
            None, gw, client, "sale.order", ["partner_id"]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_missing_returns_none(self) -> None:
        gw = _gateway()
        client = MagicMock()
        ctx = MagicMock()
        result = await elicit_missing_fields(ctx, gw, client, "sale.order", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_client_accept_returns_filled(self) -> None:
        gw = _gateway()
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={
                "partner_id": _field(
                    field_type="many2one", required=True, relation="res.partner"
                ),
            }
        )
        ctx = MagicMock()
        ctx.request_id = "req-123"
        ctx.request_context.session.elicit_form = AsyncMock(
            return_value=SimpleNamespace(
                action="accept",
                content={"partner_id": 42},
            )
        )
        filled = await elicit_missing_fields(
            ctx, gw, client, "sale.order", ["partner_id"]
        )
        assert filled == {"partner_id": 42}

    @pytest.mark.asyncio
    async def test_client_decline_returns_none(self) -> None:
        gw = _gateway()
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={"partner_id": _field(required=True)}
        )
        ctx = MagicMock()
        ctx.request_id = "r"
        ctx.request_context.session.elicit_form = AsyncMock(
            return_value=SimpleNamespace(action="decline", content={})
        )
        filled = await elicit_missing_fields(
            ctx, gw, client, "sale.order", ["partner_id"]
        )
        assert filled is None

    @pytest.mark.asyncio
    async def test_client_error_returns_none(self) -> None:
        """If the elicitation channel fails for any reason, fall back
        gracefully — don't crash the tool."""
        gw = _gateway()
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={"partner_id": _field(required=True)}
        )
        ctx = MagicMock()
        ctx.request_id = "r"
        ctx.request_context.session.elicit_form = AsyncMock(
            side_effect=RuntimeError("client doesn't support elicitation")
        )
        filled = await elicit_missing_fields(
            ctx, gw, client, "sale.order", ["partner_id"]
        )
        assert filled is None

    @pytest.mark.asyncio
    async def test_extra_fields_filtered(self) -> None:
        """A misbehaving client returning extra keys shouldn't leak
        them into the values dict."""
        gw = _gateway()
        client = MagicMock()
        gw.field_inspector.get_fields = AsyncMock(
            return_value={"partner_id": _field(required=True)}
        )
        ctx = MagicMock()
        ctx.request_id = "r"
        ctx.request_context.session.elicit_form = AsyncMock(
            return_value=SimpleNamespace(
                action="accept",
                content={"partner_id": 1, "evil": "no"},
            )
        )
        filled = await elicit_missing_fields(
            ctx, gw, client, "sale.order", ["partner_id"]
        )
        assert filled == {"partner_id": 1}
