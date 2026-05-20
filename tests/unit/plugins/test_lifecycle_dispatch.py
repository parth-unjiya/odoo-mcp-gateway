"""Tests for PluginRegistry's lifecycle hook dispatch (ADR-003 Sprint 3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from odoo_mcp_gateway.plugins.base import OdooPlugin
from odoo_mcp_gateway.plugins.registry import PluginRegistry


class _RecorderPlugin(OdooPlugin):
    """Plugin that records each hook invocation for assertion."""

    plugin_sdk_version = ">=1.0,<2.0"

    @property
    def name(self) -> str:
        return "recorder"

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple, dict]] = []

    def register(self, server: Any, context: Any) -> None:
        self.events.append(("register", (), {}))

    async def pre_register(self, context: Any) -> None:
        self.events.append(("pre_register", (), {}))

    async def post_register(self, context: Any) -> None:
        self.events.append(("post_register", (), {}))

    async def pre_call(self, tool: str, arguments: dict, context: Any) -> None:
        self.events.append(("pre_call", (tool, arguments), {}))

    async def post_call(self, tool: str, result: Any, context: Any) -> Any:
        self.events.append(("post_call", (tool, result), {}))
        return result

    async def on_session_close(self, session_key: str, context: Any) -> None:
        self.events.append(("on_session_close", (session_key,), {}))

    async def on_external_event(
        self, event_type: str, payload: dict, context: Any
    ) -> None:
        self.events.append(("on_external_event", (event_type, payload), {}))


class _ErrorOnHook(OdooPlugin):
    """Plugin that raises in lifecycle hooks — exercises error
    isolation."""

    plugin_sdk_version = ">=1.0,<2.0"

    @property
    def name(self) -> str:
        return "errored"

    def register(self, server: Any, context: Any) -> None:
        pass

    async def pre_register(self, context: Any) -> None:
        raise RuntimeError("nope")

    async def post_call(self, tool: str, result: Any, context: Any) -> Any:
        raise RuntimeError("post fail")


@pytest.fixture
def registry_with_recorder() -> tuple[PluginRegistry, _RecorderPlugin]:
    reg = PluginRegistry()
    reg.register_plugin(_RecorderPlugin)
    info = reg.get_plugin("recorder")
    assert info is not None and info.instance is not None
    return reg, info.instance  # type: ignore[return-value]


class TestLifecycleDispatch:
    @pytest.mark.asyncio
    async def test_pre_register_dispatch(
        self, registry_with_recorder: tuple[PluginRegistry, _RecorderPlugin]
    ) -> None:
        reg, plugin = registry_with_recorder
        gw = MagicMock()
        await reg.dispatch_pre_register(gw)
        assert ("pre_register", (), {}) in plugin.events

    @pytest.mark.asyncio
    async def test_post_register_dispatch(
        self, registry_with_recorder: tuple[PluginRegistry, _RecorderPlugin]
    ) -> None:
        reg, plugin = registry_with_recorder
        gw = MagicMock()
        await reg.dispatch_post_register(gw)
        assert ("post_register", (), {}) in plugin.events

    @pytest.mark.asyncio
    async def test_pre_call_dispatch_passes_args(
        self, registry_with_recorder: tuple[PluginRegistry, _RecorderPlugin]
    ) -> None:
        reg, plugin = registry_with_recorder
        gw = MagicMock()
        await reg.dispatch_pre_call(gw, "my_tool", {"a": 1})
        assert plugin.events[-1] == ("pre_call", ("my_tool", {"a": 1}), {})

    @pytest.mark.asyncio
    async def test_post_call_returns_result(
        self, registry_with_recorder: tuple[PluginRegistry, _RecorderPlugin]
    ) -> None:
        reg, plugin = registry_with_recorder
        gw = MagicMock()
        result = await reg.dispatch_post_call(gw, "my_tool", {"x": 1})
        assert result == {"x": 1}  # recorder doesn't transform
        assert plugin.events[-1] == ("post_call", ("my_tool", {"x": 1}), {})

    @pytest.mark.asyncio
    async def test_on_session_close_dispatch(
        self, registry_with_recorder: tuple[PluginRegistry, _RecorderPlugin]
    ) -> None:
        reg, plugin = registry_with_recorder
        gw = MagicMock()
        await reg.dispatch_on_session_close(gw, "2_db")
        assert plugin.events[-1] == ("on_session_close", ("2_db",), {})

    @pytest.mark.asyncio
    async def test_on_external_event_dispatch(
        self, registry_with_recorder: tuple[PluginRegistry, _RecorderPlugin]
    ) -> None:
        reg, plugin = registry_with_recorder
        gw = MagicMock()
        await reg.dispatch_on_external_event(gw, "record.created", {"id": 5})
        assert plugin.events[-1] == (
            "on_external_event",
            ("record.created", {"id": 5}),
            {},
        )


class TestErrorIsolation:
    """Hook failures in one plugin must not crash the dispatch."""

    @pytest.mark.asyncio
    async def test_pre_register_error_swallowed(self) -> None:
        reg = PluginRegistry()
        reg.register_plugin(_ErrorOnHook)
        reg.register_plugin(_RecorderPlugin)
        recorder = reg.get_plugin("recorder")
        assert recorder is not None and recorder.instance is not None
        gw = MagicMock()
        # Must not raise even though _ErrorOnHook.pre_register raises.
        await reg.dispatch_pre_register(gw)
        # Recorder STILL got called, proving error isolation.
        assert ("pre_register", (), {}) in recorder.instance.events  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_post_call_error_does_not_drop_result(self) -> None:
        reg = PluginRegistry()
        reg.register_plugin(_ErrorOnHook)
        gw = MagicMock()
        # _ErrorOnHook.post_call raises; the dispatcher swallows and
        # returns the unchanged result.
        result = await reg.dispatch_post_call(gw, "my_tool", {"x": 1})
        assert result == {"x": 1}


# ---------------------------------------------------------------------
# Audit fix #4 — END-TO-END wiring tests
#
# These exercise the FULL lifecycle: register a spy plugin, activate
# it on a real FastMCP server, call a tool, evict the session, and
# assert each hook fires the expected number of times.
# ---------------------------------------------------------------------


class _SpyPlugin(OdooPlugin):
    """Like _RecorderPlugin but with a registered tool for call tests."""

    plugin_sdk_version = ">=1.0,<2.0"

    @property
    def name(self) -> str:
        return "spy"

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple]] = []

    def register(self, server: Any, context: Any) -> None:
        self.events.append(("register", ()))

        @server.tool()
        async def spy_echo(text: str) -> str:
            """Tool registered by the spy plugin; used to verify
            pre_call / post_call wiring around real tool invocations."""
            return f"echo:{text}"

    async def pre_register(self, context: Any) -> None:
        self.events.append(("pre_register", ()))

    async def post_register(self, context: Any) -> None:
        self.events.append(("post_register", ()))

    async def pre_call(self, tool: str, arguments: dict, context: Any) -> None:
        self.events.append(("pre_call", (tool, dict(arguments))))

    async def post_call(self, tool: str, result: Any, context: Any) -> Any:
        self.events.append(("post_call", (tool, result)))
        return result

    async def on_session_close(self, session_key: str, context: Any) -> None:
        self.events.append(("on_session_close", (session_key,)))

    async def on_external_event(
        self, event_type: str, payload: dict, context: Any
    ) -> None:
        self.events.append(("on_external_event", (event_type, payload)))


class TestRegisterHooksFireDuringActivate:
    """audit-fix #4: pre/post_register must fire when activate() runs."""

    def test_pre_and_post_register_fire(self) -> None:
        from unittest.mock import MagicMock

        from mcp.server.fastmcp import FastMCP

        reg = PluginRegistry()
        reg.register_plugin(_SpyPlugin)
        server = FastMCP(name="test")
        gw = MagicMock()

        activated = reg.activate(server, gw)
        assert activated == ["spy"]

        spy = reg.get_plugin("spy")
        assert spy is not None and spy.instance is not None
        names = [e[0] for e in spy.instance.events]  # type: ignore[union-attr]
        # Order: pre_register -> register -> post_register
        assert names[0] == "pre_register"
        assert names[1] == "register"
        assert names[2] == "post_register"

    def test_post_register_skipped_on_register_failure(self) -> None:
        """If register() raises, post_register should NOT fire."""
        from unittest.mock import MagicMock

        class _BoomPlugin(OdooPlugin):
            plugin_sdk_version = ">=1.0,<2.0"
            events: list[str] = []

            @property
            def name(self) -> str:
                return "boom"

            def register(self, server: Any, context: Any) -> None:
                raise RuntimeError("register failed")

            async def pre_register(self, context: Any) -> None:
                _BoomPlugin.events.append("pre_register")

            async def post_register(self, context: Any) -> None:
                # Should NEVER be called after register() failure
                _BoomPlugin.events.append("post_register")

        _BoomPlugin.events.clear()
        reg = PluginRegistry()
        reg.register_plugin(_BoomPlugin)
        reg.activate(MagicMock(), MagicMock())

        assert "pre_register" in _BoomPlugin.events
        assert "post_register" not in _BoomPlugin.events


class TestPluginLifecycleMiddleware:
    """audit-fix #4: pre_call / post_call must fire around every tool call."""

    @pytest.mark.asyncio
    async def test_pre_and_post_call_fire_around_tool_call(self) -> None:
        from mcp.server.fastmcp import FastMCP

        from odoo_mcp_gateway.core.plugin_middleware import (
            install_plugin_middleware,
        )

        reg = PluginRegistry()
        reg.register_plugin(_SpyPlugin)
        server = FastMCP(name="test")
        gw = MagicMock()

        reg.activate(server, gw)
        install_plugin_middleware(server, reg, gw)

        await server.call_tool("spy_echo", {"text": "hi"})

        spy = reg.get_plugin("spy")
        assert spy is not None and spy.instance is not None
        names = [e[0] for e in spy.instance.events]  # type: ignore[union-attr]
        # The interesting ordering: ...register..., pre_call,
        # (tool runs), post_call. We assert pre_call comes BEFORE
        # post_call and both reference the spy_echo tool.
        assert "pre_call" in names
        assert "post_call" in names
        assert names.index("pre_call") < names.index("post_call")
        pre_events = [e for e in spy.instance.events if e[0] == "pre_call"]  # type: ignore[union-attr]
        post_events = [e for e in spy.instance.events if e[0] == "post_call"]  # type: ignore[union-attr]
        assert pre_events[0][1] == ("spy_echo", {"text": "hi"})
        # post_call sees the tool result (FastMCP wraps it in
        # content blocks; we just verify the tool name is right).
        assert post_events[0][1][0] == "spy_echo"

    @pytest.mark.asyncio
    async def test_pre_call_failure_aborts_tool(self) -> None:
        """A pre_call that RAISES must abort the tool invocation."""
        from mcp.server.fastmcp import FastMCP

        from odoo_mcp_gateway.core.plugin_middleware import (
            install_plugin_middleware,
        )

        class _Aborter(OdooPlugin):
            plugin_sdk_version = ">=1.0,<2.0"

            @property
            def name(self) -> str:
                return "aborter"

            def register(self, server: Any, context: Any) -> None:
                @server.tool()
                async def always_runs() -> str:
                    return "should never get here"

            async def pre_call(self, tool: str, arguments: dict, context: Any) -> None:
                raise PermissionError("security policy denies this call")

        reg = PluginRegistry()
        reg.register_plugin(_Aborter)
        server = FastMCP(name="test")
        gw = MagicMock()

        reg.activate(server, gw)
        install_plugin_middleware(server, reg, gw)

        # The tool call should fail because pre_call raised. FastMCP
        # converts tool exceptions into McpError; either form means
        # the tool didn't return its happy-path string.
        from mcp.shared.exceptions import McpError

        try:
            result = await server.call_tool("always_runs", {})
            # If FastMCP swallowed the error and returned an error
            # structure instead, verify it's the abort path.
            result_str = str(result)
            assert (
                "security policy denies this call" in result_str or "deny" in result_str
            )
        except (PermissionError, McpError):
            # Either propagation form is acceptable; what matters is
            # the call didn't return the success string.
            pass

    @pytest.mark.asyncio
    async def test_post_call_failure_does_not_break_tool(self) -> None:
        """A post_call that raises must not destroy the tool's result."""
        from mcp.server.fastmcp import FastMCP

        from odoo_mcp_gateway.core.plugin_middleware import (
            install_plugin_middleware,
        )

        class _BadPost(OdooPlugin):
            plugin_sdk_version = ">=1.0,<2.0"

            @property
            def name(self) -> str:
                return "badpost"

            def register(self, server: Any, context: Any) -> None:
                @server.tool()
                async def echo(text: str) -> str:
                    return f"echo:{text}"

            async def post_call(self, tool: str, result: Any, context: Any) -> Any:
                raise RuntimeError("post_call exploded")

        reg = PluginRegistry()
        reg.register_plugin(_BadPost)
        server = FastMCP(name="test")
        gw = MagicMock()

        reg.activate(server, gw)
        install_plugin_middleware(server, reg, gw)

        # The tool should STILL succeed (post_call exception swallowed).
        result = await server.call_tool("echo", {"text": "hi"})
        # FastMCP wraps the result; just verify it didn't raise.
        assert result is not None

    def test_install_is_idempotent(self) -> None:
        """install() twice on the same server must not double-wrap."""
        from mcp.server.fastmcp import FastMCP

        from odoo_mcp_gateway.core.plugin_middleware import (
            install_plugin_middleware,
        )

        reg = PluginRegistry()
        server = FastMCP(name="test")
        gw = MagicMock()

        install_plugin_middleware(server, reg, gw)
        first_call_tool = server.call_tool
        install_plugin_middleware(server, reg, gw)
        # Second install must NOT have re-wrapped.
        assert server.call_tool is first_call_tool


class TestSessionCloseDispatch:
    """audit-fix #4: on_session_close must fire when sessions evict."""

    @pytest.mark.asyncio
    async def test_session_close_fires_via_safe_dispatcher(self) -> None:
        from odoo_mcp_gateway.tools.auth import _safe_dispatch_session_close

        spy_plugin = _SpyPlugin()
        registry = PluginRegistry()
        registry.register_plugin(_SpyPlugin)
        # Replace the registry's instance with our explicit spy so
        # event capture is deterministic across the test.
        info = registry.get_plugin("spy")
        assert info is not None
        info.instance = spy_plugin

        gateway = MagicMock()
        gateway.plugin_registry = registry

        await _safe_dispatch_session_close(gateway, "5_db")

        assert ("on_session_close", ("5_db",)) in spy_plugin.events

    @pytest.mark.asyncio
    async def test_session_close_no_registry_is_silent(self) -> None:
        """Calling without a plugin_registry must not error."""
        from odoo_mcp_gateway.tools.auth import _safe_dispatch_session_close

        gateway = MagicMock(spec=[])  # no plugin_registry attr
        # Must not raise.
        await _safe_dispatch_session_close(gateway, "5_db")

    @pytest.mark.asyncio
    async def test_session_close_dispatcher_error_is_swallowed(self) -> None:
        """If the registry's dispatcher itself blows up, the caller
        sees no exception (the eviction path must complete)."""
        from odoo_mcp_gateway.tools.auth import _safe_dispatch_session_close

        bad_registry = MagicMock()
        bad_registry.dispatch_on_session_close = MagicMock(
            side_effect=RuntimeError("registry exploded")
        )
        gateway = MagicMock()
        gateway.plugin_registry = bad_registry

        # Must not raise.
        await _safe_dispatch_session_close(gateway, "5_db")
