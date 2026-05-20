"""Prometheus metrics for the MCP gateway.

The metrics live in a single registry that's instantiated once at
server-creation time. We expose them via a ``/metrics`` Starlette
route. The whole subsystem is OPTIONAL — if ``prometheus_client`` is
not installed, ``OBSERVABILITY_AVAILABLE`` is False and metric
recording becomes a no-op everywhere.

Metric design rules:

* **No per-user labels.** Cardinality bomb. Anything that could grow
  unbounded with usage (user IDs, request IDs, model+record_id) goes
  on a span attribute (OTel tracing — Sprint 5), not a Prom label.
* **Latency as histogram, not counter.** Histograms let Prometheus
  compute quantiles (`histogram_quantile`); counters can't.
* **Status labels capped at 2-3 values.** ``status={success,error}``
  is fine; ``status={ok, denied, ratelimit, validation, odoo_500,
  odoo_404, ...}`` is too many.

The metrics this module emits (the standard set documented in the
v0.3.0 plan, ADR-006):

* ``mcp_tool_requests_total{tool,status}`` — counter
* ``mcp_tool_duration_seconds{tool}`` — histogram
* ``mcp_auth_attempts_total{method,result}`` — counter
* ``odoo_rpc_duration_seconds{model,method}`` — histogram
* ``odoo_rpc_errors_total{kind}`` — counter
* ``mcp_active_sessions`` — gauge
* ``mcp_circuit_breaker_state{name}`` — gauge
* ``mcp_rate_limit_rejections_total{kind}`` — counter
* ``mcp_field_cache_hits_total / mcp_field_cache_misses_total`` —
  counters

Wiring of these metrics into the actual call sites is incremental:
v0.3.0 Sprint 2 ships the registry + ``/metrics`` route; Sprint 5
hooks the metrics into ``security_gate``, ``execute_kw``, and the
plugin loaders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

# Soft import — observability stack is optional.
try:
    from prometheus_client import (  # type: ignore[import-not-found, unused-ignore]
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    OBSERVABILITY_AVAILABLE = True
except ImportError:  # pragma: no cover - import-guard branch
    OBSERVABILITY_AVAILABLE = False
    CollectorRegistry = None  # type: ignore[assignment, misc]
    Counter = None  # type: ignore[assignment, misc]
    Gauge = None  # type: ignore[assignment, misc]
    Histogram = None  # type: ignore[assignment, misc]
    CONTENT_TYPE_LATEST = "text/plain"
    generate_latest = None  # type: ignore[assignment]


class _NoOpMetric:
    """Stand-in returned by ``MetricsRegistry`` when prometheus_client
    isn't installed. Every method does nothing so call sites don't
    need ``if registry is not None`` guards."""

    def labels(self, *args: Any, **kwargs: Any) -> _NoOpMetric:
        return self

    def inc(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def dec(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def observe(self, *_args: Any, **_kwargs: Any) -> None:
        pass


_NOOP = _NoOpMetric()


class MetricsRegistry:
    """Holds the standard set of gateway metrics.

    Construct ONCE per process. Pass the same registry into both the
    ``/metrics`` route handler and into the various call sites that
    record observations.

    Every metric attribute is typed ``Any`` because the runtime type
    flips between ``_NoOpMetric`` (when prometheus_client isn't
    installed) and the real ``Counter`` / ``Gauge`` / ``Histogram``
    classes. The metric protocol surface they share — ``.labels()``,
    ``.inc()``, ``.observe()``, ``.set()`` — is duck-typed.
    """

    tool_requests: Any
    tool_duration: Any
    auth_attempts: Any
    odoo_rpc_duration: Any
    odoo_rpc_errors: Any
    active_sessions: Any
    circuit_breaker_state: Any
    rate_limit_rejections: Any
    field_cache_hits: Any
    field_cache_misses: Any

    def __init__(self) -> None:
        if not OBSERVABILITY_AVAILABLE:
            self._registry = None
            self._available = False
            # Every metric attribute is a no-op stand-in.
            self.tool_requests = _NOOP
            self.tool_duration = _NOOP
            self.auth_attempts = _NOOP
            self.odoo_rpc_duration = _NOOP
            self.odoo_rpc_errors = _NOOP
            self.active_sessions = _NOOP
            self.circuit_breaker_state = _NOOP
            self.rate_limit_rejections = _NOOP
            self.field_cache_hits = _NOOP
            self.field_cache_misses = _NOOP
            return

        self._available = True
        self._registry = CollectorRegistry()
        # Latency histogram buckets tuned for typical MCP tool
        # latencies (single-digit ms for cache hits, hundreds of ms
        # for Odoo round-trips, seconds for grouped reads on big
        # tables).
        latency_buckets = (
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
        )
        self.tool_requests = Counter(
            "mcp_tool_requests_total",
            "Number of MCP tool invocations.",
            labelnames=("tool", "status"),
            registry=self._registry,
        )
        self.tool_duration = Histogram(
            "mcp_tool_duration_seconds",
            "End-to-end latency of MCP tool invocations.",
            labelnames=("tool",),
            buckets=latency_buckets,
            registry=self._registry,
        )
        self.auth_attempts = Counter(
            "mcp_auth_attempts_total",
            "Login attempts by method and result.",
            labelnames=("method", "result"),
            registry=self._registry,
        )
        self.odoo_rpc_duration = Histogram(
            "odoo_rpc_duration_seconds",
            "Latency of execute_kw calls to Odoo.",
            labelnames=("model", "method"),
            buckets=latency_buckets,
            registry=self._registry,
        )
        self.odoo_rpc_errors = Counter(
            "odoo_rpc_errors_total",
            "execute_kw failures by exception kind.",
            labelnames=("kind",),
            registry=self._registry,
        )
        self.active_sessions = Gauge(
            "mcp_active_sessions",
            "Currently authenticated sessions.",
            registry=self._registry,
        )
        self.circuit_breaker_state = Gauge(
            "mcp_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=half-open, 2=open).",
            labelnames=("name",),
            registry=self._registry,
        )
        self.rate_limit_rejections = Counter(
            "mcp_rate_limit_rejections_total",
            "Requests dropped by rate limiters.",
            labelnames=("kind",),
            registry=self._registry,
        )
        self.field_cache_hits = Counter(
            "mcp_field_cache_hits_total",
            "Field-inspector cache hits.",
            registry=self._registry,
        )
        self.field_cache_misses = Counter(
            "mcp_field_cache_misses_total",
            "Field-inspector cache misses.",
            registry=self._registry,
        )

    @property
    def available(self) -> bool:
        """True if prometheus_client is installed and the registry is live."""
        return self._available

    def render(self) -> bytes:
        """Return the Prometheus text-format payload for /metrics."""
        if not self._available or generate_latest is None or self._registry is None:
            return b""
        # ``generate_latest`` accepts a CollectorRegistry; we've guarded
        # both above so the cast is safe at runtime.
        result: bytes = generate_latest(self._registry)
        return result


def build_metrics_route(registry: MetricsRegistry) -> Route | None:
    """Return a Starlette ``/metrics`` Route, or None if Prom isn't installed.

    When ``prometheus_client`` is missing, we return None and the
    caller skips the route. This keeps the base wheel free of the
    Prom dependency while letting ``[observability]`` extras turn
    it on.
    """
    if not OBSERVABILITY_AVAILABLE:
        return None

    from starlette.responses import Response
    from starlette.routing import Route

    async def _metrics(_request: Request) -> Response:
        payload = registry.render()
        return Response(payload, media_type=CONTENT_TYPE_LATEST)

    return Route("/metrics", _metrics, methods=["GET"])
