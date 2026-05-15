"""MCP server setup with tool registration and session management."""

from __future__ import annotations

import asyncio
import atexit
import contextvars
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.auth.manager import AuthManager
from odoo_mcp_gateway.core.discovery.field_inspector import FieldInspector
from odoo_mcp_gateway.core.discovery.model_registry import ModelRegistry
from odoo_mcp_gateway.core.security.audit import AuditLogger
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    load_config,
)
from odoo_mcp_gateway.core.security.middleware import SecurityMiddleware
from odoo_mcp_gateway.core.security.rate_limit import (
    LoginIpRateLimiter,
    LoginRateLimiter,
    RateLimiter,
)
from odoo_mcp_gateway.core.security.rbac import RBACManager
from odoo_mcp_gateway.core.security.restrictions import RestrictionChecker
from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer

logger = logging.getLogger(__name__)

# ContextVar for per-request session isolation in HTTP mode.
#
# WARNING: HTTP mode (streamable-http transport) currently has KNOWN session
# isolation limitations. The contextvar is only set inside the login tool;
# subsequent tool calls in different request contexts may resolve to the
# first available session via _get_client/_get_auth_manager fallback.
#
# For multi-tenant HTTP deployments, proper per-request session resolution
# middleware is required (TODO). Until that is implemented, deploy HTTP mode
# as SINGLE-TENANT only — one user per server process.
#
# stdio mode is single-session by design and not affected by this limitation.
_current_session_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_session_key", default=None
)


class GatewayContext:
    """Holds shared state for the gateway: config, security, discovery."""

    def __init__(self, settings: Settings, gateway_config: GatewayConfig) -> None:
        self.settings = settings
        self.gateway_config = gateway_config
        self.auth_managers: dict[str, AuthManager] = {}
        self.restrictions = RestrictionChecker(
            config=gateway_config.restrictions,
            model_access=gateway_config.model_access,
        )
        self.rbac = RBACManager(
            config=gateway_config.rbac,
            model_access=gateway_config.model_access,
        )
        self.rate_limiter = RateLimiter(
            global_rate=settings.rate_limit_global,
            write_rate=settings.rate_limit_write,
        )
        self.login_rate_limiter = LoginRateLimiter()
        self.login_ip_rate_limiter = LoginIpRateLimiter()
        self.audit_logger = AuditLogger(
            backend="logger",
        )
        self.error_sanitizer = ErrorSanitizer()
        self.middleware = SecurityMiddleware(
            restrictions=self.restrictions,
            rbac=self.rbac,
            rate_limiter=self.rate_limiter,
            audit=self.audit_logger,
            sanitizer=self.error_sanitizer,
        )
        self.model_registry = ModelRegistry(
            model_access_config=gateway_config.model_access.model_dump(),
            blocked_models=gateway_config.restrictions.always_blocked,
        )
        self.field_inspector = FieldInspector(
            cache_ttl=settings.cache_ttl_seconds,
        )
        self._models_discovered = False

    async def cleanup(self) -> None:
        """Close all auth managers and their underlying connections."""
        for key, mgr in list(self.auth_managers.items()):
            try:
                await mgr.close()
            except Exception:
                logger.debug("Error closing auth manager %s", key, exc_info=True)
        self.auth_managers.clear()

    def sanitize_error(self, exc: Exception) -> str:
        """Sanitize an exception message for client consumption."""
        from odoo_mcp_gateway.client.exceptions import (
            OdooAccessError,
            OdooAuthError,
            OdooMissingError,
            OdooValidationError,
        )

        prefix_map: dict[type, str] = {
            OdooAuthError: "Authentication failed",
            OdooAccessError: "Access denied",
            OdooValidationError: "Validation error",
            OdooMissingError: "Record not found",
        }
        for exc_type, prefix in prefix_map.items():
            if isinstance(exc, exc_type):
                body = self.error_sanitizer.sanitize(str(exc))
                if body and body != "An unexpected error occurred":
                    return f"{prefix}: {body}"
                return prefix

        return self.error_sanitizer.sanitize_exception(exc)


def set_current_session_key(key: str | None) -> None:
    """Set the current session key for the active request context."""
    _current_session_key.set(key)


def get_current_session_key() -> str | None:
    """Get the current session key from the active request context."""
    return _current_session_key.get(None)


def _get_client(gateway: GatewayContext) -> Any:
    """Get the active authenticated Odoo client.

    In HTTP mode, uses the ``_current_session_key`` context variable
    to locate the correct session. Falls back to picking the first
    available session (single-user stdio mode).
    """
    if not gateway.auth_managers:
        raise ValueError("Not authenticated. Please call the login tool first.")
    session_key = _current_session_key.get(None)
    if session_key is not None and session_key in gateway.auth_managers:
        return gateway.auth_managers[session_key].get_active_client()
    # Fallback for stdio (single session) or when session key is not set
    auth_mgr = next(iter(gateway.auth_managers.values()))
    return auth_mgr.get_active_client()


def _get_auth_manager(gateway: GatewayContext) -> AuthManager:
    """Get the active AuthManager.

    In HTTP mode, uses the ``_current_session_key`` context variable
    to locate the correct session. Falls back to picking the first
    available session (single-user stdio mode).
    """
    if not gateway.auth_managers:
        raise ValueError("Not authenticated. Please call the login tool first.")
    session_key = _current_session_key.get(None)
    if session_key is not None and session_key in gateway.auth_managers:
        return gateway.auth_managers[session_key]
    return next(iter(gateway.auth_managers.values()))


def _sync_cleanup(gateway: GatewayContext) -> None:
    """Synchronous atexit handler that cleans up auth manager sessions.

    Since auth managers use async httpx clients, we attempt to run
    the async cleanup in an event loop.  If no loop is available
    (e.g. interpreter shutdown), we log a warning instead.
    """
    if not gateway.auth_managers:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Cannot block on a running loop; log a warning.
            logger.warning(
                "Event loop still running at shutdown; "
                "%d auth session(s) may not be closed cleanly",
                len(gateway.auth_managers),
            )
        else:
            loop.run_until_complete(gateway.cleanup())
    except RuntimeError:
        logger.warning(
            "No event loop available at shutdown; %d auth session(s) were not closed",
            len(gateway.auth_managers),
        )


def create_server(settings: Settings) -> FastMCP:
    """Create and configure the MCP server with all tools registered."""
    server = FastMCP(
        name="odoo-mcp-gateway",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )

    gateway_config = load_config(settings.config_dir)
    gateway = GatewayContext(settings, gateway_config)

    # Register atexit handler so auth manager connections are cleaned up
    # when the server process exits.
    atexit.register(_sync_cleanup, gateway)

    # Import and register all tool groups
    from odoo_mcp_gateway.tools.auth import register_auth_tools
    from odoo_mcp_gateway.tools.crud import register_crud_tools
    from odoo_mcp_gateway.tools.schema import register_schema_tools

    register_auth_tools(server, gateway)
    register_schema_tools(server, gateway)
    register_crud_tools(server, gateway)

    # Register MCP Resources and Prompts
    from odoo_mcp_gateway.prompts.handlers import register_prompts
    from odoo_mcp_gateway.resources.handlers import register_resources

    def _get_context() -> GatewayContext:
        return gateway

    register_resources(server, _get_context)
    register_prompts(server, _get_context)

    # Load and register workflow tools (if available)
    try:
        from odoo_mcp_gateway.core.workflow.registry import WorkflowRegistry
        from odoo_mcp_gateway.tools.workflow import register_workflow_tools

        workflow_registry = WorkflowRegistry()
        workflow_registry.load_stock_workflows()
        gateway.workflow_registry = workflow_registry  # type: ignore[attr-defined]
        register_workflow_tools(server, gateway, workflow_registry)
        logger.info("Workflow tools registered")
    except ImportError:
        logger.debug("Workflow module not available, skipping workflow tools")
    except Exception:
        logger.warning("Failed to load workflow tools", exc_info=True)

    # Discover and activate plugins
    from odoo_mcp_gateway.plugins.registry import PluginRegistry

    plugin_registry = PluginRegistry()

    # Discover plugins via entry_points (works when package is installed)
    plugin_registry.discover()
    if not plugin_registry._plugins:
        # Fallback: manual registration if entry points not available
        from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin
        from odoo_mcp_gateway.plugins.core.project import ProjectPlugin
        from odoo_mcp_gateway.plugins.core.sales import SalesPlugin

        plugin_registry.register_plugin(HRPlugin)
        plugin_registry.register_plugin(SalesPlugin)
        plugin_registry.register_plugin(ProjectPlugin)
        plugin_registry.register_plugin(HelpdeskPlugin)

    # Store the registry on the gateway so auth.py can check requirements
    gateway.plugin_registry = plugin_registry  # type: ignore[attr-defined]

    # Activate all enabled plugins
    activated = plugin_registry.activate(server, gateway)
    if activated:
        logger.info("Activated plugins: %s", ", ".join(activated))

    return server
