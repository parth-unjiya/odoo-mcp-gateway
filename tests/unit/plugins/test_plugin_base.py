"""Tests for the plugin base class."""

from __future__ import annotations

from typing import Any

import pytest

from odoo_mcp_gateway.plugins.base import OdooPlugin

# -- Helpers -----------------------------------------------------------


class MinimalPlugin(OdooPlugin):
    """Smallest valid concrete plugin."""

    @property
    def name(self) -> str:
        return "minimal"

    def register(self, server: Any, context: Any) -> None:
        pass


class FullPlugin(OdooPlugin):
    """Plugin with all optional properties overridden."""

    @property
    def name(self) -> str:
        return "full"

    @property
    def version(self) -> str:
        return "1.2.3"

    @property
    def description(self) -> str:
        return "A fully configured plugin"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["hr", "hr_attendance"]

    @property
    def required_models(self) -> list[str]:
        return ["hr.employee", "hr.attendance"]

    def register(self, server: Any, context: Any) -> None:
        pass


# -- Abstract base class tests ----------------------------------------


class TestOdooPluginAbstract:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            OdooPlugin()  # type: ignore[abstract]

    def test_subclass_without_name_raises(self) -> None:
        class Incomplete(OdooPlugin):
            def register(self, server: Any, context: Any) -> None:
                pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_without_register_raises(self) -> None:
        class Incomplete(OdooPlugin):
            @property
            def name(self) -> str:
                return "incomplete"

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


# -- Default property values -------------------------------------------


class TestDefaultPropertyValues:
    def test_default_version(self) -> None:
        plugin = MinimalPlugin()
        assert plugin.version == "0.1.0"

    def test_default_description(self) -> None:
        plugin = MinimalPlugin()
        assert plugin.description == ""

    def test_default_required_odoo_modules(self) -> None:
        plugin = MinimalPlugin()
        assert plugin.required_odoo_modules == []

    def test_default_required_models(self) -> None:
        plugin = MinimalPlugin()
        assert plugin.required_models == []


# -- Fully configured plugin -------------------------------------------


class TestFullPlugin:
    def test_name(self) -> None:
        plugin = FullPlugin()
        assert plugin.name == "full"

    def test_version(self) -> None:
        plugin = FullPlugin()
        assert plugin.version == "1.2.3"

    def test_description(self) -> None:
        plugin = FullPlugin()
        assert plugin.description == "A fully configured plugin"

    def test_required_odoo_modules(self) -> None:
        plugin = FullPlugin()
        assert plugin.required_odoo_modules == ["hr", "hr_attendance"]

    def test_required_models(self) -> None:
        plugin = FullPlugin()
        assert plugin.required_models == ["hr.employee", "hr.attendance"]
