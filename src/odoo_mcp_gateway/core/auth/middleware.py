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
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

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

        # Pin the gateway's session ContextVar for the request task.
        # ContextVar uses PEP 567 task-local context propagation, so
        # any ``asyncio.create_task`` spawned inside the tool inherits
        # the value automatically.
        ctx_token = _set_session_key(session_key)
        try:
            await self.app(scope, receive, send)
        finally:
            # Always restore the prior value. Without this, the next
            # HTTP request handled on the same worker task would see
            # a stale session_key and resolve to whoever was last in.
            _reset_session_key(ctx_token)


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


# Keep the explicit setter import alive in case future call sites want
# to use it directly. Mypy in strict mode would otherwise flag the
# import-without-use.
__all__ = [
    "SessionResolverMiddleware",
    "set_current_session_key",
]
