"""Entry point for odoo-mcp-gateway."""

from __future__ import annotations

import logging
import os
import sys

from odoo_mcp_gateway import __version__
from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.server import create_server

logger = logging.getLogger("odoo_mcp_gateway")


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

    transport = os.environ.get("MCP_TRANSPORT", settings.mcp_transport)

    if transport == "streamable-http":
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
