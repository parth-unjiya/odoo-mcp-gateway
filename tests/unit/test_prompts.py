"""Tests for MCP Prompt handlers and prompt library."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from odoo_mcp_gateway.prompts.handlers import register_prompts

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _register_and_get_prompts() -> dict[str, Any]:
    """Register prompts and return a dict of prompt handler callables.

    Keys are the function names used with ``@server.prompt()``.
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="test")
    ctx = MagicMock()
    captured: dict[str, Any] = {}
    original_prompt = server.prompt

    def capturing_prompt(**kwargs: Any):  # type: ignore[no-untyped-def]
        decorator = original_prompt(**kwargs)

        def wrapper(fn: Any) -> Any:
            result = decorator(fn)
            captured[fn.__name__] = fn
            return result

        return wrapper

    server.prompt = capturing_prompt  # type: ignore[assignment]
    register_prompts(server, lambda: ctx)
    return captured


# ------------------------------------------------------------------
# Prompt handler tests
# ------------------------------------------------------------------


async def test_analyze_model_prompt_includes_model_name() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["analyze_model"](model="sale.order")
    assert "sale.order" in result
    assert "get_model_fields" in result
    assert "Required fields" in result


async def test_explore_data_prompt_includes_question() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["explore_data"](
        model="res.partner", question="How many active partners?"
    )
    assert "res.partner" in result
    assert "How many active partners?" in result
    assert "search_read" in result


async def test_explore_data_prompt_default_question() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["explore_data"](model="res.partner")
    assert "What data is available?" in result


async def test_create_workflow_prompt_includes_action() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["create_workflow"](
        model="sale.order", action="confirm and validate"
    )
    assert "sale.order" in result
    assert "confirm and validate" in result
    assert "state/stage field" in result


async def test_compare_records_prompt_with_ids() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["compare_records"](model="sale.order", record_ids="1,2,3")
    assert "sale.order" in result
    assert "1,2,3" in result
    assert "get_record for each ID" in result


async def test_compare_records_prompt_without_ids() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["compare_records"](model="sale.order")
    assert "sale.order" in result
    assert "search_read to find records" in result


async def test_generate_report_prompt_includes_period() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["generate_report"](
        model="account.move", period="Q1 2025", focus="financial"
    )
    assert "account.move" in result
    assert "Q1 2025" in result
    assert "financial" in result
    assert "read_group" in result


async def test_generate_report_prompt_defaults() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["generate_report"](model="sale.order")
    assert "this month" in result
    assert "overview" in result


async def test_discover_custom_modules_prompt() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["discover_custom_modules"]()
    assert "custom" in result.lower()
    assert "list_models" in result
    assert "model_access.yaml" in result


async def test_debug_access_prompt_with_model() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["debug_access"](
        model="ir.config_parameter", operation="write"
    )
    assert "ir.config_parameter" in result
    assert "write" in result
    assert "restrictions.yaml" in result


async def test_debug_access_prompt_without_model() -> None:
    handlers = _register_and_get_prompts()
    result = await handlers["debug_access"]()
    assert "the problematic model" in result
    assert "read" in result
