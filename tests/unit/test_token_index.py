"""Tests for GatewayContext bearer-token index (Sprint 1, S1.1).

The token_index maps opaque bearer tokens to session_keys. It is the
in-process state that ties an HTTP request's ``Authorization: Bearer``
header to one of ``gateway.auth_managers``. The contract these tests
enforce:

* ``issue_bearer_token`` returns a fresh 32-byte URL-safe string.
* Tokens are unique across invocations (no collisions in a single
  process lifetime — birthday-bounded by 256 bits).
* Re-issuing for the same session_key REVOKES the prior token (rotation).
* ``revoke_session_tokens`` clears every token bound to a session.
* ``resolve_token`` is a pure read.
* ``cleanup()`` clears the index alongside auth_managers.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext


def _make_gateway() -> GatewayContext:
    settings = Settings(
        odoo_url="http://localhost:8069",
        odoo_db="test",
        odoo_username="admin",
        odoo_api_key=SecretStr(""),
    )
    cfg = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(settings, cfg)


class TestTokenIssuance:
    def test_issue_returns_urlsafe_string(self) -> None:
        gw = _make_gateway()
        token = gw.issue_bearer_token("2_test")
        assert isinstance(token, str)
        # secrets.token_urlsafe(32) produces ~43 chars of URL-safe base64.
        assert 40 < len(token) < 50
        assert all(c.isalnum() or c in ("-", "_") for c in token)

    def test_each_issuance_unique(self) -> None:
        gw = _make_gateway()
        seen: set[str] = set()
        for i in range(100):
            seen.add(gw.issue_bearer_token(f"{i}_test"))
        assert len(seen) == 100, "every issued token must be unique"

    def test_resolve_returns_session_key(self) -> None:
        gw = _make_gateway()
        token = gw.issue_bearer_token("5_db")
        assert gw.resolve_token(token) == "5_db"

    def test_resolve_unknown_returns_none(self) -> None:
        gw = _make_gateway()
        assert gw.resolve_token("not-a-real-token") is None

    def test_resolve_empty_returns_none(self) -> None:
        gw = _make_gateway()
        assert gw.resolve_token("") is None


class TestTokenRotation:
    def test_reissue_for_same_session_revokes_old(self) -> None:
        gw = _make_gateway()
        old = gw.issue_bearer_token("2_db")
        new = gw.issue_bearer_token("2_db")
        assert old != new
        # Old token is no longer resolvable.
        assert gw.resolve_token(old) is None
        # New token works.
        assert gw.resolve_token(new) == "2_db"
        # Index has exactly one entry for this session.
        assert sum(1 for sk in gw.token_index.values() if sk == "2_db") == 1

    def test_distinct_sessions_keep_distinct_tokens(self) -> None:
        gw = _make_gateway()
        t1 = gw.issue_bearer_token("2_db")
        t2 = gw.issue_bearer_token("5_db")
        # Both tokens still valid; rotation only happens for SAME session_key.
        assert gw.resolve_token(t1) == "2_db"
        assert gw.resolve_token(t2) == "5_db"


class TestTokenRevocation:
    def test_revoke_token_returns_true_when_present(self) -> None:
        gw = _make_gateway()
        token = gw.issue_bearer_token("2_db")
        assert gw.revoke_token(token) is True
        assert gw.resolve_token(token) is None

    def test_revoke_token_returns_false_when_absent(self) -> None:
        gw = _make_gateway()
        assert gw.revoke_token("nonexistent") is False

    def test_revoke_session_tokens_clears_all_for_session(self) -> None:
        gw = _make_gateway()
        # Pretend a session had multiple historical tokens that hadn't
        # been rotated out yet (simulated by direct index mutation).
        t1 = gw.issue_bearer_token("2_db")
        gw.token_index["older-leftover-token"] = "2_db"
        gw.token_index["another-leftover"] = "2_db"
        # Sanity: 3 entries for 2_db now (t1 + 2 leftovers).
        assert sum(1 for sk in gw.token_index.values() if sk == "2_db") == 3
        # Issue another to verify the OTHER session isn't disturbed.
        t_other = gw.issue_bearer_token("9_db")

        revoked = gw.revoke_session_tokens("2_db")
        assert revoked == 3
        # All 2_db tokens gone, including the originally-issued one.
        assert gw.resolve_token(t1) is None
        assert gw.resolve_token("older-leftover-token") is None
        # Other session untouched.
        assert gw.resolve_token(t_other) == "9_db"


class TestCleanupClearsTokens:
    @pytest.mark.asyncio
    async def test_cleanup_clears_token_index(self) -> None:
        gw = _make_gateway()
        gw.issue_bearer_token("2_db")
        gw.issue_bearer_token("5_db")
        assert len(gw.token_index) == 2
        await gw.cleanup()
        # auth_managers AND token_index both wiped.
        assert gw.auth_managers == {}
        assert gw.token_index == {}
