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

    starlette_app = server.streamable_http_app()
    starlette_app.user_middleware.append(Middleware(SessionResolverMiddleware))
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
