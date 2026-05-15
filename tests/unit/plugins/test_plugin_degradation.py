"""Tests for graceful plugin degradation when Odoo modules are missing."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin
from odoo_mcp_gateway.plugins.core.helpers import check_plugin_modules
from odoo_mcp_gateway.plugins.core.hr import HRPlugin
from odoo_mcp_gateway.plugins.core.project import ProjectPlugin
from odoo_mcp_gateway.plugins.core.sales import SalesPlugin
from odoo_mcp_gateway.plugins.registry import PluginRegistry


class TestCheckRequirements:
    """Test PluginRegistry.check_requirements against installed modules."""

    async def test_disables_plugin_with_missing_modules(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(HRPlugin)
        # Only "hr" installed, missing "hr_attendance" and "hr_holidays"
        await registry.check_requirements(["hr"])
        hr_info = registry.get_plugin("hr")
        assert hr_info is not None
        assert hr_info.enabled is False
        assert "hr_attendance" in hr_info.missing_modules

    async def test_enables_plugin_with_all_modules(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(HRPlugin)
        await registry.check_requirements(["hr", "hr_attendance", "hr_holidays"])
        hr_info = registry.get_plugin("hr")
        assert hr_info is not None
        assert hr_info.enabled is True
        assert hr_info.missing_modules == []

    async def test_disables_sales_with_missing_module(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SalesPlugin)
        await registry.check_requirements(["base", "contacts"])
        info = registry.get_plugin("sales")
        assert info is not None
        assert info.enabled is False
        assert "sale" in info.missing_modules

    async def test_enables_sales_with_sale_module(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SalesPlugin)
        await registry.check_requirements(["sale"])
        info = registry.get_plugin("sales")
        assert info is not None
        assert info.enabled is True
        assert info.missing_modules == []

    async def test_disables_project_with_missing_module(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(ProjectPlugin)
        await registry.check_requirements(["base"])
        info = registry.get_plugin("project")
        assert info is not None
        assert info.enabled is False
        assert "project" in info.missing_modules

    async def test_enables_project_with_project_module(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(ProjectPlugin)
        await registry.check_requirements(["project"])
        info = registry.get_plugin("project")
        assert info is not None
        assert info.enabled is True

    async def test_disables_helpdesk_with_missing_module(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(HelpdeskPlugin)
        await registry.check_requirements(["base", "sale"])
        info = registry.get_plugin("helpdesk")
        assert info is not None
        assert info.enabled is False
        assert "helpdesk" in info.missing_modules

    async def test_enables_helpdesk_with_helpdesk_module(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(HelpdeskPlugin)
        await registry.check_requirements(["helpdesk"])
        info = registry.get_plugin("helpdesk")
        assert info is not None
        assert info.enabled is True

    async def test_multiple_plugins_mixed(self) -> None:
        """Some plugins enabled, others disabled based on installed modules."""
        registry = PluginRegistry()
        registry.register_plugin(HRPlugin)
        registry.register_plugin(SalesPlugin)
        registry.register_plugin(ProjectPlugin)
        registry.register_plugin(HelpdeskPlugin)

        # Only sale and project installed
        await registry.check_requirements(["sale", "project"])

        hr_info = registry.get_plugin("hr")
        assert hr_info is not None
        assert hr_info.enabled is False

        sales_info = registry.get_plugin("sales")
        assert sales_info is not None
        assert sales_info.enabled is True

        project_info = registry.get_plugin("project")
        assert project_info is not None
        assert project_info.enabled is True

        helpdesk_info = registry.get_plugin("helpdesk")
        assert helpdesk_info is not None
        assert helpdesk_info.enabled is False

    async def test_check_requirements_returns_all_checked(self) -> None:
        """Return value includes all checked plugins."""
        registry = PluginRegistry()
        registry.register_plugin(HRPlugin)
        registry.register_plugin(SalesPlugin)
        result = await registry.check_requirements(["sale"])
        assert len(result) == 2


class TestCheckPluginModulesHelper:
    """Test the check_plugin_modules helper function."""

    def test_returns_none_when_no_registry(self) -> None:
        context = MagicMock(spec=[])  # No plugin_registry attribute
        result = check_plugin_modules(context, "hr", ["hr.employee"])
        assert result is None

    def test_returns_none_when_plugin_not_found(self) -> None:
        registry = PluginRegistry()
        context = MagicMock()
        context.plugin_registry = registry
        result = check_plugin_modules(context, "nonexistent", [])
        assert result is None

    def test_returns_none_when_no_missing_modules(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SalesPlugin)
        # All modules present
        info = registry.get_plugin("sales")
        assert info is not None
        info.missing_modules = []

        context = MagicMock()
        context.plugin_registry = registry
        result = check_plugin_modules(context, "sales", ["sale.order"])
        assert result is None

    def test_returns_error_when_modules_missing(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(HRPlugin)
        info = registry.get_plugin("hr")
        assert info is not None
        info.missing_modules = ["hr_attendance", "hr_holidays"]

        context = MagicMock()
        context.plugin_registry = registry
        result = check_plugin_modules(context, "hr", ["hr.employee"])
        assert result is not None
        assert "hr_attendance" in result
        assert "hr_holidays" in result
        assert "not installed" in result


class TestPluginToolDegradation:
    """Test that plugin tools return clear errors when modules are missing."""

    def _make_context(
        self,
        registry: PluginRegistry,
        uid: int = 1,
    ) -> MagicMock:
        """Build a minimal mock GatewayContext with an auth manager."""
        mock_client = AsyncMock()
        mock_auth_result = MagicMock()
        mock_auth_result.uid = uid
        mock_auth_result.is_admin = False
        mock_auth_result.groups = []

        mock_auth_mgr = MagicMock()
        mock_auth_mgr.get_active_client.return_value = mock_client
        mock_auth_mgr.auth_result = mock_auth_result

        context = MagicMock()
        context.auth_managers = {"1_test": mock_auth_mgr}
        context.plugin_registry = registry
        context.restrictions = MagicMock()
        context.restrictions.check_model_access.return_value = None
        context.restrictions.check_field_write.return_value = None
        context.rbac = MagicMock()
        context.rbac.filter_response_fields.return_value = None
        context.rbac.sanitize_write_values.return_value = None
        context.sanitize_error.side_effect = lambda e: str(e)
        return context

    async def test_hr_check_in_returns_error_when_modules_missing(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(HRPlugin)
        # Mark modules as missing
        info = registry.get_plugin("hr")
        assert info is not None
        info.missing_modules = ["hr_attendance"]
        info.enabled = False

        context = self._make_context(registry)
        server = MagicMock()
        tools: dict[str, Any] = {}

        # Capture tool registrations
        def capture_tool():
            def decorator(func: Any) -> Any:
                tools[func.__name__] = func
                return func
            return decorator

        server.tool = capture_tool
        plugin = HRPlugin()
        plugin.register(server, context)

        result = await tools["check_in"]()
        assert "error" in result
        assert "hr_attendance" in result["error"]
        assert "not installed" in result["error"]

    async def test_sales_tool_returns_error_when_modules_missing(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(SalesPlugin)
        info = registry.get_plugin("sales")
        assert info is not None
        info.missing_modules = ["sale"]
        info.enabled = False

        context = self._make_context(registry)
        server = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(func: Any) -> Any:
                tools[func.__name__] = func
                return func
            return decorator

        server.tool = capture_tool
        plugin = SalesPlugin()
        plugin.register(server, context)

        result = await tools["get_my_quotations"]()
        assert "error" in result
        assert "sale" in result["error"]

    async def test_project_tool_returns_error_when_modules_missing(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(ProjectPlugin)
        info = registry.get_plugin("project")
        assert info is not None
        info.missing_modules = ["project"]
        info.enabled = False

        context = self._make_context(registry)
        server = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(func: Any) -> Any:
                tools[func.__name__] = func
                return func
            return decorator

        server.tool = capture_tool
        plugin = ProjectPlugin()
        plugin.register(server, context)

        result = await tools["get_my_tasks"]()
        assert "error" in result
        assert "project" in result["error"]

    async def test_helpdesk_tool_returns_error_when_modules_missing(self) -> None:
        registry = PluginRegistry()
        registry.register_plugin(HelpdeskPlugin)
        info = registry.get_plugin("helpdesk")
        assert info is not None
        info.missing_modules = ["helpdesk"]
        info.enabled = False

        context = self._make_context(registry)
        server = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(func: Any) -> Any:
                tools[func.__name__] = func
                return func
            return decorator

        server.tool = capture_tool
        plugin = HelpdeskPlugin()
        plugin.register(server, context)

        result = await tools["get_my_tickets"]()
        assert "error" in result
        assert "helpdesk" in result["error"]

    async def test_tool_works_normally_when_modules_present(self) -> None:
        """When no modules are missing, the tool proceeds normally."""
        registry = PluginRegistry()
        registry.register_plugin(SalesPlugin)
        # All modules present (default: missing_modules = [])

        context = self._make_context(registry)
        server = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(func: Any) -> Any:
                tools[func.__name__] = func
                return func
            return decorator

        server.tool = capture_tool
        plugin = SalesPlugin()
        plugin.register(server, context)

        # Mock the security gate to allow through
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "odoo_mcp_gateway.plugins.core.helpers.security_gate",
                AsyncMock(return_value=None),
            )
            mp.setattr(
                "odoo_mcp_gateway.plugins.core.helpers.get_current_session_key",
                lambda: "1_test",
            )
            # The mock client will return an empty list for search_read
            mock_client = context.auth_managers["1_test"].get_active_client()
            mock_client.execute_kw.return_value = []

            result = await tools["get_my_quotations"]()
            # Should not have a module error; may have "Not authenticated"
            # or proceed to the actual logic
            assert "not installed" not in result.get("error", "")
