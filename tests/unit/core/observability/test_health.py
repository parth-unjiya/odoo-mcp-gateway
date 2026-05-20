"""Tests for /health and /ready route handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.testclient import TestClient

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.observability.health import build_health_routes
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext


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


def _app(gateway: GatewayContext) -> Starlette:
    return Starlette(routes=build_health_routes(gateway))


class TestHealthEndpoint:
    def test_returns_200_always(self) -> None:
        """Liveness must not check external dependencies."""
        gw = _gateway()
        client = TestClient(_app(gw))
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_no_auth_required(self) -> None:
        """Liveness is unauthenticated — load balancer probes it."""
        gw = _gateway()
        client = TestClient(_app(gw))
        # No Authorization header.
        resp = client.get("/health")
        assert resp.status_code == 200


class TestReadyEndpoint:
    def test_returns_200_when_no_sessions(self) -> None:
        """Empty session table is fine — gateway is ready for traffic."""
        gw = _gateway()
        client = TestClient(_app(gw))
        resp = client.get("/ready")
        # No active sessions → no Odoo probe needed → ready.
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["active_sessions"] == 0

    def test_returns_200_when_odoo_reachable(self) -> None:
        """With an active session, /ready probes Odoo via existing client."""
        # Clear the module-level probe cache so this test isn't influenced
        # by other tests that ran before it.
        from odoo_mcp_gateway.core.observability.health import _PROBE_CACHE

        _PROBE_CACHE.clear()

        gw = _gateway()
        # Stub an auth manager whose client.execute_kw succeeds.
        client_mock = MagicMock()
        client_mock.execute_kw = AsyncMock(return_value=5)
        mgr = MagicMock()
        mgr.get_active_client = MagicMock(return_value=client_mock)
        gw.auth_managers["2_test"] = mgr

        client = TestClient(_app(gw))
        resp = client.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["odoo_reachable"] is True

    def test_returns_503_when_odoo_unreachable(self) -> None:
        """If the live probe fails, /ready returns 503 with diagnostic."""
        from odoo_mcp_gateway.core.observability.health import _PROBE_CACHE

        _PROBE_CACHE.clear()

        gw = _gateway()
        client_mock = MagicMock()
        client_mock.execute_kw = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )
        mgr = MagicMock()
        mgr.get_active_client = MagicMock(return_value=client_mock)
        gw.auth_managers["2_test"] = mgr

        client = TestClient(_app(gw))
        resp = client.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["odoo_reachable"] is False
        assert "reason" in body


class TestProbeCacheTTL:
    @pytest.mark.asyncio
    async def test_cache_avoids_repeated_probes(self) -> None:
        """Second call within TTL must not hit Odoo again."""
        from odoo_mcp_gateway.core.observability.health import (
            _PROBE_CACHE,
            _probe_odoo_reachable,
        )

        _PROBE_CACHE.clear()
        gw = _gateway()
        client_mock = MagicMock()
        client_mock.execute_kw = AsyncMock(return_value=1)
        mgr = MagicMock()
        mgr.get_active_client = MagicMock(return_value=client_mock)
        gw.auth_managers["2_test"] = mgr

        # First call probes.
        assert await _probe_odoo_reachable(gw) is True
        assert client_mock.execute_kw.await_count == 1
        # Second call within TTL must hit the cache, not Odoo.
        assert await _probe_odoo_reachable(gw) is True
        assert client_mock.execute_kw.await_count == 1
