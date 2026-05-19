"""Authentication tool for the MCP gateway."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.client.exceptions import OdooAuthError, OdooError
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient
from odoo_mcp_gateway.core.auth.manager import AuthManager
from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.core.version.adapters import get_adapter
from odoo_mcp_gateway.core.version.detector import detect_version
from odoo_mcp_gateway.server import (
    get_current_session_key,
    set_current_session_key,
)


def _resolve_source_id() -> str:
    """Return a per-process source id for source-level rate limiting.

    The previous literal ``"stdio"`` sentinel collapsed every process's
    failure counter into a single global bucket — 30 failed logins from
    ANY process in any role would lock out EVERY process for 15 minutes
    (self-DoS). Including the PID gives each process its own bucket
    while still catching naive username-rotation brute force.

    Session-aware buckets (post-login) are not used here because a wrong
    password never establishes a session — using the session key would
    rate-limit the user instead of the source of the failure.
    """
    key = get_current_session_key()
    if key is not None:
        return key
    return f"stdio:{os.getpid()}"


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
        # See _resolve_source_id for the rationale on per-process bucketing.
        source_id = _resolve_source_id()
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

            # IMPORTANT: attempt login FIRST, before mutating
            # gateway.auth_managers. A failed login (typo'd password,
            # expired API key, brute force attempt) MUST NOT evict the
            # legitimate user's existing session — that would let any
            # caller DoS the active user just by submitting wrong
            # credentials.
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

            # Acquire the gateway-wide auth lock so the eviction +
            # registration + contextvar set are atomic with respect to
            # any other concurrent login() call AND with respect to
            # in-flight tool calls reading auth_managers. Without this,
            # an inopportune interleaving leaves auth_managers either
            # empty (briefly) or holding two managers (briefly) and
            # callers can resolve to the wrong session.
            async with gateway.auth_lock():
                # Single-user-per-process enforcement.
                # If a different user is logging in, close ALL existing
                # sessions first. Same-uid+same-db re-login is allowed.
                # NOTE: this only runs on successful login — see the
                # comment above the auth_mgr.login() call.
                for old_key in list(gateway.auth_managers.keys()):
                    if old_key == session_key:
                        continue
                    prior_mgr = gateway.auth_managers.pop(old_key)
                    try:
                        await prior_mgr.close()
                    except Exception:
                        logger.debug("Failed to close prior auth manager %s", old_key)
                    logger.info(
                        "Closed prior session %s — replaced by %s "
                        "(single-user-per-process)",
                        old_key,
                        session_key,
                    )

                # Same-uid re-login: close the existing one so the new
                # client supersedes it (don't leave dangling sessions).
                existing_mgr = gateway.auth_managers.get(session_key)
                if existing_mgr is not None and existing_mgr is not auth_mgr:
                    try:
                        await existing_mgr.close()
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

            # Activate the version-specific adapter for downstream tools
            # (CRUD field-rename translation, context normalization, ...).
            if version_info is not None:
                try:
                    gateway.version_adapter = get_adapter(version_info)
                except Exception:
                    logger.warning(
                        "Could not load version adapter for %s",
                        version_info.full_string,
                        exc_info=True,
                    )
                    gateway.version_adapter = None

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
                    await gateway.plugin_registry.check_requirements(installed_names)
            except Exception:
                logger.debug("Plugin requirements check failed", exc_info=True)

            gateway.login_rate_limiter.record_success(username)
            gateway.login_ip_rate_limiter.record_success(source_id)

            response: dict[str, Any] = {
                "user": result.username,
                "uid": result.uid,
                "method": method,
                "groups": result.groups,
                "is_admin": result.is_admin,
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
