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
    get_current_http_client,
    get_current_session_key,
    set_current_session_key,
)


def _resolve_source_id() -> str:
    """Return a stable source id for failed-login rate limiting.

    Resolution order:

    1. The per-request HTTP-client ContextVar (set by
       :class:`SessionResolverMiddleware`). In HTTP mode this carries
       the immediate peer IP — or the first hop of ``X-Forwarded-For``
       when ``trust_proxy`` is enabled — so each remote attacker has
       their own bucket. CRITICAL: failed logins from peer A do not
       affect peer B, otherwise an attacker hammering bad creds at the
       gateway could DoS every legitimate caller for 15 minutes.
    2. Fall back to ``stdio:<pid>``. Used in stdio mode (single user
       per process by design) and as a defence-in-depth fallback when
       the HTTP middleware did not run (e.g. background task).

    We deliberately DO NOT use ``_current_session_key`` here: a wrong
    password never establishes a session, and even when it did, using
    a successful user's session key as their own rate-limit bucket
    would let one accidental typo penalise their next 30 logins. The
    rate limit must follow the SOURCE of the failure, not the victim.
    """
    http_client = get_current_http_client()
    if http_client is not None:
        return http_client
    return f"stdio:{os.getpid()}"


if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)


def _active_session_metadata(gateway: GatewayContext) -> dict[str, Any] | None:
    """Return non-secret metadata about the currently active session, if any.

    Used when reporting a FAILED ``login`` call: the caller often does not
    realise their prior session is still in effect (a wrong password does
    NOT downgrade the active identity by design — see
    ``test_failed_login_preserves_session``). UAT H1 confirmed that an LLM
    client cannot tell which identity it is now operating as.

    To close that observability gap we attach the prior session's
    ``{login, uid, database}`` to the error payload. We DO NOT include
    bearer tokens, api_keys, password hashes, group XML IDs, or any
    other material that would either let the caller assume the prior
    identity stronger than they already can OR reveal more authorisation
    surface than the prior login response already disclosed.

    Returns ``None`` when there is no resolvable active session — the
    caller should leave the error payload unchanged in that case (no
    ``active_session`` key).
    """
    session_key = get_current_session_key()
    mgr = None
    if session_key is not None:
        mgr = gateway.auth_managers.get(session_key)
    if mgr is None and len(gateway.auth_managers) == 1:
        # Stdio mode: single-session-per-process. Resolve the lone
        # session deterministically so the metadata is still emitted
        # even when the contextvar isn't set (e.g. legacy stdio call
        # path that predates session middleware).
        mgr = next(iter(gateway.auth_managers.values()))
    if mgr is None:
        return None
    auth_result = getattr(mgr, "auth_result", None)
    if auth_result is None:
        return None
    return {
        "login": auth_result.username,
        "uid": auth_result.uid,
        "database": auth_result.database,
    }


async def _safe_dispatch_session_close(
    gateway: GatewayContext, session_key: str
) -> None:
    """Fire ``on_session_close`` on every plugin, defensively.

    Plugins may or may not be wired up on the gateway (depends on
    construction path — tests sometimes skip the registry). We
    tolerate the missing attribute silently. Per-plugin failures
    are already isolated inside ``PluginRegistry.dispatch_on_session_close``.

    The eviction caller is NOT blocked by a misbehaving plugin —
    any registry-level surprise is caught and logged so the parent
    eviction flow still completes.
    """
    registry = getattr(gateway, "plugin_registry", None)
    if registry is None:
        return
    try:
        await registry.dispatch_on_session_close(gateway, session_key)
    except Exception:  # pragma: no cover - belt+braces
        logger.debug(
            "Plugin registry dispatch_on_session_close failed for %s",
            session_key,
            exc_info=True,
        )


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
                    # Revoke the prior session's bearer tokens FIRST so
                    # an in-flight HTTP request can't validate against
                    # a dead AuthManager.
                    gateway.revoke_session_tokens(old_key)
                    try:
                        await prior_mgr.close()
                    except Exception:
                        logger.debug("Failed to close prior auth manager %s", old_key)
                    # Audit fix #4: notify plugins that this session is
                    # gone so they can release per-session state (e.g.
                    # cached field maps, idempotency tokens). Wrapped in
                    # a try/except — the registry's own dispatcher
                    # already isolates per-plugin failures, this outer
                    # layer protects against a registry-level surprise
                    # (e.g. plugin_registry attribute not yet set).
                    await _safe_dispatch_session_close(gateway, old_key)
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
                    # Also fire on_session_close on re-login — the old
                    # AuthManager is logically being evicted.
                    await _safe_dispatch_session_close(gateway, session_key)
                gateway.auth_managers[session_key] = auth_mgr
                auth_mgr.register_session(session_key)
                set_current_session_key(session_key)
                # Mint a fresh bearer token for HTTP-transport callers.
                # Stdio callers ignore this — they have no need for a
                # bearer header — but issuing universally keeps the
                # response shape consistent and lets a process started
                # as stdio be re-attached over HTTP later (rare, but
                # cheap to support). ``issue_bearer_token`` revokes any
                # prior token for this session_key, so re-login rotates
                # the credential automatically.
                bearer_token = gateway.issue_bearer_token(session_key)

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

            # Observability: count the successful auth + refresh the
            # active-sessions gauge to len(auth_managers). The gauge
            # is monotonic in steady state (one session per process
            # in stdio; up to ``max_concurrent_sessions`` in HTTP).
            gateway.metrics.auth_attempts.labels(method=method, result="success").inc()
            gateway.metrics.active_sessions.set(len(gateway.auth_managers))

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
            # Surface the bearer token to HTTP-transport callers. The
            # field is named ``bearer_token`` rather than ``token`` to
            # avoid colliding with future OAuth ``access_token`` /
            # ``refresh_token`` fields and to make the wire shape
            # explicit ("send this in the Authorization header").
            #
            # Callers using stdio can ignore it; it's harmless metadata
            # for them and useful documentation of the auth surface.
            response["bearer_token"] = bearer_token
            # OAuth 2.0 "token_type" naming convention — not a secret.
            response["token_type"] = "Bearer"  # noqa: S105 - protocol literal
            return response

        except OdooAuthError as e:
            gateway.login_rate_limiter.record_failure(username)
            gateway.login_ip_rate_limiter.record_failure(source_id)
            gateway.metrics.auth_attempts.labels(method=method, result="failure").inc()
            # UAT H1: when a login attempt fails AND there is a prior
            # active session, the caller cannot tell from the error
            # payload that they are still operating as the previous
            # identity. Surface the active session's identity (login,
            # uid, database) — no secret material, no bearer token,
            # no api_key, no password hash. If no prior session
            # exists, the payload shape is unchanged.
            error_payload: dict[str, Any] = {"error": gateway.sanitize_error(e)}
            active = _active_session_metadata(gateway)
            if active is not None:
                error_payload["active_session"] = active
            return error_payload
        except OdooError as e:
            gateway.metrics.auth_attempts.labels(method=method, result="error").inc()
            return {"error": gateway.sanitize_error(e)}
        except Exception as e:
            logger.exception("Unexpected error during login")
            gateway.metrics.auth_attempts.labels(method=method, result="error").inc()
            return {"error": gateway.sanitize_error(e)}
