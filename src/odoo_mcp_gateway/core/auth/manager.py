"""Authentication manager supporting multiple Odoo auth strategies."""

from __future__ import annotations

import logging
import time
from typing import Any

from odoo_mcp_gateway.client.base import AuthResult, OdooClientBase
from odoo_mcp_gateway.client.exceptions import OdooAuthError
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient

logger = logging.getLogger(__name__)

# Global registry of active sessions keyed by session key.
# Used by AuthManager to enforce max_concurrent_sessions.
_active_sessions: dict[str, AuthManager] = {}


def _evict_expired_sessions() -> None:
    """Remove expired sessions from the global registry (lazy eviction)."""
    expired = [
        key
        for key, mgr in _active_sessions.items()
        if mgr._is_session_expired()  # noqa: SLF001
    ]
    for key in expired:
        mgr = _active_sessions.pop(key, None)
        if mgr is not None:
            mgr._session_key = None  # noqa: SLF001
            logger.debug("Evicted expired session: %s", key)


def get_active_session_count() -> int:
    """Return the number of currently registered sessions."""
    return len(_active_sessions)


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
        session_timeout_seconds: int = 1800,
        max_concurrent_sessions: int = 100,
    ) -> None:
        self._jsonrpc = jsonrpc_client
        self._xmlrpc = xmlrpc_client
        self._active_client: OdooClientBase | None = None
        self._auth_result: AuthResult | None = None
        self._session_timeout_seconds = session_timeout_seconds
        self._max_concurrent_sessions = max_concurrent_sessions
        self._last_activity_time: float = 0.0
        self._session_key: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close both RPC clients."""
        # Remove from global session registry
        if self._session_key is not None:
            _active_sessions.pop(self._session_key, None)
            self._session_key = None
        for client in (self._jsonrpc, self._xmlrpc):
            try:
                await client.close()
            except Exception:
                logger.debug("Failed to close client", exc_info=True)
        self._active_client = None
        self._auth_result = None
        self._last_activity_time = 0.0

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_active_client(self) -> OdooClientBase:
        """Return the client that was used for the last successful login.

        Also performs lazy session timeout enforcement: if the session
        has expired, the client is invalidated and an auth error is raised.
        """
        if self._active_client is None:
            raise OdooAuthError("Not authenticated yet. Call login() first.")
        if self._is_session_expired():
            self._invalidate_session()
            raise OdooAuthError(
                "Session has expired due to inactivity. Please login again."
            )
        self._touch_activity()
        return self._active_client

    @property
    def auth_result(self) -> AuthResult | None:
        """Last successful :class:`AuthResult`, or ``None``."""
        return self._auth_result

    @property
    def last_activity_time(self) -> float:
        """Monotonic timestamp of the last activity, or 0.0 if never used."""
        return self._last_activity_time

    def _is_session_expired(self) -> bool:
        """Check whether the current session has exceeded the timeout."""
        if self._last_activity_time == 0.0:
            return False
        elapsed = time.monotonic() - self._last_activity_time
        return elapsed > self._session_timeout_seconds

    def _touch_activity(self) -> None:
        """Update the last activity timestamp to now."""
        self._last_activity_time = time.monotonic()

    def _invalidate_session(self) -> None:
        """Invalidate the session without closing network resources."""
        if self._session_key is not None:
            _active_sessions.pop(self._session_key, None)
        self._active_client = None
        self._auth_result = None
        self._last_activity_time = 0.0

    def register_session(self, session_key: str) -> None:
        """Register this manager in the global session registry.

        Raises :class:`OdooAuthError` if the maximum concurrent session
        limit would be exceeded.
        """
        # Evict expired sessions lazily before checking the limit.
        _evict_expired_sessions()
        # If this session key is already registered (re-login), allow it.
        if session_key not in _active_sessions:
            if len(_active_sessions) >= self._max_concurrent_sessions:
                raise OdooAuthError(
                    f"Maximum concurrent sessions ({self._max_concurrent_sessions}) "
                    "reached. Please try again later."
                )
        self._session_key = session_key
        _active_sessions[session_key] = self

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
        # Determine admin status via XML IDs (locale-independent).
        result = await self._detect_admin_via_xmlid(result)
        # Verify admin status server-side (defense against tampered auth
        # responses from a compromised proxy/MITM). The `has_group` call
        # is executed as the authenticated user against Odoo, so a
        # tampered auth payload cannot flip the bit.
        verified_is_admin = await self._verify_admin_via_has_group(
            self._get_active_client_unchecked()
        )
        result.is_admin = verified_is_admin
        self._auth_result = result
        self._touch_activity()
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
        from odoo_mcp_gateway.client.base import Credential
        self._jsonrpc._session_id = Credential(session_id)  # noqa: SLF001
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
        """Enrich *result* with the user's group display names.

        Group names are used for RBAC matching. Also derives is_admin from
        group names as a fallback (used when ``has_group()`` is unavailable).
        The primary admin detection via XML IDs happens in
        :meth:`_detect_admin_via_xmlid`.
        """
        client = self._get_active_client_unchecked()
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
            # Derive is_admin from group membership as a fallback.
            # _detect_admin_via_xmlid runs after this for a more
            # reliable locale-independent check.
            if not result.is_admin:
                admin_indicators = {
                    "base.group_system",
                    "base.group_erp_manager",
                }
                result.is_admin = bool(admin_indicators & set(result.groups))
        except Exception:
            logger.warning("Could not fetch user groups", exc_info=True)
        return result

    async def _detect_admin_via_xmlid(self, result: AuthResult) -> AuthResult:
        """Detect admin status using ``res.users.has_group()`` with XML IDs.

        This is locale-independent unlike matching on group display names.
        Checks for ``base.group_system`` (Settings / Admin) and
        ``base.group_erp_manager`` (Access Rights).
        """
        if result.is_admin:
            return result
        client = self._get_active_client_unchecked()
        for xmlid in ("base.group_system", "base.group_erp_manager"):
            try:
                has_group: Any = await client.execute_kw(
                    "res.users",
                    "has_group",
                    [[result.uid], xmlid],
                )
                # Odoo's has_group returns a boolean. Be strict about the
                # type check to avoid false positives when the mock or an
                # unexpected response returns a non-boolean truthy value.
                if has_group is True:
                    result.is_admin = True
                    return result
            except Exception:
                logger.debug("has_group check failed for %s", xmlid, exc_info=True)
        return result

    async def _verify_admin_via_has_group(self, client: OdooClientBase) -> bool:
        """Verify admin status by calling ``res.users.has_group`` server-side.

        This protects against a compromised proxy or MITM tampering with the
        auth response payload (which previously sourced ``is_admin``). The
        call is executed as the authenticated user against Odoo, so the
        result reflects real group membership in the database.

        Falls back to ``False`` if the check fails (fail-closed) — better to
        downgrade a real admin's privileges than to grant admin to a user
        whose status cannot be verified.
        """
        try:
            result = await client.execute_kw(
                "res.users",
                "has_group",
                ["base.group_system"],
            )
            return bool(result)
        except Exception:
            # Fail closed — if we can't verify, assume not admin.
            logger.debug(
                "Admin verification via has_group failed; defaulting to False",
                exc_info=True,
            )
            return False

    def _get_active_client_unchecked(self) -> OdooClientBase:
        """Return the active client without timeout checks.

        Used internally during login flow where we know the session is fresh.
        """
        if self._active_client is None:
            raise OdooAuthError("Not authenticated yet. Call login() first.")
        return self._active_client
