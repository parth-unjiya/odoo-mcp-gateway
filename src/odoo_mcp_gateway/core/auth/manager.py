"""Authentication manager supporting multiple Odoo auth strategies."""

from __future__ import annotations

import logging
from typing import Any

from odoo_mcp_gateway.client.base import AuthResult, OdooClientBase
from odoo_mcp_gateway.client.exceptions import OdooAuthError
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient

logger = logging.getLogger(__name__)


class AuthManager:
    """Orchestrates authentication against Odoo.

    Three strategies are supported:

    * ``api_key``  -- XML-RPC authenticate with username + API key
    * ``password`` -- JSON-RPC ``/web/session/authenticate``
    * ``session``  -- Reuse an existing browser session cookie
    """

    def __init__(
        self,
        jsonrpc_client: JsonRpcClient,
        xmlrpc_client: XmlRpcClient,
    ) -> None:
        self._jsonrpc = jsonrpc_client
        self._xmlrpc = xmlrpc_client
        self._active_client: OdooClientBase | None = None
        self._auth_result: AuthResult | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close both RPC clients."""
        for client in (self._jsonrpc, self._xmlrpc):
            try:
                await client.close()
            except Exception:
                logger.debug("Failed to close client", exc_info=True)
        self._active_client = None
        self._auth_result = None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_active_client(self) -> OdooClientBase:
        """Return the client that was used for the last successful login."""
        if self._active_client is None:
            raise OdooAuthError("Not authenticated yet. Call login() first.")
        return self._active_client

    @property
    def auth_result(self) -> AuthResult | None:
        """Last successful :class:`AuthResult`, or ``None``."""
        return self._auth_result

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def login(
        self,
        method: str,
        username: str,
        credential: str,
        database: str,
    ) -> AuthResult:
        """Authenticate using the specified *method*.

        Parameters
        ----------
        method:
            One of ``"api_key"``, ``"password"``, ``"session"``.
        username:
            Odoo login name (not used for ``"session"``).
        credential:
            API key, password, or session_id depending on *method*.
        database:
            Odoo database name.
        """
        if method == "api_key":
            result = await self._login_api_key(database, username, credential)
        elif method == "password":
            result = await self._login_password(database, username, credential)
        elif method == "session":
            result = await self._login_session(database, credential)
        else:
            raise OdooAuthError(f"Unknown auth method: {method!r}")

        # Fetch user groups to populate result.groups.
        result = await self._fetch_groups(result)
        self._auth_result = result
        return result

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    async def _login_api_key(self, db: str, username: str, api_key: str) -> AuthResult:
        """Strategy A: XML-RPC with api_key in place of password."""
        result = await self._xmlrpc.authenticate(db, username, api_key)
        self._active_client = self._xmlrpc
        return result

    async def _login_password(
        self, db: str, username: str, password: str
    ) -> AuthResult:
        """Strategy B: JSON-RPC session auth."""
        result = await self._jsonrpc.authenticate(db, username, password)
        self._active_client = self._jsonrpc
        return result

    async def _login_session(self, db: str, session_id: str) -> AuthResult:
        """Strategy C: Reuse existing browser session cookie."""
        # Inject the session cookie and ask Odoo for session info.
        self._jsonrpc._session_id = session_id  # noqa: SLF001
        try:
            info: dict[str, Any] = await self._jsonrpc._rpc(  # noqa: SLF001
                "/web/session/get_session_info",
                {},
            )
        except Exception as exc:
            raise OdooAuthError(f"Session token validation failed: {exc}") from exc

        uid: int = info.get("uid", 0)
        if not uid:
            raise OdooAuthError("Session token is invalid or expired")

        self._active_client = self._jsonrpc
        return AuthResult(
            uid=uid,
            session_id=session_id,
            user_context=info.get("user_context", {}),
            is_admin=info.get("is_admin", False),
            groups=[],
            username=info.get("username", ""),
            database=db,
        )

    # ------------------------------------------------------------------
    # Group fetching
    # ------------------------------------------------------------------

    async def _fetch_groups(self, result: AuthResult) -> AuthResult:
        """Enrich *result* with the user's group XML IDs."""
        client = self.get_active_client()
        try:
            groups_data: Any = await client.execute_kw(
                "res.groups",
                "search_read",
                [[["users", "in", [result.uid]]]],
                {"fields": ["full_name"]},
            )
            if isinstance(groups_data, list):
                result.groups = [
                    str(g.get("full_name", ""))
                    for g in groups_data
                    if isinstance(g, dict)
                ]
            # Derive is_admin from group membership if not already set
            if not result.is_admin:
                admin_indicators = {
                    "base.group_system",
                    "base.group_erp_manager",
                }
                result.is_admin = bool(admin_indicators & set(result.groups))
        except Exception:
            logger.warning("Could not fetch user groups", exc_info=True)
        return result
