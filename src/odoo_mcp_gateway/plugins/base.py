"""Base class for odoo-mcp-gateway plugins.

This module ships the convenience base class :class:`OdooPlugin` that
existing plugins inherit. It satisfies the
:class:`odoo_mcp_gateway.plugins.sdk.OdooMcpPlugin` Protocol with
no-op default implementations of the optional lifecycle hooks.

External plugin authors targeting Plugin SDK 1.0 may EITHER subclass
``OdooPlugin`` (convenience) OR implement the Protocol directly
(zero-dependency on the gateway's implementation). Both paths are
fully supported and equivalent at the registry level.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from odoo_mcp_gateway.plugins.sdk import PluginContext


class OdooPlugin(ABC):
    """Convenience base class for domain-specific tool plugins.

    Plugins provide additional MCP tools, resources, and prompts
    that extend the gateway's capabilities for specific Odoo domains.

    Subclasses MUST implement ``name`` and ``register()``.

    Subclasses SHOULD declare ``plugin_sdk_version`` as a class
    attribute, e.g. ``plugin_sdk_version = ">=1.0,<2.0"``. The
    registry checks this against the running SDK version. Plugins
    that omit it are loaded with a deprecation warning.

    The lifecycle hooks (``pre_register``, ``post_register``,
    ``pre_call``, ``post_call``, ``on_session_close``,
    ``on_external_event``) are implemented as no-ops here so
    subclasses can override only what they need without writing
    empty bodies for the rest.
    """

    # SDK compat range. Subclasses override (or leave at default —
    # the registry's deprecation warning will nudge them).
    plugin_sdk_version: str = ">=1.0,<2.0"

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
            ``GatewayContext`` (legacy) OR ``PluginContext`` (Plugin SDK 1.0+).
            Plugins MUST work with both; access common attributes via
            ``getattr(context, "gateway", context)`` if needed.
        """

    # ----------------------------------------------------------------
    # Lifecycle hooks (Plugin SDK 1.0) — default no-ops.
    #
    # Subclasses override only the hooks they need. Hooks are async
    # except ``register``. ``post_call`` MUST return the result it
    # was given (or a transformed value); the default returns it
    # unchanged.
    # ----------------------------------------------------------------

    # Lifecycle hooks below are INTENTIONALLY non-abstract — they have
    # no-op defaults so subclasses override only what they need.
    # The B027 lint suppression on each method below disables ruff's
    # "empty method in ABC without @abstractmethod" check, which
    # doesn't fit this opt-in-hook design pattern.

    async def pre_register(self, context: PluginContext) -> None:  # noqa: B027
        """Async hook before register(). Default: no-op."""

    async def post_register(self, context: PluginContext) -> None:  # noqa: B027
        """Async hook after all plugins are registered. Default: no-op."""

    async def pre_call(  # noqa: B027
        self,
        tool: str,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> None:
        """Async hook before every tool call. Default: no-op.

        Raising aborts the call.
        """

    async def post_call(
        self,
        tool: str,
        result: Any,
        context: PluginContext,
    ) -> Any:
        """Async hook after every tool call. Default: pass-through."""
        return result

    async def on_session_close(  # noqa: B027
        self,
        session_key: str,
        context: PluginContext,
    ) -> None:
        """Async hook when a session is evicted. Default: no-op."""

    async def on_external_event(  # noqa: B027
        self,
        event_type: str,
        payload: dict[str, Any],
        context: PluginContext,
    ) -> None:
        """RESERVED for v0.4.0 webhook delivery. Default: no-op."""
