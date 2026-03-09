"""Tests for shared plugin helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from odoo_mcp_gateway.plugins.core.helpers import (
    get_auth_info,
    get_client,
    get_uid,
    next_month,
)


def _make_context(*, auth_mgr=None):
    ctx = MagicMock()
    ctx.auth_managers = {}
    if auth_mgr is not None:
        ctx.auth_managers["session"] = auth_mgr
    return ctx


def _make_auth_mgr(*, uid=1, is_admin=False, groups=None, client=None, raises=False):
    mgr = MagicMock()
    auth_result = MagicMock()
    auth_result.uid = uid
    auth_result.is_admin = is_admin
    auth_result.groups = groups or []
    mgr.auth_result = auth_result
    if raises:
        mgr.get_active_client.side_effect = RuntimeError("no client")
    else:
        mgr.get_active_client.return_value = client or AsyncMock()
    return mgr


class TestGetClient:
    def test_no_auth(self):
        ctx = _make_context()
        assert get_client(ctx) is None

    def test_with_client(self):
        mock_client = AsyncMock()
        ctx = _make_context(auth_mgr=_make_auth_mgr(client=mock_client))
        assert get_client(ctx) is mock_client

    def test_client_raises(self):
        ctx = _make_context(auth_mgr=_make_auth_mgr(raises=True))
        assert get_client(ctx) is None


class TestGetUid:
    def test_no_auth(self):
        ctx = _make_context()
        assert get_uid(ctx) == 0

    def test_with_uid(self):
        ctx = _make_context(auth_mgr=_make_auth_mgr(uid=42))
        assert get_uid(ctx) == 42


class TestGetAuthInfo:
    def test_no_auth(self):
        ctx = _make_context()
        assert get_auth_info(ctx) == (False, [])

    def test_admin_with_groups(self):
        ctx = _make_context(
            auth_mgr=_make_auth_mgr(is_admin=True, groups=["hr_manager"])
        )
        assert get_auth_info(ctx) == (True, ["hr_manager"])

    def test_non_admin(self):
        ctx = _make_context(auth_mgr=_make_auth_mgr(is_admin=False))
        assert get_auth_info(ctx) == (False, [])


class TestNextMonth:
    def test_regular_month(self):
        assert next_month("2024-03") == "2024-04-01 00:00:00"

    def test_december_wraps(self):
        assert next_month("2024-12") == "2025-01-01 00:00:00"

    def test_january(self):
        assert next_month("2024-01") == "2024-02-01 00:00:00"
