"""Tests that the Sprint 2 MetricsRegistry is wired into call sites (Sprint 5)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.observability.metrics import OBSERVABILITY_AVAILABLE
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.core.security.middleware import security_gate
from odoo_mcp_gateway.server import GatewayContext

pytestmark = pytest.mark.skipif(
    not OBSERVABILITY_AVAILABLE,
    reason="prometheus_client not installed; install [observability]",
)


def _make_gateway(**settings_overrides: Any) -> GatewayContext:
    settings_defaults = {
        "odoo_url": "http://localhost:8069",
        "odoo_db": "test",
        "odoo_username": "",
        "odoo_api_key": SecretStr(""),
    }
    settings_defaults.update(settings_overrides)
    settings = Settings(**settings_defaults)
    cfg = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(settings, cfg)


def _staff_session(gw: GatewayContext) -> None:
    """Install a fake authenticated session so security_gate doesn't bail."""
    mgr = MagicMock()
    mgr.auth_result = MagicMock(
        uid=5,
        username="alice",
        is_admin=False,
        groups=["base.group_user"],
    )
    gw.auth_managers["5_test"] = mgr


def _metric_value(metric: Any, **labels: str) -> float:
    """Extract a Prometheus counter/gauge value for assertion."""
    sample = metric.labels(**labels)._value.get() if labels else metric._value.get()
    return float(sample)


class TestSecurityGateMetrics:
    @pytest.mark.asyncio
    async def test_allowed_request_increments_counter(self) -> None:
        gw = _make_gateway()
        _staff_session(gw)
        await security_gate(gw, "search_read", session_id="5_test")
        # Counter should have one allowed sample for search_read.
        v = _metric_value(
            gw.metrics.tool_requests, tool="search_read", status="allowed"
        )
        assert v == 1.0

    @pytest.mark.asyncio
    async def test_rate_limit_rejection_increments_counter(self) -> None:
        gw = _make_gateway()
        _staff_session(gw)
        # Force the rate limiter to deny.
        gw.rate_limiter = MagicMock()
        gw.rate_limiter.check = MagicMock(return_value=(False, "Too many requests"))
        await security_gate(gw, "search_read", session_id="5_test")
        v = _metric_value(gw.metrics.rate_limit_rejections, kind="read")
        assert v == 1.0
        v_tr = _metric_value(
            gw.metrics.tool_requests, tool="search_read", status="rate_limited"
        )
        assert v_tr == 1.0

    @pytest.mark.asyncio
    async def test_rbac_denial_increments_counter(self) -> None:
        gw = _make_gateway()
        _staff_session(gw)
        gw.rbac = MagicMock()
        gw.rbac.check_tool_access = MagicMock(
            return_value="Tool 'delete_record' requires base.group_system"
        )
        await security_gate(gw, "delete_record", session_id="5_test")
        v = _metric_value(
            gw.metrics.tool_requests, tool="delete_record", status="denied"
        )
        assert v == 1.0


class TestLoginMetrics:
    @pytest.mark.asyncio
    async def test_success_increments_success_counter(self) -> None:
        gw = _make_gateway()
        from odoo_mcp_gateway.tools.auth import register_auth_tools

        server = MagicMock()
        captured: dict[str, Any] = {}

        def _tool() -> Any:
            def dec(fn: Any) -> Any:
                captured[fn.__name__] = fn
                return fn

            return dec

        server.tool = _tool
        register_auth_tools(server, gw)
        login_fn = captured["login"]

        auth_result = AuthResult(
            uid=2,
            session_id="sess",
            user_context={},
            is_admin=False,
            groups=[],
            username="admin",
            database="test",
        )
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.login = AsyncMock(return_value=auth_result)
            mgr.close = AsyncMock()
            mgr.get_active_client = MagicMock(return_value=MagicMock())
            mgr.register_session = MagicMock()
            await login_fn(
                method="password",
                credential="pw",
                username="admin",
                database="test",
            )

        v = _metric_value(gw.metrics.auth_attempts, method="password", result="success")
        assert v == 1.0
        assert _metric_value(gw.metrics.active_sessions) == 1.0

    @pytest.mark.asyncio
    async def test_failure_increments_failure_counter(self) -> None:
        from odoo_mcp_gateway.client.exceptions import OdooAuthError
        from odoo_mcp_gateway.tools.auth import register_auth_tools

        gw = _make_gateway()
        server = MagicMock()
        captured: dict[str, Any] = {}

        def _tool() -> Any:
            def dec(fn: Any) -> Any:
                captured[fn.__name__] = fn
                return fn

            return dec

        server.tool = _tool
        register_auth_tools(server, gw)
        login_fn = captured["login"]

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.login = AsyncMock(side_effect=OdooAuthError("bad creds"))
            mgr.close = AsyncMock()
            await login_fn(
                method="password",
                credential="wrong",
                username="admin",
                database="test",
            )

        v = _metric_value(gw.metrics.auth_attempts, method="password", result="failure")
        assert v == 1.0
