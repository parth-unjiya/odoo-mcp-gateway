"""ASGI middleware that pins the gateway's per-request session key.

Design (see ``.release-drafts/v030-plan.md`` ADR-001):

The MCP Python SDK already implements the OAuth-style auth pipeline via
two middlewares — ``BearerAuthBackend`` (reads the ``Authorization``
header, calls our ``TokenVerifier``, stores an ``AuthenticatedUser`` on
the Starlette scope) and ``AuthContextMiddleware`` (sets the SDK's own
``auth_context_var`` so tool handlers can call ``get_access_token()``).

What the SDK does NOT do is connect that authenticated identity to the
gateway's pre-existing ``_current_session_key`` ContextVar. Every CRUD
tool, plugin, and security gate in this codebase already reads from
that ContextVar (see ``server.py:get_current_session_key`` and the
strict resolution in ``_resolve_session_auth_manager``). Rather than
refactor 30+ tool handlers to a new identity scheme, this middleware
projects the SDK's authenticated identity into our existing one:

    SDK auth_context_var (AuthenticatedUser)
         │  client_id == session_key (per OdooTokenVerifier contract)
         ▼
    _current_session_key (str | None)
         │
         ▼
    every existing tool resolves the right AuthManager via
    GatewayContext.auth_managers[session_key]

That's the entire job. It must be mounted AFTER the SDK's
``AuthContextMiddleware`` so the contextvar is already populated when
we read it; mounting order is enforced by the wiring helper in
``__main__.py``.

PEP 567 guarantees the ContextVar value propagates to every task
created from the request task. The ``try/finally`` block restores the
prior value on exit so consecutive requests cannot leak state.
"""

from __future__ import annotations

from mcp.server.auth.middleware.auth_context import auth_context_var
from starlette.types import ASGIApp, Receive, Scope, Send

from odoo_mcp_gateway.server import set_current_session_key


class SessionResolverMiddleware:
    """Project the SDK's ``AuthenticatedUser`` into our session ContextVar.

    Non-HTTP scopes (lifespan, websocket) pass straight through. HTTP
    scopes with no authenticated user pass through unchanged too — the
    downstream tools see ``_current_session_key == None`` and refuse
    to operate, which is the correct "not authenticated" outcome.

    In addition to the session ContextVar, this middleware also pins a
    per-request HTTP-client identifier (the remote IP, or the first
    hop of ``X-Forwarded-For`` when ``gateway.settings.trust_proxy`` is
    True). Rate-limiting tools (notably the login tool) read that
    identifier so failed-login lockouts are scoped per peer rather than
    per process — without this, a single process-wide bucket would let
    any attacker DoS every other caller by submitting bad passwords
    until the bucket trips.
    """

    def __init__(self, app: ASGIApp, settings: object | None = None) -> None:
        self.app = app
        # ``settings`` is optional so existing tests that mount the
        # middleware without it (and never hit a real HTTP scope) keep
        # passing. Only the ``trust_proxy`` flag is consulted, and only
        # for HTTP scopes; the absence of settings means trust_proxy=False
        # which is the secure default.
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Pull the authenticated user the SDK staged for us. The SDK
        # sets ``auth_context_var`` inside its own AuthContextMiddleware
        # before our middleware runs, so by the time we read here, the
        # value is either the validated user or None.
        auth_user = auth_context_var.get()
        session_key: str | None
        if auth_user is not None and auth_user.access_token is not None:
            # Per the OdooTokenVerifier contract, ``client_id`` is our
            # internal session_key.
            session_key = auth_user.access_token.client_id
        else:
            session_key = None

        # Derive the HTTP-client identifier for rate limiting. This is
        # the IMMEDIATE peer's IP unless ``trust_proxy`` is enabled, in
        # which case we honour the first hop of X-Forwarded-For. The
        # trust_proxy guard is essential: if we honoured XFF
        # unconditionally, any caller could forge the header and bypass
        # IP-level rate limits.
        http_client = _derive_http_client_id(scope, self._settings)

        # Pin the gateway's session + http-client ContextVars for the
        # request task. ContextVar uses PEP 567 task-local context
        # propagation, so any ``asyncio.create_task`` spawned inside
        # the tool inherits both values automatically.
        ctx_token = _set_session_key(session_key)
        http_token = _set_http_client(http_client)
        try:
            await self.app(scope, receive, send)
        finally:
            # Always restore the prior values. Without this, the next
            # HTTP request handled on the same worker task would see
            # a stale session_key / client id and resolve to whoever
            # was last in.
            _reset_http_client(http_token)
            _reset_session_key(ctx_token)


def _derive_http_client_id(scope: Scope, settings: object | None) -> str | None:
    """Return a stable per-request client identifier for rate limiting.

    Resolution order:

    1. If ``settings.trust_proxy`` is True AND the request carries an
       ``X-Forwarded-For`` header, use the FIRST hop (the original
       client per the convention documented in MDN, RFC 7239 §5.2).
       We trim whitespace and lowercase for stability.
    2. Otherwise fall back to ``scope["client"][0]`` — the immediate
       peer's IP address as reported by Starlette / uvicorn.
    3. ``None`` if neither is available (e.g. a transport that didn't
       populate ``scope["client"]``). Callers must handle None as
       "no HTTP scope" and use their stdio fallback.
    """
    trust_proxy = bool(getattr(settings, "trust_proxy", False))
    if trust_proxy:
        # Headers are ASGI bytes-tuples. Build a case-insensitive map.
        headers = scope.get("headers") or ()
        for raw_name, raw_value in headers:
            try:
                name = raw_name.decode("latin-1").lower()
            except Exception:
                continue
            if name != "x-forwarded-for":
                continue
            try:
                xff = raw_value.decode("latin-1")
            except Exception:
                continue
            # XFF is "client, proxy1, proxy2". First entry is the
            # original client.
            first_hop = xff.split(",", 1)[0].strip().lower()
            if first_hop:
                return f"ip:{first_hop}"
            break

    client = scope.get("client")
    if client and isinstance(client, (tuple, list)) and len(client) >= 1:
        host = client[0]
        if host:
            return f"ip:{host}"
    return None


# ---------------------------------------------------------------------
# ContextVar plumbing
#
# We re-export the existing setter/getter from ``server`` so we don't
# fork the ContextVar. Two ContextVars with the same purpose would be a
# subtle correctness bug — every call site has to agree on which one
# carries the value.
# ---------------------------------------------------------------------


def _set_session_key(value: str | None) -> object:
    """Set the gateway's session ContextVar and return a reset token.

    Wrapper around the existing ``set_current_session_key`` that also
    captures the reset token so the middleware can restore the prior
    value on exit.
    """
    # The ContextVar lives in ``server.py``; we use the public setter
    # (which calls ``ContextVar.set`` internally) plus the underlying
    # ContextVar directly to get back the reset token. The underlying
    # ContextVar is module-private so we use a small accessor.
    from odoo_mcp_gateway.server import _current_session_key

    return _current_session_key.set(value)


def _reset_session_key(token: object) -> None:
    """Restore the ContextVar to its prior value via the reset token."""
    # ``ContextVar.reset`` accepts a Token returned by a prior ``set``;
    # the typing.cast is needed because ``object`` is wider than the
    # real return type (``contextvars.Token[str | None]``) but we
    # don't want to leak that detail through the middleware's public
    # surface.
    import contextvars

    from odoo_mcp_gateway.server import _current_session_key

    if isinstance(token, contextvars.Token):
        _current_session_key.reset(token)


def _set_http_client(value: str | None) -> object:
    """Set the HTTP-client ContextVar and return its reset token.

    Mirrors :func:`_set_session_key` so the middleware can pin and
    restore both ContextVars symmetrically inside the same try/finally.
    """
    from odoo_mcp_gateway.server import _current_http_client

    return _current_http_client.set(value)


def _reset_http_client(token: object) -> None:
    """Restore the HTTP-client ContextVar via the reset token."""
    import contextvars

    from odoo_mcp_gateway.server import _current_http_client

    if isinstance(token, contextvars.Token):
        _current_http_client.reset(token)


# Keep the explicit setter import alive in case future call sites want
# to use it directly. Mypy in strict mode would otherwise flag the
# import-without-use.
__all__ = [
    "SessionResolverMiddleware",
    "set_current_session_key",
]
