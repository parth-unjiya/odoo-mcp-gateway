"""Entry point for odoo-mcp-gateway."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import anyio

from odoo_mcp_gateway import __version__
from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.server import create_server

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("odoo_mcp_gateway")


async def _run_streamable_http(server: FastMCP, settings: Settings) -> None:
    """Run streamable-http transport with our SessionResolverMiddleware injected.

    Why we don't just call ``server.run(transport="streamable-http")``:
    FastMCP's ``streamable_http_app()`` builds a Starlette app with the
    SDK's ``AuthenticationMiddleware`` + ``AuthContextMiddleware`` chain
    and a single Route whose endpoint is ``RequireAuthMiddleware``. We
    need our :class:`SessionResolverMiddleware` to run AFTER the SDK's
    ``AuthContextMiddleware`` (so the SDK's ``auth_context_var`` is
    already populated when we read it) but BEFORE the MCP tool dispatch
    (so the gateway's ``_current_session_key`` ContextVar is pinned
    when every tool handler runs).

    Starlette's middleware list orders OUTERMOST-FIRST. Appending to
    ``user_middleware`` puts us INNERMOST — exactly the slot between
    AuthContextMiddleware and the route endpoint. We then drive
    uvicorn directly (mirroring what ``run_streamable_http_async``
    does internally) since FastMCP doesn't expose a middleware-
    customisation hook.
    """
    import uvicorn
    from starlette.middleware import Middleware

    from odoo_mcp_gateway.core.auth.middleware import SessionResolverMiddleware
    from odoo_mcp_gateway.core.observability import (
        MetricsRegistry,
        build_health_routes,
        build_metrics_route,
    )

    starlette_app = server.streamable_http_app()
    starlette_app.user_middleware.append(Middleware(SessionResolverMiddleware))

    # Mount /health, /ready, and (if prometheus_client is installed)
    # /metrics. The gateway was stashed on the server in create_server
    # so we can hand it to the health probes without plumbing.
    gateway = getattr(server, "_odoo_gateway", None)
    if gateway is not None:
        for route in build_health_routes(gateway):
            starlette_app.router.routes.append(route)
    # Reuse the gateway's already-built MetricsRegistry — Sprint 5
    # wired call sites against gateway.metrics, so we MUST expose
    # the same instance via /metrics rather than building a fresh
    # (empty) registry here.
    metrics_registry = gateway.metrics if gateway is not None else MetricsRegistry()
    metrics_route = build_metrics_route(metrics_registry)
    if metrics_route is not None:
        starlette_app.router.routes.append(metrics_route)
        # Stash on the server for diagnostic introspection in tests.
        server._metrics_registry = metrics_registry  # type: ignore[attr-defined]

    # Force re-build of the middleware stack on next request so our
    # appended middleware actually appears in the chain. Starlette
    # caches the built stack lazily; we invalidate by setting it to
    # None (the next ``__call__`` rebuilds from ``user_middleware``).
    starlette_app.middleware_stack = None

    config = uvicorn.Config(
        starlette_app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.mcp_log_level.lower(),
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    """Load configuration and start the MCP server."""
    settings = Settings()

    logging.basicConfig(
        level=getattr(logging, settings.mcp_log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Configure structlog if available — emits JSON to stdout with
    # auto-injected ContextVars (mcp_session_id, trace_id). No-op
    # when the [observability] extra isn't installed.
    # Configure OTel tracing similarly — spans become observable
    # (and optionally exported) when OTEL_EXPORTER_OTLP_ENDPOINT
    # is set; otherwise the cost is in-process metadata only.
    from odoo_mcp_gateway.core.observability import (
        configure_structlog,
        configure_tracing,
    )

    configure_structlog()
    configure_tracing()

    logger.info(
        "Starting odoo-mcp-gateway v%s (transport=%s)",
        __version__,
        settings.mcp_transport,
    )

    server = create_server(settings)

    if settings.mcp_transport == "streamable-http":
        # Use our custom runner so SessionResolverMiddleware mounts
        # correctly. stdio mode stays on FastMCP's built-in path.
        anyio.run(_run_streamable_http, server, settings)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
