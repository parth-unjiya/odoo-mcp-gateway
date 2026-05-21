"""Tests for the MCP completion handler (ADR-009 Sprint 4)."""

from __future__ import annotations

import pytest
from mcp.types import (
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.discovery.completions import (
    MAX_COMPLETIONS,
    build_completion_handler,
)
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
        model_access=ModelAccessConfig(
            stock_models={
                "full_crud": [
                    "res.partner",
                    "sale.order",
                    "sale.order.line",
                    "purchase.order",
                ],
                "read_only": ["res.company", "res.currency"],
            },
            allowed_methods={
                "sale.order": ["action_confirm", "action_cancel"],
                "purchase.order": ["button_confirm", "button_cancel"],
            },
        ),
    )
    return GatewayContext(settings, cfg)


def _arg(name: str, value: str) -> CompletionArgument:
    return CompletionArgument(name=name, value=value)


def _prompt_ref(name: str = "analyze_model") -> PromptReference:
    return PromptReference(type="ref/prompt", name=name)


def _resource_ref(uri: str = "odoo://models/{model_name}") -> ResourceTemplateReference:
    return ResourceTemplateReference(type="ref/resource", uri=uri)


class TestModelCompletions:
    @pytest.mark.asyncio
    async def test_model_arg_completes_from_yaml(self) -> None:
        gw = _make_gateway()
        handler = build_completion_handler(gw)
        result = await handler(_prompt_ref(), _arg("model", "sale"), None)
        assert result is not None
        # Prefix-match should rank sale.order BEFORE sale.order.line
        assert result.values[0] == "sale.order"
        assert "sale.order.line" in result.values
        # Models not starting with "sale" are excluded (no substring
        # match when there's a clearer prefix to surface first).
        assert "res.partner" not in result.values

    @pytest.mark.asyncio
    async def test_model_arg_with_empty_prefix_returns_all(self) -> None:
        gw = _make_gateway()
        handler = build_completion_handler(gw)
        result = await handler(_prompt_ref(), _arg("model", ""), None)
        assert result is not None
        # All registered models pass the empty-prefix filter.
        assert "res.partner" in result.values
        assert "sale.order" in result.values

    @pytest.mark.asyncio
    async def test_capped_at_max_completions(self) -> None:
        """Even with many candidates, MCP wire payload stays small."""
        gw = _make_gateway()
        # Pad the allowlist with synthetic models to exceed the cap.
        gw.gateway_config.model_access.stock_models["full_crud"] = [
            f"my.model{i}" for i in range(MAX_COMPLETIONS + 20)
        ]
        # Rebuild the model_registry so it sees the updated config.
        gw.model_registry._models = {}  # type: ignore[attr-defined]
        handler = build_completion_handler(gw)
        result = await handler(_prompt_ref(), _arg("model", "my"), None)
        assert result is not None
        assert len(result.values) <= MAX_COMPLETIONS

    @pytest.mark.asyncio
    async def test_blocked_model_filtered_out(self) -> None:
        """Models that fail restriction check must not appear."""
        gw = _make_gateway()
        # Add res.users to the candidate list — it's hardcoded-blocked.
        gw.gateway_config.model_access.stock_models["full_crud"] = [
            "res.partner",
            "res.users",
        ]
        gw.model_registry._models = {}  # type: ignore[attr-defined]
        handler = build_completion_handler(gw)
        result = await handler(_prompt_ref(), _arg("model", "res"), None)
        assert result is not None
        assert "res.users" not in result.values
        assert "res.partner" in result.values


class TestMethodCompletions:
    @pytest.mark.asyncio
    async def test_method_with_model_context(self) -> None:
        gw = _make_gateway()
        handler = build_completion_handler(gw)
        ctx = CompletionContext(arguments={"model": "sale.order"})
        result = await handler(_prompt_ref(), _arg("method", "action"), ctx)
        assert result is not None
        assert "action_confirm" in result.values
        assert "action_cancel" in result.values
        # purchase.order methods must NOT leak when context is sale.order.
        assert "button_confirm" not in result.values

    @pytest.mark.asyncio
    async def test_method_without_context_unions_all_models(self) -> None:
        gw = _make_gateway()
        handler = build_completion_handler(gw)
        result = await handler(_prompt_ref(), _arg("method", ""), None)
        assert result is not None
        # Union of methods across every model in the YAML.
        assert "action_confirm" in result.values
        assert "button_confirm" in result.values


class TestRecordIdCompletions:
    @pytest.mark.asyncio
    async def test_record_id_returns_syntax_hint(self) -> None:
        gw = _make_gateway()
        handler = build_completion_handler(gw)
        result = await handler(_prompt_ref(), _arg("record_id", "1"), None)
        assert result is not None
        # We don't enumerate live IDs (that'd require an Odoo round-trip
        # on every keystroke); a single placeholder is enough.
        assert len(result.values) == 1


class TestUnknownArgument:
    @pytest.mark.asyncio
    async def test_unknown_arg_returns_none(self) -> None:
        """Spec says servers MAY return no completions; we return None
        so the client falls back to free-text input."""
        gw = _make_gateway()
        handler = build_completion_handler(gw)
        result = await handler(_prompt_ref(), _arg("foobar", "x"), None)
        assert result is None


class TestResourceTemplateRef:
    @pytest.mark.asyncio
    async def test_resource_template_model_name(self) -> None:
        gw = _make_gateway()
        handler = build_completion_handler(gw)
        result = await handler(
            _resource_ref("odoo://schema/{model_name}"),
            _arg("model_name", "sale"),
            None,
        )
        assert result is not None
        assert "sale.order" in result.values


class TestHandlerRobustness:
    @pytest.mark.asyncio
    async def test_handler_returns_none_on_internal_error(self) -> None:
        """Typeahead must NEVER crash the client — exceptions become None."""
        gw = _make_gateway()
        # Force the model_registry into a state that raises.
        gw.model_registry = None  # type: ignore[assignment]
        handler = build_completion_handler(gw)
        result = await handler(_prompt_ref(), _arg("model", "sale"), None)
        assert result is None


class TestSessionScopedAdminResolution:
    """v0.3.3 MED-2 — admin status comes from the active session, not arbitrary."""

    @pytest.mark.asyncio
    async def test_admin_session_sees_admin_filtered_models(self) -> None:
        """ContextVar pinned to an admin session -> is_admin=True path."""
        from unittest.mock import MagicMock

        from odoo_mcp_gateway.server import set_current_session_key

        gw = _make_gateway()

        # Register two auth managers: an admin and a portal user.
        admin_mgr = MagicMock()
        admin_mgr.auth_result = MagicMock(is_admin=True)
        portal_mgr = MagicMock()
        portal_mgr.auth_result = MagicMock(is_admin=False)
        gw.auth_managers = {
            "admin_session": admin_mgr,
            "portal_session": portal_mgr,
        }

        handler = build_completion_handler(gw)

        # Pin the ContextVar to the admin session.
        set_current_session_key("admin_session")
        try:
            result = await handler(_prompt_ref(), _arg("model", "sale"), None)
        finally:
            set_current_session_key(None)

        assert result is not None
        # Models the admin can read still come through.
        assert "sale.order" in result.values

    @pytest.mark.asyncio
    async def test_portal_session_falls_back_to_non_admin(self) -> None:
        """ContextVar pinned to a portal session -> is_admin=False."""
        from unittest.mock import MagicMock

        from odoo_mcp_gateway.server import set_current_session_key

        gw = _make_gateway()
        admin_mgr = MagicMock()
        admin_mgr.auth_result = MagicMock(is_admin=True)
        portal_mgr = MagicMock()
        portal_mgr.auth_result = MagicMock(is_admin=False)
        gw.auth_managers = {
            "admin_session": admin_mgr,
            "portal_session": portal_mgr,
        }

        # Inject a spy into restrictions.check_model_access to verify
        # the is_admin flag passed in.  We replace it with a lambda
        # that records each call.
        seen_is_admin: list[bool] = []
        original_check = gw.restrictions.check_model_access

        def spy(model: str, op: str, is_admin: bool):  # noqa: ANN001
            seen_is_admin.append(is_admin)
            return original_check(model, op, is_admin)

        gw.restrictions.check_model_access = spy  # type: ignore[assignment]

        handler = build_completion_handler(gw)

        set_current_session_key("portal_session")
        try:
            await handler(_prompt_ref(), _arg("model", "sale"), None)
        finally:
            set_current_session_key(None)

        # The handler must have called the restriction check with the
        # portal session's is_admin=False, never the admin's True.
        assert seen_is_admin, "restriction check was never invoked"
        assert all(flag is False for flag in seen_is_admin)

    @pytest.mark.asyncio
    async def test_no_session_key_defaults_to_non_admin(self) -> None:
        """No ContextVar bound -> fail closed at is_admin=False."""
        from unittest.mock import MagicMock

        from odoo_mcp_gateway.server import set_current_session_key

        gw = _make_gateway()
        # Even if an admin session exists in the dict, no ContextVar
        # means we must not pick it.
        admin_mgr = MagicMock()
        admin_mgr.auth_result = MagicMock(is_admin=True)
        gw.auth_managers = {"admin_session": admin_mgr}

        seen_is_admin: list[bool] = []
        original_check = gw.restrictions.check_model_access

        def spy(model: str, op: str, is_admin: bool):  # noqa: ANN001
            seen_is_admin.append(is_admin)
            return original_check(model, op, is_admin)

        gw.restrictions.check_model_access = spy  # type: ignore[assignment]

        handler = build_completion_handler(gw)
        # Clear any leftover ContextVar.
        set_current_session_key(None)
        await handler(_prompt_ref(), _arg("model", "sale"), None)

        assert seen_is_admin
        # Fail-closed: no session key -> is_admin must be False even
        # though the dict's only entry is an admin.
        assert all(flag is False for flag in seen_is_admin)

    @pytest.mark.asyncio
    async def test_unknown_session_key_defaults_to_non_admin(self) -> None:
        """ContextVar bound to an unknown session -> fail closed."""
        from unittest.mock import MagicMock

        from odoo_mcp_gateway.server import set_current_session_key

        gw = _make_gateway()
        admin_mgr = MagicMock()
        admin_mgr.auth_result = MagicMock(is_admin=True)
        gw.auth_managers = {"admin_session": admin_mgr}

        seen_is_admin: list[bool] = []
        original_check = gw.restrictions.check_model_access

        def spy(model: str, op: str, is_admin: bool):  # noqa: ANN001
            seen_is_admin.append(is_admin)
            return original_check(model, op, is_admin)

        gw.restrictions.check_model_access = spy  # type: ignore[assignment]

        handler = build_completion_handler(gw)
        set_current_session_key("nonexistent_session")
        try:
            await handler(_prompt_ref(), _arg("model", "sale"), None)
        finally:
            set_current_session_key(None)

        assert seen_is_admin
        assert all(flag is False for flag in seen_is_admin)
