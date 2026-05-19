"""Regression tests for _resolve_auth_manager stale-key refusal (H3).

Before v0.2.2-final, if the contextvar was SET but pointed to a session
that had been evicted (e.g., re-login replaced it), the resolver fell
back to "the only remaining session" — which usually belonged to a
different user. The fix refuses to fall through when the contextvar is
set, so a stale key returns None (caller refuses) instead of silently
rebinding to someone else's session.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from odoo_mcp_gateway.plugins.core.helpers import (
    _resolve_auth_manager,
    check_security_gate,
    get_auth_context,
)
from odoo_mcp_gateway.server import set_current_session_key


def _ctx_with_managers(*keys: str) -> SimpleNamespace:
    mgrs = {}
    for k in keys:
        mgr = MagicMock()
        mgr.get_active_client = MagicMock(return_value=MagicMock())
        mgr.auth_result = MagicMock(uid=int(k.split("_")[0]), is_admin=False, groups=[])
        mgrs[k] = mgr
    return SimpleNamespace(auth_managers=mgrs)


class TestStaleKeyRefuses:
    def teardown_method(self) -> None:
        set_current_session_key(None)

    def test_stale_key_returns_none(self) -> None:
        """ContextVar set but stale → refuse rather than fall back."""
        ctx = _ctx_with_managers("5_testdb")
        set_current_session_key("99_ghost")  # key not in auth_managers
        assert _resolve_auth_manager(ctx) is None

    def test_stale_key_does_not_pick_remaining_session(self) -> None:
        """Critical: even with exactly ONE remaining session, stale key refuses."""
        ctx = _ctx_with_managers("5_testdb")
        set_current_session_key("2_oldsession")
        # Pre-fix behaviour would have returned the uid=5 manager — wrong user.
        assert _resolve_auth_manager(ctx) is None

    def test_no_contextvar_uses_single_session(self) -> None:
        """The single-session fallback only fires when contextvar is None."""
        ctx = _ctx_with_managers("5_testdb")
        set_current_session_key(None)
        mgr = _resolve_auth_manager(ctx)
        assert mgr is ctx.auth_managers["5_testdb"]

    def test_valid_key_resolves(self) -> None:
        ctx = _ctx_with_managers("5_testdb", "7_other")
        set_current_session_key("5_testdb")
        mgr = _resolve_auth_manager(ctx)
        assert mgr is ctx.auth_managers["5_testdb"]

    def test_get_auth_context_refuses_on_stale_key(self) -> None:
        ctx = _ctx_with_managers("5_testdb")
        set_current_session_key("ghost")
        assert get_auth_context(ctx) is None


class TestCheckSecurityGateStaleKey:
    def teardown_method(self) -> None:
        set_current_session_key(None)

    async def test_stale_key_does_not_rebind_to_other_session(self) -> None:
        """check_security_gate must not silently re-attribute a stale request.

        We patch ``security_gate`` so we can see the session_key the
        helper actually passes through. The expected outcome is the
        ``"default"`` bucket — NOT the surviving session's key.
        """
        from unittest.mock import patch as _patch

        ctx = MagicMock()
        ctx.auth_managers = {
            "5_testdb": MagicMock(
                auth_result=MagicMock(uid=5, is_admin=False, groups=[], username="bob")
            )
        }
        set_current_session_key("99_stale")

        with _patch(
            "odoo_mcp_gateway.plugins.core.helpers.security_gate",
            return_value=None,
        ) as mock_gate:
            await check_security_gate(ctx, "search_read")

        passed_session_key = mock_gate.await_args.args[2]
        assert passed_session_key == "default", (
            "stale contextvar key must fall through to 'default', not "
            "silently rebind to the residual session"
        )
