"""MCP server setup with tool registration and session management."""

from __future__ import annotations

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
from odoo_mcp_gateway.core.security.rate_limit import RateLimiter
from odoo_mcp_gateway.core.security.rbac import RBACManager
from odoo_mcp_gateway.core.security.restrictions import RestrictionChecker
from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer

logger = logging.getLogger(__name__)


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


def _get_client(gateway: GatewayContext) -> Any:
    """Get the active authenticated Odoo client."""
    if not gateway.auth_managers:
        raise ValueError("Not authenticated. Please call the login tool first.")
    auth_mgr = next(iter(gateway.auth_managers.values()))
    return auth_mgr.get_active_client()


def _get_auth_manager(gateway: GatewayContext) -> AuthManager:
    """Get the active AuthManager."""
    if not gateway.auth_managers:
        raise ValueError("Not authenticated. Please call the login tool first.")
    return next(iter(gateway.auth_managers.values()))


def create_server(settings: Settings) -> FastMCP:
    """Create and configure the MCP server with all tools registered."""
    server = FastMCP(
        name="odoo-mcp-gateway",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )

    gateway_config = load_config(settings.config_dir)
    gateway = GatewayContext(settings, gateway_config)

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

    # Discover and activate plugins
    from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin
    from odoo_mcp_gateway.plugins.core.hr import HRPlugin
    from odoo_mcp_gateway.plugins.core.project import ProjectPlugin
    from odoo_mcp_gateway.plugins.core.sales import SalesPlugin
    from odoo_mcp_gateway.plugins.registry import PluginRegistry

    plugin_registry = PluginRegistry()

    # Register built-in domain plugins
    for plugin_cls in (HRPlugin, SalesPlugin, ProjectPlugin, HelpdeskPlugin):
        plugin_registry.register_plugin(plugin_cls)

    # Discover any external plugins from entry_points
    plugin_registry.discover()

    # Activate all enabled plugins
    activated = plugin_registry.activate(server, gateway)
    if activated:
        logger.info("Activated plugins: %s", ", ".join(activated))

    return server
