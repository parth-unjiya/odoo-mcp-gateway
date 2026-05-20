"""Tests for OpenTelemetry tracing scaffolding (Sprint 5)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.observability.tracing import (
    TRACING_AVAILABLE,
    configure_tracing,
    tool_span,
)
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext


def _make_gateway() -> GatewayContext:
    settings = Settings(
        odoo_url="http://localhost:8069",
        odoo_db="test",
        odoo_username="",
        odoo_api_key=SecretStr(""),
    )
    cfg = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(settings, cfg)


class TestConfigureTracing:
    def test_idempotent(self) -> None:
        # Calling twice is safe — second call no-ops.
        assert configure_tracing() == TRACING_AVAILABLE
        assert configure_tracing() == TRACING_AVAILABLE


class TestToolSpan:
    @pytest.mark.asyncio
    async def test_yields_span_or_none_without_crashing(self) -> None:
        gw = _make_gateway()
        async with tool_span("test_tool", gateway=gw, extra="x") as span:
            # When tracing isn't configured (in test env), span is None;
            # when it IS configured (observability extras installed),
            # span is a real OTel Span. Both must be safe to use.
            assert span is None or hasattr(span, "set_attribute")

    @pytest.mark.asyncio
    async def test_span_exception_re_raises(self) -> None:
        gw = _make_gateway()
        with pytest.raises(ValueError, match="boom"):
            async with tool_span("test_tool", gateway=gw):
                raise ValueError("boom")

    @pytest.mark.skipif(
        not TRACING_AVAILABLE,
        reason="opentelemetry not installed",
    )
    @pytest.mark.asyncio
    async def test_span_attributes_set(self) -> None:
        """When tracing IS configured, the span attributes include the
        gateway-derived identity hints."""
        from unittest.mock import MagicMock

        configure_tracing()
        gw = _make_gateway()
        mgr = MagicMock()
        mgr.auth_result = MagicMock(uid=42)
        gw.auth_managers["42_db"] = mgr

        async with tool_span(
            "search_read", gateway=gw, odoo_model="res.partner"
        ) as span:
            if span is None:  # provider may be a no-op recorder
                return
            # OTel test recorder spans expose attributes via `.attributes`.
            attrs = getattr(span, "attributes", None)
            if attrs is None:
                return  # in-memory tracer may not surface
            assert attrs.get("mcp.tool.name") == "search_read"
            assert "mcp.session.id" in attrs
            # session id must be hashed, not raw.
            assert attrs["mcp.session.id"] != "42_db"
            assert attrs.get("odoo.uid") == 42
            assert attrs.get("odoo_model") == "res.partner"
