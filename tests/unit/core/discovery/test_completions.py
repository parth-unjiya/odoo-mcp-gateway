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
