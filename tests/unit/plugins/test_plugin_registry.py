"""Tests for the plugin registry (discovery, validation, activation)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from odoo_mcp_gateway.plugins.base import OdooPlugin
from odoo_mcp_gateway.plugins.registry import (
    ENTRY_POINT_GROUP,
    PluginRegistry,
)

_EP_PATH = "odoo_mcp_gateway.plugins.registry.importlib.metadata.entry_points"


# ── Helpers ───────────────────────────────────────────────────────


class SamplePlugin(OdooPlugin):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "A sample plugin"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["sale"]

    def register(self, server: Any, context: Any) -> None:
        pass


class AnotherPlugin(OdooPlugin):
    @property
    def name(self) -> str:
        return "another"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["hr", "hr_attendance"]

    def register(self, server: Any, context: Any) -> None:
        pass


class BrokenRegisterPlugin(OdooPlugin):
    @property
    def name(self) -> str:
        return "broken_register"

    def register(self, server: Any, context: Any) -> None:
        raise RuntimeError("register exploded")


class BrokenInitPlugin(OdooPlugin):
    """Plugin whose __init__ raises."""

    def __init__(self) -> None:
        super().__init__()
        raise ValueError("init failed")

    @property
    def name(self) -> str:
        return "broken_init"

    def register(self, server: Any, context: Any) -> None:
        pass


def _make_entry_point(
    name: str,
    plugin_class: type | None = None,
    load_error: Exception | None = None,
) -> MagicMock:
    """Build a mock entry_point."""
    ep = MagicMock()
    ep.name = name
    if load_error:
        ep.load.side_effect = load_error
    else:
        ep.load.return_value = plugin_class
    return ep


# ── Discovery tests ──────────────────────────────────────────────


class TestDiscover:
    def test_discover_no_entry_points(self) -> None:
        registry = PluginRegistry()
        with patch(_EP_PATH) as mock_eps:
            mock_result = MagicMock()
            mock_result.select.return_value = []
            mock_eps.return_value = mock_result
            result = registry.discover()
        assert result == []

    def test_discover_loads_valid_plugin(self) -> None:
        registry = PluginRegistry()
        ep = _make_entry_point("sample", SamplePlugin)
        with patch(_EP_PATH) as mock_eps:
            mock_result = MagicMock()
            mock_result.select.return_value = [ep]
            mock_eps.return_value = mock_result
            result = registry.discover()
        assert len(result) == 1
        assert result[0].name == "sample"
        assert result[0].instance is not None

    def test_discover_handles_load_error(self) -> None:
        registry = PluginRegistry()
        ep = _make_entry_point("bad", load_error=ImportError("no module"))
        with patch(_EP_PATH) as mock_eps:
            mock_result = MagicMock()
            mock_result.select.return_value = [ep]
            mock_eps.return_value = mock_result
            result = registry.discover()
        assert len(result) == 1
        assert result[0].load_error is not None

    def test_discover_rejects_non_subclass(self) -> None:
        registry = PluginRegistry()
        ep = _make_entry_point("notplugin", plugin_class=dict)
        with patch(_EP_PATH) as mock_eps:
            mock_result = MagicMock()
            mock_result.select.return_value = [ep]
            mock_eps.return_value = mock_result
            result = registry.discover()
        assert len(result) == 1
        assert "not a subclass" in (result[0].load_error or "")

    def test_discover_respects_blocklist(self) -> None:
        registry = PluginRegistry(disabled_plugins=["sample"])
        ep = _make_entry_point("sample", SamplePlugin)
        with patch(_EP_PATH) as mock_eps:
            mock_result = MagicMock()
            mock_result.select.return_value = [ep]
            mock_eps.return_value = mock_result
            result = registry.discover()
        assert len(result) == 1
        assert result[0].enabled is False
        assert result[0].load_error == "Disabled by configuration"

    def test_discover_handles_entry_points_exception(self) -> None:
        registry = PluginRegistry()
        with patch(_EP_PATH) as mock_eps:
            mock_eps.side_effect = RuntimeError("broken")
            result = registry.discover()
        assert result == []

    def test_discover_fallback_no_select(self) -> None:
        """Fallback when entry_points() returns a dict."""
        registry = PluginRegistry()
        ep = _make_entry_point("sample", SamplePlugin)
        with patch(_EP_PATH) as mock_eps:
            mock_dict: dict[str, list[Any]] = {
                ENTRY_POINT_GROUP: [ep],
            }
            mock_eps.return_value = mock_dict
            result = registry.discover()
        assert len(result) == 1
        assert result[0].name == "sample"


# ── register_plugin tests ────────────────────────────────────────


class TestRegisterPlugin:
    def test_register_plugin_adds_to_registry(self) -> None:
        registry = PluginRegistry()
        info = registry.register_plugin(SamplePlugin)
        assert info.name == "sample"
        assert registry.get_plugin("sample") is info

    def test_register_plugin_instantiates(self) -> None:
        registry = PluginRegistry()
        info = registry.register_plugin(SamplePlugin)
        assert info.instance is not None
        assert isinstance(info.instance, SamplePlugin)

    def test_register_plugin_captures_metadata(self) -> None:
        registry = PluginRegistry()
        info = registry.register_plugin(SamplePlugin)
        assert info.version == "1.0.0"
        assert info.description == "A sample plugin"
        assert info.required_modules == ["sale"]

    def test_register_plugin_handles_init_error(self) -> None:
        registry = PluginRegistry()
        info = registry.register_plugin(BrokenInitPlugin)
        assert info.load_error is not None
        assert "init failed" in info.load_error
        assert info.instance is None


# ── get_* query methods ──────────────────────────────────────────


class TestGetMethods:
    def test_get_plugin_returns_none_for_unknown(self) -> None:
        registry = PluginRegistry()
        assert registry.get_plugin("nonexistent") is None

    def test_get_all_plugins(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SamplePlugin)
        registry.register_plugin(AnotherPlugin)
        assert len(registry.get_all_plugins()) == 2

    def test_get_active_plugins_filters_disabled(self) -> None:
        registry = PluginRegistry()
        info_sample = registry.register_plugin(SamplePlugin)
        registry.register_plugin(AnotherPlugin)
        info_sample.enabled = False
        active = registry.get_active_plugins()
        assert len(active) == 1
        assert active[0].name == "another"

    def test_get_active_plugins_filters_errors(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SamplePlugin)
        registry.register_plugin(BrokenInitPlugin)
        active = registry.get_active_plugins()
        assert len(active) == 1
        assert active[0].name == "sample"


# ── check_requirements tests ─────────────────────────────────────


class TestCheckRequirements:
    @pytest.mark.asyncio
    async def test_all_modules_present(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SamplePlugin)
        result = await registry.check_requirements(["sale", "purchase"])
        assert len(result) == 1
        assert result[0].missing_modules == []
        assert result[0].enabled is True

    @pytest.mark.asyncio
    async def test_missing_module_disables_plugin(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SamplePlugin)
        result = await registry.check_requirements(["purchase"])
        assert len(result) == 1
        assert result[0].missing_modules == ["sale"]
        assert result[0].enabled is False

    @pytest.mark.asyncio
    async def test_partial_modules_missing(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(AnotherPlugin)
        result = await registry.check_requirements(["hr"])
        assert result[0].missing_modules == ["hr_attendance"]
        assert result[0].enabled is False

    @pytest.mark.asyncio
    async def test_skips_plugins_without_instance(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(BrokenInitPlugin)
        result = await registry.check_requirements(["sale"])
        assert len(result) == 0


# ── activate tests ───────────────────────────────────────────────


class TestActivate:
    def test_activate_calls_register(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SamplePlugin)
        server = MagicMock()
        context = MagicMock()
        activated = registry.activate(server, context)
        assert activated == ["sample"]

    def test_activate_skips_disabled_plugins(self) -> None:
        registry = PluginRegistry()
        info = registry.register_plugin(SamplePlugin)
        info.enabled = False
        activated = registry.activate(MagicMock(), MagicMock())
        assert activated == []

    def test_activate_skips_plugins_with_load_error(self) -> None:
        registry = PluginRegistry()
        info = registry.register_plugin(SamplePlugin)
        info.load_error = "something broke"
        activated = registry.activate(MagicMock(), MagicMock())
        assert activated == []

    def test_activate_handles_register_error(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(BrokenRegisterPlugin)
        activated = registry.activate(MagicMock(), MagicMock())
        assert activated == []
        info = registry.get_plugin("broken_register")
        assert info is not None
        assert info.load_error is not None

    def test_activate_returns_activated_names(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SamplePlugin)
        registry.register_plugin(AnotherPlugin)
        activated = registry.activate(MagicMock(), MagicMock())
        assert "sample" in activated
        assert "another" in activated

    def test_activate_respects_blocklist(self) -> None:
        registry = PluginRegistry(disabled_plugins=["sample"])
        registry.register_plugin(SamplePlugin)
        registry.register_plugin(AnotherPlugin)
        activated = registry.activate(MagicMock(), MagicMock())
        assert "sample" not in activated
        assert "another" in activated

    def test_activate_respects_allowlist(self) -> None:
        registry = PluginRegistry(enabled_plugins=["sample"])
        registry.register_plugin(SamplePlugin)
        registry.register_plugin(AnotherPlugin)
        activated = registry.activate(MagicMock(), MagicMock())
        assert activated == ["sample"]

    def test_activate_skips_no_instance(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(BrokenInitPlugin)
        activated = registry.activate(MagicMock(), MagicMock())
        assert activated == []
