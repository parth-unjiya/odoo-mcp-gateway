"""Regression test for update_record empty-string-on-required guard (M-tools-13).

Previously ``update_record`` accepted ``field=""`` even when the field
was required; Odoo silently ignored the write and returned success.
That's the canonical silent-success failure mode. The fix runs the
required-non-empty check on BOTH create and update.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.tools.crud import _validate_writable_fields


class _FieldInfo:
    def __init__(self, required: bool = False, readonly: bool = False) -> None:
        self.required = required
        self.readonly = readonly
        self.field_type = "char"
        self.selection: list[tuple[str, str]] = []


@pytest.fixture
def gateway() -> MagicMock:
    gw = MagicMock()
    gw.field_inspector.get_fields = AsyncMock(
        return_value={
            "name": _FieldInfo(required=True),
            "comment": _FieldInfo(required=False),
        }
    )
    return gw


class TestEmptyRequiredAlwaysRejected:
    async def test_update_with_empty_required_rejected(
        self, gateway: MagicMock
    ) -> None:
        """The fix's headline: update with required='' is no longer silent success."""
        client = AsyncMock()
        err = await _validate_writable_fields(
            gateway,
            client,
            "res.partner",
            {"name": ""},
            check_required_non_empty=False,  # update flag (still rejected now)
        )
        assert err is not None
        assert "cannot be empty" in err

    async def test_update_with_whitespace_required_rejected(
        self, gateway: MagicMock
    ) -> None:
        client = AsyncMock()
        err = await _validate_writable_fields(
            gateway,
            client,
            "res.partner",
            {"name": "   \t"},
            check_required_non_empty=False,
        )
        assert err is not None

    async def test_update_clearing_optional_field_still_allowed(
        self, gateway: MagicMock
    ) -> None:
        """Critical: optional fields can still be cleared by empty string."""
        client = AsyncMock()
        err = await _validate_writable_fields(
            gateway,
            client,
            "res.partner",
            {"comment": ""},
            check_required_non_empty=False,
        )
        assert err is None

    async def test_create_with_empty_required_rejected(
        self, gateway: MagicMock
    ) -> None:
        client = AsyncMock()
        err = await _validate_writable_fields(
            gateway,
            client,
            "res.partner",
            {"name": ""},
            check_required_non_empty=True,  # create flag
        )
        assert err is not None

    async def test_valid_write_passes(self, gateway: MagicMock) -> None:
        client = AsyncMock()
        err = await _validate_writable_fields(
            gateway,
            client,
            "res.partner",
            {"name": "Acme", "comment": ""},
            check_required_non_empty=False,
        )
        assert err is None
