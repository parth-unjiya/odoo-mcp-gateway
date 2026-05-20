"""Observability scaffolding: structured logs, metrics, health endpoints.

This package is intentionally optional — all imports of the third-party
libraries (``prometheus_client``, ``structlog``) are guarded so the
base wheel works without them. Install via::

    pip install odoo-mcp-gateway[observability]

What ships in Sprint 2 (this iteration):

* ``health.py`` — ``/health`` (liveness) and ``/ready`` (readiness)
  Starlette route handlers.
* ``metrics.py`` — ``/metrics`` Prometheus scrape handler + the
  ``MetricsRegistry`` that exposes our standard counters/histograms.
* ``structured_logging.py`` — structlog configuration helper.

What's deferred to Sprint 5 (per the v0.3.0 plan):

* OpenTelemetry tracing wiring (``opentelemetry-instrumentation-httpx``,
  custom spans around ``security_gate`` / ``odoo.rpc``).
* Real exporter wiring (OTLP endpoint config via env vars).

See ``.release-drafts/v030-plan.md`` ADR-006 for the full design.
"""

from __future__ import annotations

from odoo_mcp_gateway.core.observability.health import (
    build_health_routes,
    is_ready,
)
from odoo_mcp_gateway.core.observability.metrics import (
    OBSERVABILITY_AVAILABLE,
    MetricsRegistry,
    build_metrics_route,
)
from odoo_mcp_gateway.core.observability.structured_logging import (
    configure_structlog,
)

__all__ = [
    "OBSERVABILITY_AVAILABLE",
    "MetricsRegistry",
    "build_health_routes",
    "build_metrics_route",
    "configure_structlog",
    "is_ready",
]
