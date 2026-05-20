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
