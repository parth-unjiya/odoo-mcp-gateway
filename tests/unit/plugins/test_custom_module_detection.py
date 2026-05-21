"""Tests for v0.3.3 follow-up MED-3 — custom-module detection.

Operators running custom Odoo modules whose model / module names
differ from a plugin's declared requirements can opt into
compatibility via ``model_access.yaml::plugin_overrides``.  The
registry OR-merges the override into the plugin's ``required_*``
lists; a plugin is satisfied if ANY of (declared ∪ accepted) is
present.  This module pins that contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from odoo_mcp_gateway.core.security.config_loader import PluginOverride
from odoo_mcp_gateway.plugins.base import OdooPlugin
from odoo_mcp_gateway.plugins.registry import PluginRegistry


class _HelpdeskLike(OdooPlugin):
    """Stand-in for the real HelpdeskPlugin without its tool registration."""

    @property
    def name(self) -> str:
        return "helpdesk_like"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["helpdesk"]

    @property
    def required_models(self) -> list[str]:
        return ["helpdesk.ticket"]

    def register(self, server: Any, context: Any) -> None:  # pragma: no cover
        pass


class _DoubleModulePlugin(OdooPlugin):
    """Plugin declaring TWO required modules — legacy AND semantics apply
    here when no override is configured."""

    @property
    def name(self) -> str:
        return "double_module"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["hr", "hr_attendance"]

    def register(self, server: Any, context: Any) -> None:  # pragma: no cover
        pass


# ----------------------------------------------------------------------
# set_plugin_overrides — basic plumbing
# ----------------------------------------------------------------------


class TestSetPluginOverrides:
    def test_override_applied_to_registered_plugin(self) -> None:
        """Override applied AFTER the plugin is registered should land
        on its PluginInfo immediately."""
        registry = PluginRegistry()
        info = registry.register_plugin(_HelpdeskLike)
        assert info.accept_modules == []
        assert info.accept_models == []

        registry.set_plugin_overrides(
            {
                "helpdesk_like": PluginOverride(
                    accept_modules=["helpdesk", "odoo_website_helpdesk"],
                    accept_models=["helpdesk.ticket", "ticket.helpdesk"],
                )
            }
        )

        info = registry.get_plugin("helpdesk_like")
        assert info is not None
        assert info.accept_modules == ["helpdesk", "odoo_website_helpdesk"]
        assert info.accept_models == ["helpdesk.ticket", "ticket.helpdesk"]
        # effective_model_name picks the first accepted model.
        assert info.effective_model_name == "helpdesk.ticket"

    def test_override_with_dict_input(self) -> None:
        """The setter accepts both Pydantic models and plain dicts."""
        registry = PluginRegistry()
        registry.set_plugin_overrides(
            {
                "helpdesk_like": {
                    "accept_modules": ["alt_helpdesk"],
                    "accept_models": ["ticket.helpdesk"],
                }
            }
        )
        registry.register_plugin(_HelpdeskLike)

        info = registry.get_plugin("helpdesk_like")
        assert info is not None
        assert info.accept_modules == ["alt_helpdesk"]
        assert info.accept_models == ["ticket.helpdesk"]
        assert info.effective_model_name == "ticket.helpdesk"

    def test_override_pending_until_plugin_registers(self) -> None:
        """An override for an unknown plugin is held as pending and
        applied once the plugin registers later."""
        registry = PluginRegistry()
        registry.set_plugin_overrides(
            {
                "helpdesk_like": PluginOverride(
                    accept_modules=["custom_help"],
                    accept_models=["custom.ticket"],
                )
            }
        )
        # Plugin not yet registered.
        assert registry.get_plugin("helpdesk_like") is None

        registry.register_plugin(_HelpdeskLike)
        info = registry.get_plugin("helpdesk_like")
        assert info is not None
        assert info.accept_modules == ["custom_help"]
        assert info.accept_models == ["custom.ticket"]

    def test_no_override_keeps_v032_behavior(self) -> None:
        """Without an override, ``effective_model_name`` defaults to the
        plugin's ``required_models[0]`` (or ``None`` if absent)."""
        registry = PluginRegistry()
        info = registry.register_plugin(_HelpdeskLike)
        # No overrides applied.
        assert info.accept_modules == []
        assert info.accept_models == []
        assert info.effective_model_name == "helpdesk.ticket"

    def test_ignores_unsupported_value_types(self) -> None:
        """A garbage value type for an override entry is dropped, not
        raised — operator typos shouldn't break startup."""
        registry = PluginRegistry()
        registry.set_plugin_overrides({"helpdesk_like": "not-a-dict"})
        registry.register_plugin(_HelpdeskLike)
        info = registry.get_plugin("helpdesk_like")
        assert info is not None
        # Garbage entry was silently dropped → defaults stand.
        assert info.accept_modules == []
        assert info.accept_models == []


# ----------------------------------------------------------------------
# check_requirements — OR semantics on override paths
# ----------------------------------------------------------------------


class TestCheckRequirementsWithOverride:
    @pytest.mark.asyncio
    async def test_any_module_accepted_satisfies(self) -> None:
        """Plugin with override is satisfied if ANY accepted module
        is installed (custom-module-only case)."""
        registry = PluginRegistry()
        registry.set_plugin_overrides(
            {
                "helpdesk_like": PluginOverride(
                    accept_modules=["helpdesk", "odoo_website_helpdesk"],
                    accept_models=["helpdesk.ticket", "ticket.helpdesk"],
                )
            }
        )
        registry.register_plugin(_HelpdeskLike)

        await registry.check_requirements(["odoo_website_helpdesk"])
        info = registry.get_plugin("helpdesk_like")
        assert info is not None
        assert info.enabled is True
        assert info.missing_modules == []

    @pytest.mark.asyncio
    async def test_no_modules_disables_with_union_list(self) -> None:
        """When neither declared nor accepted module is installed the
        plugin disables and ``missing_modules`` reports the union."""
        registry = PluginRegistry()
        registry.set_plugin_overrides(
            {
                "helpdesk_like": PluginOverride(
                    accept_modules=["helpdesk", "odoo_website_helpdesk"],
                )
            }
        )
        registry.register_plugin(_HelpdeskLike)

        await registry.check_requirements([])
        info = registry.get_plugin("helpdesk_like")
        assert info is not None
        assert info.enabled is False
        # Sorted union of declared + accepted.
        assert info.missing_modules == ["helpdesk", "odoo_website_helpdesk"]

    @pytest.mark.asyncio
    async def test_no_override_preserves_v032_behaviour(self) -> None:
        """Without an override the legacy AND semantics across declared
        modules still apply: every declared module must be installed.
        """
        registry = PluginRegistry()
        registry.register_plugin(_DoubleModulePlugin)

        # Only one of two declared modules installed → still disabled.
        await registry.check_requirements(["hr"])
        info = registry.get_plugin("double_module")
        assert info is not None
        assert info.enabled is False
        assert info.missing_modules == ["hr_attendance"]

    @pytest.mark.asyncio
    async def test_no_override_satisfied_when_all_declared_present(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(_DoubleModulePlugin)

        await registry.check_requirements(["hr", "hr_attendance"])
        info = registry.get_plugin("double_module")
        assert info is not None
        assert info.enabled is True
        assert info.missing_modules == []

    @pytest.mark.asyncio
    async def test_declared_module_alone_satisfies_with_override(self) -> None:
        """Override active and declared module is installed — still OK
        (OR pool includes both declared and accepted)."""
        registry = PluginRegistry()
        registry.set_plugin_overrides(
            {
                "helpdesk_like": PluginOverride(
                    accept_modules=["odoo_website_helpdesk"],
                )
            }
        )
        registry.register_plugin(_HelpdeskLike)

        await registry.check_requirements(["helpdesk"])
        info = registry.get_plugin("helpdesk_like")
        assert info is not None
        assert info.enabled is True
        assert info.missing_modules == []
