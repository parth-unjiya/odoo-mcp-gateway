"""Regression test for Credential constant-time equality (audit L-1).

``Credential.__eq__`` now uses ``hmac.compare_digest`` so a future
call site comparing secrets via ``==`` does not leak length or prefix
through early-exit timing.
"""

from __future__ import annotations

from odoo_mcp_gateway.client.base import Credential


class TestCredentialEquality:
    def test_equal_credentials_compare_true(self) -> None:
        assert Credential("hunter2") == Credential("hunter2")

    def test_distinct_credentials_compare_false(self) -> None:
        assert Credential("hunter2") != Credential("letmein")

    def test_none_compares_with_none(self) -> None:
        # Both empty strings compare equal under compare_digest.
        assert Credential(None) == Credential("")

    def test_eq_against_non_credential_returns_notimplemented(self) -> None:
        # Equality with a raw string must NOT silently compare —
        # Python returns False because __eq__ returns NotImplemented
        # and the right-hand side has no recognising __eq__.
        assert (Credential("x") == "x") is False
