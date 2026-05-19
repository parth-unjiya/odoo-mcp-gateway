"""Tests for the get_valid_states plugin helper.

The helper introduces a live-schema fallback for plugin state whitelists
that previously drifted between Odoo versions (A12 from the v0.2.0 pass-2
audit). It probes ``fields_get`` and returns the live selection values,
or ``None`` when the probe is unusable so the caller can fall back to a
static set.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from odoo_mcp_gateway.plugins.core.helpers import get_valid_states


class TestGetValidStates:
    async def test_returns_live_selection_set(self) -> None:
        client = AsyncMock()
        client.execute_kw.return_value = {
            "state": {
                "selection": [
                    ["draft", "Draft"],
                    ["sent", "Sent"],
                    ["sale", "Sales Order"],
                ]
            }
        }

        result = await get_valid_states(client, "sale.order")

        assert result == {"draft", "sent", "sale"}
        # The probe is field-targeted (only fetches selection metadata
        # for one field) so it's safe to call frequently.
        call_args = client.execute_kw.call_args[0]
        assert call_args[0] == "sale.order"
        assert call_args[1] == "fields_get"
        # The call passes the state field name in args, with the
        # selection attribute requested via kwargs.
        assert call_args[2] == [["state"]]

    async def test_custom_state_field_name(self) -> None:
        client = AsyncMock()
        client.execute_kw.return_value = {
            "stage": {"selection": [["open", "Open"], ["closed", "Closed"]]}
        }

        result = await get_valid_states(client, "custom.model", state_field="stage")

        assert result == {"open", "closed"}

    async def test_returns_none_when_field_missing(self) -> None:
        client = AsyncMock()
        client.execute_kw.return_value = {}  # field absent

        result = await get_valid_states(client, "sale.order")

        assert result is None

    async def test_returns_none_when_not_selection(self) -> None:
        """Char fields don't have selection metadata."""
        client = AsyncMock()
        client.execute_kw.return_value = {"state": {"type": "char"}}

        result = await get_valid_states(client, "sale.order")

        assert result is None

    async def test_returns_none_on_client_error(self) -> None:
        """Network or ACL failures must not crash the caller — we return
        ``None`` so the caller can fall back to its static set.
        """
        client = AsyncMock()
        client.execute_kw.side_effect = RuntimeError("offline")

        result = await get_valid_states(client, "sale.order")

        assert result is None

    async def test_handles_empty_selection_list(self) -> None:
        client = AsyncMock()
        client.execute_kw.return_value = {"state": {"selection": []}}

        result = await get_valid_states(client, "sale.order")

        # Empty selection is indistinguishable from "no selection
        # metadata" — return None so the caller falls back.
        assert result is None

    async def test_skips_non_string_values(self) -> None:
        """A malformed selection (e.g. integer keys) is skipped without
        crashing. Strings only.
        """
        client = AsyncMock()
        client.execute_kw.return_value = {
            "state": {
                "selection": [
                    ["draft", "Draft"],
                    [1, "Numeric Key"],  # bogus
                    ["confirmed", "Confirmed"],
                ]
            }
        }

        result = await get_valid_states(client, "sale.order")

        assert result == {"draft", "confirmed"}
