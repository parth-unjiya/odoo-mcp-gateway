"""OpenTelemetry tracing helpers for the MCP gateway.

Like the rest of the ``core/observability`` package, this module is
optional behind the ``[observability]`` extra. When
``opentelemetry-api`` / ``opentelemetry-sdk`` aren't installed,
every function in here is a no-op so call sites don't need to
``if available`` guard.

Two surfaces:

* :func:`configure_tracing` — call once at server startup. Wires the
  global OpenTelemetry ``TracerProvider`` and registers the OTLP
  exporter when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set; otherwise
  spans go to a no-op exporter (instrumentation overhead only —
  no network calls).
* :func:`tool_span` — async context manager that wraps a tool call
  with a span (``mcp.tool.<name>``) carrying the standard
  attributes (mcp.session.id, odoo.uid, mcp.tool.name).

httpx auto-instrumentation is enabled when
``opentelemetry-instrumentation-httpx`` is installed and
``configure_tracing`` runs successfully — every outbound Odoo
RPC then gets a child span automatically without per-call wiring.

The span hierarchy for one tool call:

    mcp.tool.<name>           (this module)
    └── http.client           (auto-instrumented httpx)

Sprint 6 may add the parent ``mcp.request`` span if the SDK exposes
the request lifecycle directly.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)

# Soft imports — observability stack is optional. ``unused-ignore``
# is bundled with each ``import-not-found`` so the type: ignore is
# silent when OTel IS installed (most install paths) and active when
# it isn't.
try:
    from opentelemetry import (
        trace,  # type: ignore[import-untyped, import-not-found, unused-ignore]
    )
    from opentelemetry.sdk.resources import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
        SERVICE_NAME,
        Resource,
    )
    from opentelemetry.sdk.trace import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
        TracerProvider,
    )
    from opentelemetry.sdk.trace.export import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
        BatchSpanProcessor,
    )

    TRACING_AVAILABLE = True
except ImportError:  # pragma: no cover - import-guard branch
    trace = None  # type: ignore[assignment, unused-ignore]
    Resource = None  # type: ignore[assignment, misc, unused-ignore]
    TracerProvider = None  # type: ignore[assignment, misc, unused-ignore]
    # The well-known OTel attribute key — used as a string fallback.
    SERVICE_NAME = "service.name"  # type: ignore[assignment, unused-ignore]
    BatchSpanProcessor = None  # type: ignore[assignment, misc, unused-ignore]
    TRACING_AVAILABLE = False


# Module-level flag set by configure_tracing — keeps subsequent
# calls cheap (we don't want to re-init the provider on every
# create_server in tests).
_TRACING_CONFIGURED = False


def configure_tracing(service_name: str = "odoo-mcp-gateway") -> bool:
    """Initialise the global OpenTelemetry tracer provider.

    Returns ``True`` if tracing is now active, ``False`` if the
    extras aren't installed (caller can degrade gracefully).

    Idempotent — calling twice is safe. The OTLP exporter is wired
    only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set; otherwise
    the provider runs with no exporter (instrumentation cost only,
    no I/O).
    """
    global _TRACING_CONFIGURED

    if not TRACING_AVAILABLE or trace is None:
        return False
    if _TRACING_CONFIGURED:
        return True

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        try:
            # Late import — opentelemetry-exporter-otlp is its own
            # extra; we don't bundle it.
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found, unused-ignore]
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTel tracing exporting to %s", otlp_endpoint)
        except ImportError:
            logger.warning(
                "OTEL_EXPORTER_OTLP_ENDPOINT set but "
                "opentelemetry-exporter-otlp not installed; spans will "
                "stay in-process. Install with "
                "`pip install opentelemetry-exporter-otlp`."
            )

    trace.set_tracer_provider(provider)

    # Auto-instrument httpx so every outbound Odoo RPC gets a span
    # under whatever parent is active. Best-effort — if the
    # instrumentation lib isn't installed we just skip.
    try:
        from opentelemetry.instrumentation.httpx import (  # type: ignore[import-not-found, unused-ignore]
            HTTPXClientInstrumentor,
        )

        HTTPXClientInstrumentor().instrument()
        logger.debug("httpx auto-instrumentation enabled")
    except ImportError:  # pragma: no cover
        logger.debug(
            "opentelemetry-instrumentation-httpx not installed; "
            "Odoo RPC spans will be missing"
        )

    _TRACING_CONFIGURED = True
    return True


@asynccontextmanager
async def tool_span(
    tool_name: str,
    gateway: GatewayContext | None = None,
    **attributes: Any,
) -> AsyncIterator[Any]:
    """Async context manager wrapping a tool call in an OTel span.

    Usage::

        async with tool_span("search_read", gateway, odoo_model="res.partner"):
            ...

    Attributes attached automatically:
    * ``mcp.tool.name``
    * ``mcp.session.id`` (hash of session_key — not raw PII)
    * ``odoo.uid``
    * Any keyword args you pass via ``**attributes``.

    When tracing isn't configured, yields ``None`` and the body
    runs unchanged. Span exceptions are recorded but re-raised so
    error semantics are preserved.
    """
    if not TRACING_AVAILABLE or trace is None or not _TRACING_CONFIGURED:
        yield None
        return

    tracer = trace.get_tracer("odoo_mcp_gateway")
    span_attrs: dict[str, Any] = {"mcp.tool.name": tool_name}
    # Pull caller identity from the gateway's active session.
    if gateway is not None and gateway.auth_managers:
        mgr = next(iter(gateway.auth_managers.values()), None)
        if mgr is not None and mgr.auth_result is not None:
            # Hash the session_key so the trace doesn't leak the
            # raw (uid_db) form to downstream systems.
            import hashlib

            sk = next(iter(gateway.auth_managers.keys()), None)
            if sk:
                span_attrs["mcp.session.id"] = hashlib.sha256(sk.encode()).hexdigest()[
                    :16
                ]
            span_attrs["odoo.uid"] = int(mgr.auth_result.uid)
    for key, value in attributes.items():
        # OTel only accepts primitive attribute values; coerce.
        if isinstance(value, (str, bool, int, float)):
            span_attrs[key] = value
        elif value is not None:
            span_attrs[key] = str(value)

    with tracer.start_as_current_span(
        f"mcp.tool.{tool_name}",
        attributes=span_attrs,
    ) as span:
        try:
            yield span
        except Exception as exc:
            # Record on the span so traces show the failure
            # without losing the stack at the call site.
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise


def shutdown_tracing() -> None:
    """Flush + shut down the tracer provider. Call from atexit."""
    if not TRACING_AVAILABLE or trace is None:
        return
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        try:
            provider.shutdown()
        except Exception:
            logger.debug("Tracer provider shutdown failed", exc_info=True)
