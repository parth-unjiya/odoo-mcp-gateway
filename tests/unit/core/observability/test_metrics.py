"""Tests for the Prometheus metrics registry + /metrics route."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.observability.metrics import (
    OBSERVABILITY_AVAILABLE,
    MetricsRegistry,
    build_metrics_route,
)

# Skip the whole module if prometheus_client isn't installed —
# obs is an optional extra, the test should be opt-in too.
pytestmark = pytest.mark.skipif(
    not OBSERVABILITY_AVAILABLE,
    reason="prometheus_client not installed; install [observability] extras",
)


class TestMetricsRegistry:
    def test_registry_is_available(self) -> None:
        reg = MetricsRegistry()
        assert reg.available is True

    def test_all_standard_metrics_exist(self) -> None:
        """The standard set documented in ADR-006 must be exposed."""
        reg = MetricsRegistry()
        # Touching each metric attribute must not raise; that confirms
        # the registry built them all.
        reg.tool_requests.labels(tool="search_read", status="success").inc()
        reg.tool_duration.labels(tool="search_read").observe(0.05)
        reg.auth_attempts.labels(method="password", result="success").inc()
        reg.odoo_rpc_duration.labels(model="res.partner", method="read").observe(0.02)
        reg.odoo_rpc_errors.labels(kind="OdooAccessError").inc()
        reg.active_sessions.set(3)
        reg.circuit_breaker_state.labels(name="odoo_rpc").set(0)
        reg.rate_limit_rejections.labels(kind="login_ip").inc()
        reg.field_cache_hits.inc()
        reg.field_cache_misses.inc()

    def test_render_emits_text_exposition(self) -> None:
        reg = MetricsRegistry()
        reg.tool_requests.labels(tool="search_read", status="success").inc()
        payload = reg.render()
        assert payload  # non-empty
        text = payload.decode("utf-8")
        # Prometheus text format: HELP, TYPE, and the metric line.
        assert "mcp_tool_requests_total" in text
        assert "tool=" in text


class TestMetricsRoute:
    def test_route_returns_prometheus_text(self) -> None:
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        reg = MetricsRegistry()
        reg.tool_requests.labels(tool="search_read", status="success").inc()
        route = build_metrics_route(reg)
        assert route is not None
        app = Starlette(routes=[route])
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "mcp_tool_requests_total" in resp.text


class TestNoOpMode:
    """When prometheus_client is NOT installed, every metric call is a no-op.

    We can't easily simulate that in a single test (the import-guard
    runs at module load), but we can verify the no-op stand-in's
    surface so the code is safe to call regardless.
    """

    def test_noop_metric_swallows_calls(self) -> None:
        from odoo_mcp_gateway.core.observability.metrics import _NoOpMetric

        n = _NoOpMetric()
        n.labels(a="b").inc()
        n.labels(a="b").set(5)
        n.labels(a="b").observe(0.5)
        n.labels(a="b").dec()
        # No exceptions, no return value contract.
