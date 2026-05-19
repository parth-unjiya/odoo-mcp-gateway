"""Tests for plugin helpers.

Specifically covers the session-isolation fail-safe behavior introduced
to prevent cross-user data contamination. Before this fix, ``get_uid()``,
``get_client()``, and ``get_auth_info()`` fell back to
``next(iter(context.auth_managers.values()))`` when the ContextVar wasn't
set — which silently returned the FIRST user (typically admin) regardless
of who was calling. The new behavior REFUSES to resolve when ambiguous,
so callers get "Not authenticated" rather than another user's data.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from odoo_mcp_gateway.plugins.core.helpers import (
    _resolve_auth_manager,
    get_auth_context,
    get_auth_info,
    get_client,
    get_uid,
)


class _FakeContext:
    """Minimal context shape that helpers expect."""

    def __init__(self) -> None:
        self.auth_managers: dict[str, Any] = {}


def _make_mgr(
    uid: int, is_admin: bool = False, groups: list[str] | None = None
) -> MagicMock:
    mgr = MagicMock()
    result = MagicMock()
    result.uid = uid
    result.is_admin = is_admin
    result.groups = groups or []
    mgr.auth_result = result
    client = MagicMock()
    mgr.get_active_client = MagicMock(return_value=client)
    return mgr


class TestResolveAuthManagerSingleSession:
    """Single-user-per-process is the expected state — resolution must work."""

    def test_single_session_resolves_without_contextvar(self) -> None:
        ctx = _FakeContext()
        mgr = _make_mgr(uid=42)
        ctx.auth_managers["42_db"] = mgr

        resolved = _resolve_auth_manager(ctx)
        assert resolved is mgr

    def test_no_sessions_returns_none(self) -> None:
        ctx = _FakeContext()
        assert _resolve_auth_manager(ctx) is None


class TestResolveAuthManagerMultipleSessionsRefused:
    """When >1 session exists AND no contextvar is set, REFUSE to guess."""

    def test_multi_session_without_contextvar_refuses(self) -> None:
        """The old bug: would return whichever session was first.
        New behavior: refuse and return None (caller treats as not-auth).
        """
        ctx = _FakeContext()
        ctx.auth_managers["2_db"] = _make_mgr(uid=2)
        ctx.auth_managers["5_db"] = _make_mgr(uid=5)

        resolved = _resolve_auth_manager(ctx)
        # Refusal is the secure choice
        assert resolved is None

    def test_multi_session_with_valid_contextvar_resolves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If contextvar IS set, use that — defense-in-depth for HTTP."""
        from odoo_mcp_gateway.plugins.core import helpers as helpers_mod

        ctx = _FakeContext()
        mgr_a = _make_mgr(uid=2)
        mgr_b = _make_mgr(uid=5)
        ctx.auth_managers["2_db"] = mgr_a
        ctx.auth_managers["5_db"] = mgr_b

        monkeypatch.setattr(helpers_mod, "get_current_session_key", lambda: "5_db")

        resolved = _resolve_auth_manager(ctx)
        assert resolved is mgr_b


class TestGetUidFailSafe:
    """uid resolution must REFUSE rather than guess."""

    def test_single_session_returns_uid(self) -> None:
        ctx = _FakeContext()
        ctx.auth_managers["7_db"] = _make_mgr(uid=7)

        assert get_uid(ctx) == 7

    def test_multi_session_without_key_returns_zero(self) -> None:
        """Old behavior: returned admin's uid (security bug).
        New behavior: returns 0 → caller treats as not-authenticated."""
        ctx = _FakeContext()
        ctx.auth_managers["2_db"] = _make_mgr(uid=2)
        ctx.auth_managers["5_db"] = _make_mgr(uid=5)

        assert get_uid(ctx) == 0

    def test_no_sessions_returns_zero(self) -> None:
        ctx = _FakeContext()
        assert get_uid(ctx) == 0


class TestGetClientFailSafe:
    def test_single_session_returns_client(self) -> None:
        ctx = _FakeContext()
        mgr = _make_mgr(uid=7)
        ctx.auth_managers["7_db"] = mgr

        client = get_client(ctx)
        assert client is mgr.get_active_client.return_value

    def test_multi_session_without_key_returns_none(self) -> None:
        ctx = _FakeContext()
        ctx.auth_managers["2_db"] = _make_mgr(uid=2)
        ctx.auth_managers["5_db"] = _make_mgr(uid=5)

        assert get_client(ctx) is None


class TestGetAuthInfoFailSafe:
    def test_single_session_returns_admin_and_groups(self) -> None:
        ctx = _FakeContext()
        ctx.auth_managers["2_db"] = _make_mgr(
            uid=2, is_admin=True, groups=["base.group_system"]
        )

        is_admin, groups = get_auth_info(ctx)
        assert is_admin is True
        assert groups == ["base.group_system"]

    def test_multi_session_without_key_returns_safe_default(self) -> None:
        ctx = _FakeContext()
        ctx.auth_managers["2_db"] = _make_mgr(uid=2, is_admin=True)
        ctx.auth_managers["5_db"] = _make_mgr(uid=5, is_admin=False)

        is_admin, groups = get_auth_info(ctx)
        assert is_admin is False
        assert groups == []


class TestGetAuthContextAtomic:
    """get_auth_context resolves client+uid+is_admin+groups atomically."""

    def test_single_session_returns_tuple(self) -> None:
        ctx = _FakeContext()
        mgr = _make_mgr(uid=7, is_admin=True, groups=["g1", "g2"])
        ctx.auth_managers["7_db"] = mgr

        result = get_auth_context(ctx)
        assert result is not None
        client, uid, is_admin, groups = result
        assert client is mgr.get_active_client.return_value
        assert uid == 7
        assert is_admin is True
        assert groups == ["g1", "g2"]

    def test_multi_session_without_key_returns_none(self) -> None:
        ctx = _FakeContext()
        ctx.auth_managers["2_db"] = _make_mgr(uid=2)
        ctx.auth_managers["5_db"] = _make_mgr(uid=5)

        assert get_auth_context(ctx) is None

    def test_no_sessions_returns_none(self) -> None:
        ctx = _FakeContext()
        assert get_auth_context(ctx) is None
