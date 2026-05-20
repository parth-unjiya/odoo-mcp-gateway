"""Tests for the resource SubscriptionTracker (Sprint 5)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.observability.subscriptions import (
    SubscriptionTracker,
    notify_resource_changed,
)
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext


def _gateway() -> GatewayContext:
    settings = Settings(
        odoo_url="http://localhost:8069",
        odoo_db="test",
        odoo_username="",
        odoo_api_key=SecretStr(""),
    )
    cfg = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(settings, cfg)


class TestSubscribeUnsubscribe:
    def test_subscribe_then_unsubscribe(self) -> None:
        tracker = SubscriptionTracker()
        tracker.subscribe("5_db", "odoo://record/sale.order/42")
        assert tracker.subscribers("odoo://record/sale.order/42") == ["5_db"]
        assert tracker.unsubscribe("5_db", "odoo://record/sale.order/42") is True
        assert tracker.subscribers("odoo://record/sale.order/42") == []

    def test_subscribe_is_idempotent(self) -> None:
        tracker = SubscriptionTracker()
        tracker.subscribe("5_db", "odoo://x/1")
        tracker.subscribe("5_db", "odoo://x/1")
        # Two subscribes from the same session collapse to one.
        assert len(tracker.subscribers("odoo://x/1")) == 1

    def test_unsubscribe_unknown_returns_false(self) -> None:
        """Spec: unsubscribing from unknown URI is a no-op, NOT an error."""
        tracker = SubscriptionTracker()
        assert tracker.unsubscribe("5_db", "odoo://x/1") is False

    def test_two_sessions_independent(self) -> None:
        tracker = SubscriptionTracker()
        tracker.subscribe("5_db", "odoo://x/1")
        tracker.subscribe("7_db", "odoo://x/1")
        assert sorted(tracker.subscribers("odoo://x/1")) == ["5_db", "7_db"]
        tracker.unsubscribe("5_db", "odoo://x/1")
        # 7_db still subscribed.
        assert tracker.subscribers("odoo://x/1") == ["7_db"]


class TestClearSession:
    def test_clear_session_drops_all_subs(self) -> None:
        tracker = SubscriptionTracker()
        tracker.subscribe("5_db", "odoo://x/1")
        tracker.subscribe("5_db", "odoo://x/2")
        tracker.subscribe("7_db", "odoo://x/1")
        cleared = tracker.clear_session("5_db")
        assert cleared == 2
        assert tracker.all_uris_for_session("5_db") == []
        # Other session untouched.
        assert tracker.all_uris_for_session("7_db") == ["odoo://x/1"]

    def test_clear_unknown_session_returns_zero(self) -> None:
        tracker = SubscriptionTracker()
        assert tracker.clear_session("ghost") == 0


class TestNotifyResourceChanged:
    @pytest.mark.asyncio
    async def test_returns_count_of_subscribers(self) -> None:
        gw = _gateway()
        gw.subscriptions.subscribe("5_db", "odoo://record/sale.order/42")
        gw.subscriptions.subscribe("7_db", "odoo://record/sale.order/42")
        gw.subscriptions.subscribe("9_db", "odoo://record/sale.order/99")
        count = await notify_resource_changed(gw, "odoo://record/sale.order/42")
        assert count == 2

    @pytest.mark.asyncio
    async def test_no_subscribers_returns_zero(self) -> None:
        gw = _gateway()
        count = await notify_resource_changed(gw, "odoo://record/sale.order/9999")
        assert count == 0


class TestGatewayMount:
    def test_gateway_has_subscriptions_attr(self) -> None:
        gw = _gateway()
        assert isinstance(gw.subscriptions, SubscriptionTracker)
        assert len(gw.subscriptions) == 0
