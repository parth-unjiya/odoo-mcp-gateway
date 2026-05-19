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
            self._get_active_client_unchecked(),
            result.uid,
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
        """Enrich *result* with the user's groups — display names AND XML IDs.

        Two parallel lists are populated:

        * ``result.groups`` — UNION of display names AND XML IDs. RBAC
          configs may use either form; both resolve the same way.
        * ``result.group_xml_ids`` — technical XML IDs like
          ``"base.group_system"``. For RBAC matching by callers that
          specifically want the technical (version-stable) form.

        XML IDs are fetched via ``res.groups.get_external_id()`` rather
        than ``ir.model.data.search_read``. The former uses ``sudo()``
        internally (see Odoo's ``BaseModel._get_external_ids``) and is
        therefore callable by ANY authenticated user. The latter requires
        ``base.group_erp_manager`` and silently failed for non-admin
        users, leaving ``group_xml_ids = []`` and breaking any RBAC
        config keyed on technical IDs.

        Three fallback layers are attempted so a transient Odoo glitch
        on any single API doesn't strand the user without XML IDs:

          1. ``res.groups.get_external_id([group_ids])`` — preferred,
             works for any user.
          2. ``ir.model.data.search_read`` — legacy path, kept as a
             safety net for admin sessions or non-standard Odoo forks.
          3. Empty list — last resort; gateway still works against
             display-name configs.

        Admin detection is NOT performed here — see
        :meth:`_detect_admin_via_xmlid` and
        :meth:`_verify_admin_via_has_group`.
        """
        client = self._get_active_client_unchecked()
        try:
            # Resolve the user's group memberships by reading the user
            # record directly, NOT by searching ``res.groups`` for the
            # user. The latter pattern broke on Odoo 19 because the
            # ``res.groups.users`` field was renamed to ``user_ids``
            # there, and a domain referencing the old field raised
            # "Invalid field res.groups.users". Reading
            # ``res.users.groups_id`` (v17/v18) or ``res.users.group_ids``
            # (v19+) sidesteps the rename entirely AND avoids the ACL
            # corner cases of searching ``res.groups`` as a non-admin.
            group_ids = await self._read_user_group_ids(client, result.uid)
            if not group_ids:
                return result
            # Now read the group records by ID. ``full_name`` is stable
            # across 17/18/19, but tolerate its absence on a fork that
            # may have dropped the helper.
            groups_data: Any = await client.execute_kw(
                "res.groups",
                "read",
                [group_ids, ["full_name", "name"]],
            )
            if not isinstance(groups_data, list):
                return result
            display_names = [
                str(g.get("full_name") or g.get("name") or "")
                for g in groups_data
                if isinstance(g, dict)
            ]
            xml_ids = await self._fetch_group_xml_ids(client, group_ids)
            # ``groups`` is the UNION of display names + XML IDs. This
            # makes RBAC matching work whether rbac.yaml uses technical
            # XML IDs (``base.group_user``) or display names
            # (``"User types / Internal User"``) — both forms resolve
            # the same way. ``group_xml_ids`` is also kept separate
            # for callers that specifically want the technical form.
            result.groups = display_names + xml_ids
            result.group_xml_ids = xml_ids
        except Exception:
            logger.warning("Could not fetch user groups", exc_info=True)
        return result

    async def _read_user_group_ids(
        self,
        client: OdooClientBase,
        uid: int,
    ) -> list[int]:
        """Return the user's effective group IDs across all Odoo versions.

        Odoo renamed the many2many between ``res.users`` and ``res.groups``
        when introducing the ``_id``-suffix convention:

          * Odoo 17 / 18: ``res.users.groups_id`` (resolved-with-implications)
          * Odoo 19+   : ``res.users.group_ids`` (DIRECT memberships only)
                         plus ``res.users.all_group_ids`` (with implications)

        RBAC matching MUST see the full implied set — a user who has
        ``sales_team.group_sale_manager`` implicitly belongs to
        ``base.group_user``, and config keyed on the latter must match.
        So on Odoo 19+ we prefer ``all_group_ids``; on Odoo 17/18 we
        use ``groups_id`` (which is already implication-resolved at the
        ORM level).

        Candidates are tried in order: ``all_group_ids`` (v19+, best
        signal), then ``groups_id`` (v17/v18 canonical), then
        ``group_ids`` (v19 direct-only fallback). Each candidate is
        skipped on "invalid field" errors; any other error re-raises
        so the outer ``_fetch_groups`` try/except still gates failure.

        Reading ``res.users`` for one's own ``uid`` works for ANY
        authenticated user (Odoo's ACLs allow self-read).
        """
        for field_name in ("all_group_ids", "groups_id", "group_ids"):
            try:
                user_rec = await client.execute_kw(
                    "res.users",
                    "read",
                    [[uid], [field_name]],
                )
            except Exception as exc:
                # If this looks like an "invalid field" error, try the
                # next candidate; otherwise re-raise so the outer
                # _fetch_groups catch-all handles it the same way it
                # would have without this helper.
                msg = str(exc).lower()
                if "invalid field" in msg or "no such field" in msg:
                    logger.debug(
                        "res.users.%s not present, trying alternate name",
                        field_name,
                    )
                    continue
                raise
            if isinstance(user_rec, list) and user_rec:
                rec = user_rec[0]
                if isinstance(rec, dict) and field_name in rec:
                    raw = rec[field_name]
                    if isinstance(raw, list):
                        return [int(g) for g in raw if isinstance(g, int)]
            # Field name accepted but the record didn't carry the
            # field — break out so we don't keep probing
            return []
        return []

    async def _fetch_group_xml_ids(
        self,
        client: OdooClientBase,
        group_ids: list[int],
    ) -> list[str]:
        """Return XML IDs for the given group records.

        Uses ``get_external_id`` (sudo'd, works for any user) with a
        legacy ``ir.model.data`` fallback for non-standard Odoo forks
        that may have shadowed the helper. Returns ``[]`` if both APIs
        fail — caller continues with display-name-only RBAC.
        """
        if not group_ids:
            return []

        # Preferred path: get_external_id is defined on every BaseModel
        # and calls ir.model.data through sudo() internally — works for
        # any authenticated user regardless of ACLs on ir.model.data.
        try:
            external_ids: Any = await client.execute_kw(
                "res.groups",
                "get_external_id",
                [group_ids],
            )
        except Exception:
            logger.debug(
                "res.groups.get_external_id failed; falling back to "
                "ir.model.data lookup",
                exc_info=True,
            )
            external_ids = None

        if isinstance(external_ids, dict):
            # Returned shape: {group_id: 'module.name'} or
            # {group_id: ''} for records without an XML ID. Drop empties.
            xml_ids = [
                str(xmlid)
                for xmlid in external_ids.values()
                if isinstance(xmlid, str) and xmlid and "." in xmlid
            ]
            if xml_ids:
                return xml_ids

        # Legacy fallback: ir.model.data direct read. Only succeeds for
        # privileged users but kept as a safety net for non-standard
        # Odoo configurations where get_external_id might be missing.
        try:
            xmlid_data: Any = await client.execute_kw(
                "ir.model.data",
                "search_read",
                [
                    [
                        ["model", "=", "res.groups"],
                        ["res_id", "in", group_ids],
                    ]
                ],
                {"fields": ["module", "name"]},
            )
        except Exception:
            logger.debug(
                "ir.model.data fallback also failed; group_xml_ids will be empty",
                exc_info=True,
            )
            return []

        if not isinstance(xmlid_data, list):
            return []
        return [
            f"{rec['module']}.{rec['name']}"
            for rec in xmlid_data
            if (isinstance(rec, dict) and rec.get("module") and rec.get("name"))
        ]

    async def _call_has_group(
        self,
        client: OdooClientBase,
        uid: int,
        xmlid: str,
    ) -> bool | None:
        """Call ``res.users.has_group`` with the right form for the Odoo version.

        Odoo 17 expects ``has_group(xmlid)`` (uid implicit from the session),
        while Odoo 18+ expects ``has_group([uid], xmlid)`` (recordset form).
        The two forms are mutually exclusive: passing the recordset to Odoo
        17 raises "Users.has_group() takes 2 positional arguments but 3 were
        given", and passing just the xmlid to Odoo 18+ returns ``False``
        unconditionally.

        We try the Odoo 18+ recordset form first (the dominant, supported
        version), and fall back to the Odoo 17 form on TypeError-like
        failures. Returns ``True``/``False`` on success or ``None`` if both
        forms failed (the caller decides what to do — typically fail-closed).
        """
        # Try Odoo 18+ form first
        try:
            result = await client.execute_kw(
                "res.users",
                "has_group",
                [[uid], xmlid],
            )
            return bool(result)
        except Exception as exc_18:
            # Fall back to Odoo 17 form (xmlid only, uid from session)
            try:
                result = await client.execute_kw(
                    "res.users",
                    "has_group",
                    [xmlid],
                )
                return bool(result)
            except Exception:
                logger.debug(
                    "has_group failed for %s with both call forms; first error: %s",
                    xmlid,
                    exc_18,
                    exc_info=True,
                )
                return None

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
            has_group = await self._call_has_group(client, result.uid, xmlid)
            # Strict boolean check — only True should grant admin.
            if has_group is True:
                result.is_admin = True
                return result
        return result

    async def _verify_admin_via_has_group(
        self,
        client: OdooClientBase,
        uid: int,
    ) -> bool:
        """Verify admin status by calling ``res.users.has_group`` server-side.

        This protects against a compromised proxy or MITM tampering with the
        auth response payload (which previously sourced ``is_admin``). The
        call is executed as the authenticated user against Odoo, so the
        result reflects real group membership in the database.

        Uses ``_call_has_group()`` for cross-version compatibility (Odoo 17
        vs Odoo 18+ have different call signatures). See that method's
        docstring for the version detail and regression notes.

        Falls back to ``False`` if the check fails on both call forms
        (fail-closed) — better to downgrade a real admin's privileges than
        to grant admin to a user whose status cannot be verified.
        """
        has_group = await self._call_has_group(client, uid, "base.group_system")
        # has_group may be None (both call forms failed) → fail closed.
        return has_group is True

    def _get_active_client_unchecked(self) -> OdooClientBase:
        """Return the active client without timeout checks.

        Used internally during login flow where we know the session is fresh.
        """
        if self._active_client is None:
            raise OdooAuthError("Not authenticated yet. Call login() first.")
        return self._active_client
