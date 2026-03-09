"""Base class for odoo-mcp-gateway plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class OdooPlugin(ABC):
    """Base class for domain-specific tool plugins.

    Plugins provide additional MCP tools, resources, and prompts
    that extend the gateway's capabilities for specific Odoo domains.

    Subclasses must implement ``name`` and ``register()``.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier (e.g. 'hr', 'sales', 'project')."""

    @property
    def version(self) -> str:
        """Plugin version string."""
        return "0.1.0"

    @property
    def description(self) -> str:
        """Human-readable description."""
        return ""

    @property
    def required_odoo_modules(self) -> list[str]:
        """Odoo modules that must be installed for this plugin to work.

        The plugin registry checks these against ir.module.module at startup.
        If any are missing, the plugin is skipped with a warning.
        """
        return []

    @property
    def required_models(self) -> list[str]:
        """Odoo models that must exist for this plugin to work."""
        return []

    @abstractmethod
    def register(self, server: FastMCP, context: Any) -> None:
        """Register tools, resources, and prompts on the MCP server.

        This is the main extension point.  Use ``server.tool()``,
        ``server.resource()``, etc. to register handlers.

        Parameters
        ----------
        server:
            The FastMCP server instance.
        context:
            GatewayContext with auth, security, discovery.
        """
