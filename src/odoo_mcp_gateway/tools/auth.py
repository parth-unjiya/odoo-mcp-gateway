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
        try:
            gate_error = await security_gate(
                gateway, "login", f"login_{username or 'anon'}"
            )
            if gate_error:
                return {"error": gate_error}

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

            # Detect Odoo version after successful authentication
            version_info = None
            try:
                client = auth_mgr.get_active_client()
                version_info = await detect_version(client)
            except Exception:
                logger.warning("Could not detect Odoo version", exc_info=True)

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
            return {"error": gateway.sanitize_error(e)}
        except OdooError as e:
            return {"error": gateway.sanitize_error(e)}
        except Exception as e:
            logger.exception("Unexpected error during login")
            return {"error": gateway.sanitize_error(e)}
