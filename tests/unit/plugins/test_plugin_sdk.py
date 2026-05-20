"""Tests for the Plugin SDK 1.0 compat checker + Protocol surface."""

from __future__ import annotations

from odoo_mcp_gateway.plugins.sdk import (
    PLUGIN_SDK_VERSION,
    OdooMcpPlugin,
    PluginContext,
    check_plugin_sdk_compat,
)


class TestCheckPluginSdkCompat:
    def test_matching_range_loads_clean(self) -> None:
        ok, msg = check_plugin_sdk_compat("p", ">=1.0,<2.0")
        assert ok is True
        assert msg is None

    def test_missing_spec_warns_but_loads(self) -> None:
        ok, msg = check_plugin_sdk_compat("p", None)
        assert ok is True
        assert msg is not None
        assert "plugin_sdk_version" in msg

    def test_empty_string_treated_as_missing(self) -> None:
        ok, msg = check_plugin_sdk_compat("p", "")
        assert ok is True
        assert msg is not None

    def test_invalid_specifier_refused(self) -> None:
        ok, msg = check_plugin_sdk_compat("p", "@@@not-a-version-spec@@@")
        assert ok is False
        assert msg is not None
        assert "invalid" in msg.lower()

    def test_major_mismatch_refused(self) -> None:
        # Plugin wants 2.x, core is 1.x — refuse.
        ok, msg = check_plugin_sdk_compat("p", ">=2.0,<3.0")
        assert ok is False
        assert msg is not None
        assert "major mismatch" in msg.lower()

    def test_minor_mismatch_loads_with_warning(self) -> None:
        # Plugin wants >=1.99,<2.0 — core is 1.0; same major.
        ok, msg = check_plugin_sdk_compat("p", ">=1.99,<2.0")
        assert ok is True
        assert msg is not None
        assert "same major" in msg.lower() or "loading anyway" in msg.lower()


class TestProtocolSurface:
    def test_odoo_plugin_implements_protocol(self) -> None:
        """The convenience base class MUST satisfy the Protocol via
        runtime structural check."""
        from odoo_mcp_gateway.plugins.base import OdooPlugin

        # We can't instantiate OdooPlugin directly (it has abstract
        # `name` and `register`). Test a concrete subclass instead.
        class _Concrete(OdooPlugin):
            plugin_sdk_version = ">=1.0,<2.0"

            @property
            def name(self) -> str:
                return "concrete"

            def register(self, server, context) -> None:
                pass

        inst = _Concrete()
        assert isinstance(inst, OdooMcpPlugin)

    def test_external_plugin_satisfies_protocol_without_inheriting(self) -> None:
        """Authors can implement the Protocol WITHOUT inheriting OdooPlugin."""

        class _External:
            name = "external"
            version = "0.1.0"
            plugin_sdk_version = ">=1.0,<2.0"
            required_odoo_modules: tuple[str, ...] = ()
            required_models: tuple[str, ...] = ()

            def register(self, server, context) -> None:
                pass

            async def pre_register(self, context) -> None:
                pass

            async def post_register(self, context) -> None:
                pass

            async def pre_call(self, tool, arguments, context) -> None:
                pass

            async def post_call(self, tool, result, context):  # noqa: ANN201
                return result

            async def on_session_close(self, session_key, context) -> None:
                pass

            async def on_external_event(self, event_type, payload, context) -> None:
                pass

        assert isinstance(_External(), OdooMcpPlugin)


class TestPluginContext:
    def test_exposes_gateway_via_attribute(self) -> None:
        from unittest.mock import MagicMock

        gw = MagicMock()
        ctx = PluginContext(gw, plugin_name="hr")
        assert ctx.gateway is gw
        assert ctx.plugin_name == "hr"
        assert ctx.plugin_sdk_version == PLUGIN_SDK_VERSION

    def test_convenience_accessors_proxy_gateway(self) -> None:
        from unittest.mock import MagicMock

        gw = MagicMock()
        gw.rbac = "rbac-sentinel"
        gw.restrictions = "restrictions-sentinel"
        gw.field_inspector = "fi-sentinel"
        gw.version_adapter = "va-sentinel"
        ctx = PluginContext(gw, plugin_name="x")
        assert ctx.rbac == "rbac-sentinel"
        assert ctx.restrictions == "restrictions-sentinel"
        assert ctx.field_inspector == "fi-sentinel"
        assert ctx.version_adapter == "va-sentinel"


class TestRegistryCompatIntegration:
    def test_built_in_plugins_pass_compat(self) -> None:
        """Every built-in plugin must declare a valid plugin_sdk_version."""
        from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin
        from odoo_mcp_gateway.plugins.core.project import ProjectPlugin
        from odoo_mcp_gateway.plugins.core.sales import SalesPlugin

        for cls in (HRPlugin, SalesPlugin, ProjectPlugin, HelpdeskPlugin):
            spec = getattr(cls, "plugin_sdk_version", None)
            assert spec is not None, f"{cls.__name__} missing plugin_sdk_version"
            ok, msg = check_plugin_sdk_compat(cls.__name__, spec)
            assert ok, f"{cls.__name__} fails SDK compat: {msg}"
