"""Tests for SessionResolverMiddleware (Sprint 1, S1.3).

The middleware projects the SDK's authenticated user into the gateway's
``_current_session_key`` ContextVar. These tests verify:

* HTTP scope with an AuthenticatedUser → ContextVar pinned to client_id.
* HTTP scope without auth → ContextVar stays None (downstream tools
  refuse to operate).
* Non-HTTP scopes (lifespan, websocket) pass through unchanged.
* The ContextVar is RESET on exit, even if the inner app raises.
* Concurrent requests with different users don't cross.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from odoo_mcp_gateway.core.auth.middleware import SessionResolverMiddleware
from odoo_mcp_gateway.server import get_current_session_key, set_current_session_key


class _CapturingApp:
    """ASGI app that records the session_key visible while it runs."""

    def __init__(self) -> None:
        self.captured: list[str | None] = []
        self.raise_on_call: Exception | None = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.captured.append(get_current_session_key())
        if self.raise_on_call is not None:
            raise self.raise_on_call


def _http_scope() -> dict[str, Any]:
    return {"type": "http", "headers": [], "method": "POST", "path": "/mcp"}


def _set_auth_user(session_key: str) -> Any:
    """Install an AuthenticatedUser in the SDK's contextvar — the
    real BearerAuthBackend would do this; we simulate it for tests."""
    access = AccessToken(
        token="t",
        client_id=session_key,
        scopes=["odoo.session"],
        expires_at=None,
    )
    user = AuthenticatedUser(access)
    return auth_context_var.set(user)


class TestHttpScopeWithAuth:
    @pytest.mark.asyncio
    async def test_pins_session_key_during_request(self) -> None:
        app = _CapturingApp()
        mw = SessionResolverMiddleware(app)
        ctx_token = _set_auth_user("5_db")
        try:
            await mw(_http_scope(), lambda: None, lambda *_: None)
        finally:
            auth_context_var.reset(ctx_token)

        assert app.captured == ["5_db"]

    @pytest.mark.asyncio
    async def test_resets_session_key_after_request(self) -> None:
        # Pin a "previous request" key so we can detect non-cleanup.
        set_current_session_key("prior_request")
        try:
            app = _CapturingApp()
            mw = SessionResolverMiddleware(app)
            ctx_token = _set_auth_user("5_db")
            try:
                await mw(_http_scope(), lambda: None, lambda *_: None)
            finally:
                auth_context_var.reset(ctx_token)

            # After the middleware exits the ContextVar must be back
            # to its prior value, not "5_db" and not None.
            assert get_current_session_key() == "prior_request"
        finally:
            set_current_session_key(None)

    @pytest.mark.asyncio
    async def test_resets_even_when_inner_raises(self) -> None:
        set_current_session_key("prior_request")
        try:
            app = _CapturingApp()
            app.raise_on_call = RuntimeError("simulated handler crash")
            mw = SessionResolverMiddleware(app)
            ctx_token = _set_auth_user("9_db")
            try:
                with pytest.raises(RuntimeError, match="simulated"):
                    await mw(_http_scope(), lambda: None, lambda *_: None)
            finally:
                auth_context_var.reset(ctx_token)

            # Despite the inner exception, the ContextVar was reset.
            assert get_current_session_key() == "prior_request"
        finally:
            set_current_session_key(None)


class TestHttpScopeNoAuth:
    @pytest.mark.asyncio
    async def test_no_auth_user_leaves_session_key_none(self) -> None:
        # No _set_auth_user call — the SDK's contextvar is at its
        # default (None).
        app = _CapturingApp()
        mw = SessionResolverMiddleware(app)
        await mw(_http_scope(), lambda: None, lambda *_: None)
        assert app.captured == [None]


class TestNonHttpScope:
    @pytest.mark.asyncio
    async def test_lifespan_scope_passes_through(self) -> None:
        app = _CapturingApp()
        mw = SessionResolverMiddleware(app)
        # Lifespan scopes don't have auth at all — the middleware must
        # not crash and not try to pin a session.
        await mw({"type": "lifespan"}, lambda: None, lambda *_: None)
        # The inner app was called (and captured) but with no pinned
        # session_key (it's whatever the surrounding context had).
        assert len(app.captured) == 1

    @pytest.mark.asyncio
    async def test_websocket_scope_passes_through(self) -> None:
        app = _CapturingApp()
        mw = SessionResolverMiddleware(app)
        await mw({"type": "websocket"}, lambda: None, lambda *_: None)
        assert len(app.captured) == 1


class TestConcurrentRequests:
    @pytest.mark.asyncio
    async def test_two_users_dont_cross(self) -> None:
        """ContextVar is task-local per PEP 567 — two concurrent
        requests from different users must each see their OWN session_key.
        """
        app = _CapturingApp()
        mw = SessionResolverMiddleware(app)

        async def _request_as(session_key: str, app: _CapturingApp) -> None:
            ctx_token = _set_auth_user(session_key)
            try:
                await mw(_http_scope(), lambda: None, lambda *_: None)
                # Add a yield point so two requests really interleave.
                await asyncio.sleep(0)
            finally:
                auth_context_var.reset(ctx_token)

        # Spawn two tasks; they must each observe their own user's key,
        # never each other's.
        app_a, app_b = _CapturingApp(), _CapturingApp()
        mw_a = SessionResolverMiddleware(app_a)
        mw_b = SessionResolverMiddleware(app_b)

        async def _run_a() -> None:
            ctx = _set_auth_user("user_a")
            try:
                await mw_a(_http_scope(), lambda: None, lambda *_: None)
            finally:
                auth_context_var.reset(ctx)

        async def _run_b() -> None:
            ctx = _set_auth_user("user_b")
            try:
                await mw_b(_http_scope(), lambda: None, lambda *_: None)
            finally:
                auth_context_var.reset(ctx)

        await asyncio.gather(_run_a(), _run_b())
        assert app_a.captured == ["user_a"]
        assert app_b.captured == ["user_b"]
