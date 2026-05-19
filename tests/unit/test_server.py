"""Tests for server.py session isolation and helper functions."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.core.auth.manager import AuthManager
from odoo_mcp_gateway.server import (
    GatewayContext,
    _get_auth_manager,
    _get_client,
    get_current_session_key,
    set_current_session_key,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _auth_result(**overrides: Any) -> AuthResult:
    defaults: dict[str, Any] = {
        "uid": 1,
        "session_id": "s1",
        "user_context": {"lang": "en_US"},
        "is_admin": False,
        "groups": [],
        "username": "admin",
        "database": "testdb",
    }
    defaults.update(overrides)
    return AuthResult(**defaults)


def _mock_auth_manager(uid: int = 1) -> AuthManager:
    """Build a mock AuthManager that returns a stub client."""
    mgr = MagicMock(spec=AuthManager)
    client = MagicMock()
    mgr.get_active_client.return_value = client
    mgr.auth_result = _auth_result(uid=uid)
    return mgr


def _mock_gateway(*session_keys: str) -> MagicMock:
    """Build a mock GatewayContext with auth_managers for each session key."""
    gateway = MagicMock(spec=GatewayContext)
    gateway.auth_managers = {}
    for key in session_keys:
        gateway.auth_managers[key] = _mock_auth_manager(uid=hash(key) % 1000)
    return gateway


# ------------------------------------------------------------------
# Session key context variable
# ------------------------------------------------------------------


class TestSessionKeyContextVar:
    def test_default_is_none(self) -> None:
        set_current_session_key(None)
        assert get_current_session_key() is None

    def test_set_and_get(self) -> None:
        set_current_session_key("my_session")
        assert get_current_session_key() == "my_session"
        set_current_session_key(None)

    def test_reset(self) -> None:
        set_current_session_key("test")
        set_current_session_key(None)
        assert get_current_session_key() is None


# ------------------------------------------------------------------
# _get_client session isolation
# ------------------------------------------------------------------


class TestGetClientSessionIsolation:
    def test_uses_session_key_when_set(self) -> None:
        gw = _mock_gateway("session_a", "session_b")
        set_current_session_key("session_b")
        try:
            _get_client(gw)
            gw.auth_managers["session_b"].get_active_client.assert_called_once()
        finally:
            set_current_session_key(None)

    def test_falls_back_to_first_when_no_session_key(self) -> None:
        gw = _mock_gateway("session_a")
        set_current_session_key(None)
        _get_client(gw)
        gw.auth_managers["session_a"].get_active_client.assert_called_once()

    def test_refuses_when_session_key_not_in_managers(self) -> None:
        """v0.2.2 hardening: stale contextvar key must NOT fall through.

        Previously the resolver fell back to the only remaining session,
        which silently rebound a stale request to a different user. With
        the strict resolution, a contextvar key not present in
        ``auth_managers`` raises rather than picking a residual session.
        """
        gw = _mock_gateway("session_a")
        set_current_session_key("nonexistent")
        try:
            with pytest.raises(ValueError, match="no longer active"):
                _get_client(gw)
        finally:
            set_current_session_key(None)

    def test_raises_when_no_auth_managers(self) -> None:
        gw = _mock_gateway()
        with pytest.raises(ValueError, match="Not authenticated"):
            _get_client(gw)


# ------------------------------------------------------------------
# _get_auth_manager session isolation
# ------------------------------------------------------------------


class TestGetAuthManagerSessionIsolation:
    def test_uses_session_key_when_set(self) -> None:
        gw = _mock_gateway("session_x", "session_y")
        set_current_session_key("session_x")
        try:
            mgr = _get_auth_manager(gw)
            assert mgr is gw.auth_managers["session_x"]
        finally:
            set_current_session_key(None)

    def test_falls_back_to_first_when_no_session_key(self) -> None:
        gw = _mock_gateway("only_session")
        set_current_session_key(None)
        mgr = _get_auth_manager(gw)
        assert mgr is gw.auth_managers["only_session"]

    def test_refuses_when_session_key_not_in_managers(self) -> None:
        """v0.2.2 hardening: same strict-resolution contract as _get_client."""
        gw = _mock_gateway("real_session")
        set_current_session_key("ghost_session")
        try:
            with pytest.raises(ValueError, match="no longer active"):
                _get_auth_manager(gw)
        finally:
            set_current_session_key(None)

    def test_raises_when_no_auth_managers(self) -> None:
        gw = _mock_gateway()
        with pytest.raises(ValueError, match="Not authenticated"):
            _get_auth_manager(gw)
