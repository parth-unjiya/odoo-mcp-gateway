"""Authentication tool for the MCP gateway."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.client.exceptions import OdooAuthError, OdooError
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient
from odoo_mcp_gateway.core.auth.manager import AuthManager
from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.core.version.detector import detect_version
from odoo_mcp_gateway.server import (
    get_current_session_key,
    set_current_session_key,
)

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)


def register_auth_tools(server: FastMCP, gateway: GatewayContext) -> None:
    """Register authentication tools on the server."""

    @server.tool()
    async def login(
        method: str,
        credential: str,
        username: str = "",
        database: str = "",
    ) -> dict[str, Any]:
        """Authenticate with Odoo. Methods: 'api_key', 'password', or 'session'."""
        # Stable identifier for source-level (IP/connection) rate limiting.
        # In stdio mode there is no real IP, so we use the current session
        # key when available, falling back to a fixed "stdio" sentinel.
        # In HTTP mode this ideally would be the connection's IP — until
        # per-request middleware is added, "shared" provides a global
        # bucket that still catches naive username-rotation attacks.
        source_id = get_current_session_key() or "stdio"
        try:
            gate_error = await security_gate(
                gateway, "login", f"login_{username or 'anon'}"
            )
            if gate_error:
                return {"error": gate_error}

            # IP/source-level rate limit (catches username-rotation attacks
            # that bypass the per-username LoginRateLimiter).
            ip_lockout = gateway.login_ip_rate_limiter.check_allowed(source_id)
            if ip_lockout:
                return {"error": ip_lockout}

            # Check login brute force lockout
            lockout_msg = gateway.login_rate_limiter.check_allowed(username)
            if lockout_msg:
                return {"error": lockout_msg}

            if method not in ("api_key", "password", "session"):
                return {
                    "error": (
                        f"Unknown auth method: {method!r}. "
                        "Use 'api_key', 'password', or 'session'."
                    ),
                }

            if len(username) > 256:
                return {"error": "Username too long (max 256 characters)"}
            if len(credential) > 4096:
                return {"error": "Credential too long (max 4096 characters)"}

            db = database or gateway.settings.odoo_db
            if not db:
                return {
                    "error": (
                        "No database specified. Provide 'database' or set ODOO_DB."
                    ),
                }

            url = gateway.settings.odoo_url
            jsonrpc_client = JsonRpcClient(base_url=url)
            xmlrpc_client = XmlRpcClient(base_url=url)

            auth_mgr = AuthManager(
                jsonrpc_client=jsonrpc_client,
                xmlrpc_client=xmlrpc_client,
                session_timeout_seconds=gateway.settings.session_timeout_seconds,
                max_concurrent_sessions=gateway.settings.max_concurrent_sessions,
            )

            try:
                result = await auth_mgr.login(
                    method=method,
                    username=username,
                    credential=credential,
                    database=db,
                )
            except Exception:
                try:
                    await auth_mgr.close()
                except Exception:
                    logger.debug("Failed to close auth manager on login error")
                raise

            session_key = f"{result.uid}_{db}"
            old_mgr = gateway.auth_managers.get(session_key)
            if old_mgr is not None:
                try:
                    await old_mgr.close()
                except Exception:
                    logger.debug("Failed to close old auth manager")
            gateway.auth_managers[session_key] = auth_mgr
            auth_mgr.register_session(session_key)
            set_current_session_key(session_key)

            # Detect Odoo version after successful authentication
            version_info = None
            try:
                client = auth_mgr.get_active_client()
                version_info = await detect_version(client)
            except Exception:
                logger.warning("Could not detect Odoo version", exc_info=True)

            # Check plugin requirements against installed Odoo modules
            try:
                if hasattr(gateway, "plugin_registry"):
                    client = auth_mgr.get_active_client()
                    installed_raw = await client.execute_kw(
                        "ir.module.module",
                        "search_read",
                        [[["state", "=", "installed"]]],
                        {"fields": ["name"], "limit": 0},
                    )
                    installed_names = (
                        [m["name"] for m in installed_raw]
                        if isinstance(installed_raw, list)
                        else []
                    )
                    await gateway.plugin_registry.check_requirements(
                        installed_names
                    )
            except Exception:
                logger.debug(
                    "Plugin requirements check failed", exc_info=True
                )

            gateway.login_rate_limiter.record_success(username)
            gateway.login_ip_rate_limiter.record_success(source_id)

            response: dict[str, Any] = {
                "user": result.username,
                "uid": result.uid,
                "method": method,
                "groups": result.groups,
                "database": result.database,
            }
            if version_info is not None:
                response["version"] = version_info.full_string
                response["edition"] = version_info.edition
            return response

        except OdooAuthError as e:
            gateway.login_rate_limiter.record_failure(username)
            gateway.login_ip_rate_limiter.record_failure(source_id)
            return {"error": gateway.sanitize_error(e)}
        except OdooError as e:
            return {"error": gateway.sanitize_error(e)}
        except Exception as e:
            logger.exception("Unexpected error during login")
            return {"error": gateway.sanitize_error(e)}
