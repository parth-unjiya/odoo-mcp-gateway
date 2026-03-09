"""Smoke tests for create_server() wiring.

Verifies that the server factory produces a FastMCP instance with
all expected tools registered, without needing a live Odoo connection.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.server import create_server


def _make_settings(config_dir: str) -> Settings:
    """Build a Settings object pointing at a temp config directory."""
    return Settings(
        odoo_url="http://localhost:8069",
        odoo_db="testdb",
        config_dir=config_dir,
    )


class TestCreateServer:
    """Verify create_server() returns a properly wired FastMCP instance."""

    def test_returns_fastmcp_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _make_settings(tmpdir)
            server = create_server(settings)
            assert isinstance(server, FastMCP)

    def test_server_has_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _make_settings(tmpdir)
            server = create_server(settings)
            assert server.name == "odoo-mcp-gateway"

    def test_auth_tools_registered(self) -> None:
        """The login tool must be present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _make_settings(tmpdir)
            server = create_server(settings)
            tool_names = set(server._tool_manager._tools.keys())
            assert "login" in tool_names

    def test_schema_tools_registered(self) -> None:
        """Schema introspection tools must be present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _make_settings(tmpdir)
            server = create_server(settings)
            tool_names = set(server._tool_manager._tools.keys())
            assert "list_models" in tool_names
            assert "get_model_fields" in tool_names

    def test_crud_tools_registered(self) -> None:
        """All CRUD tools must be present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _make_settings(tmpdir)
            server = create_server(settings)
            tool_names = set(server._tool_manager._tools.keys())
            expected = {
                "search_read",
                "get_record",
                "search_count",
                "create_record",
                "update_record",
                "delete_record",
                "read_group",
                "execute_method",
            }
            assert expected.issubset(tool_names), (
                f"Missing CRUD tools: {expected - tool_names}"
            )

    def test_plugin_tools_registered(self) -> None:
        """Domain plugin tools (HR, Sales, etc.) should also be registered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _make_settings(tmpdir)
            server = create_server(settings)
            tool_names = set(server._tool_manager._tools.keys())
            # HR plugin tools
            assert "check_in" in tool_names
            assert "check_out" in tool_names
            # Sales plugin tools
            assert "get_my_quotations" in tool_names
            assert "confirm_order" in tool_names

    def test_server_with_restrictions_yaml(self) -> None:
        """Providing a restrictions.yaml should not break server creation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            restrictions_path = Path(tmpdir) / "restrictions.yaml"
            restrictions_path.write_text(
                "always_blocked:\n  - ir.config_parameter\nadmin_only:\n  - ir.model\n",
                encoding="utf-8",
            )
            settings = _make_settings(tmpdir)
            server = create_server(settings)
            assert isinstance(server, FastMCP)

    def test_no_crash_with_empty_config_dir(self) -> None:
        """An empty config dir should use defaults without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _make_settings(tmpdir)
            server = create_server(settings)
            assert server is not None
