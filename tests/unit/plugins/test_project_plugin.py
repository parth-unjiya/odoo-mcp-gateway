"""Tests for the Project domain plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.plugins.core.project import ProjectPlugin

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def mock_context():
    """Create a mock gateway context with auth manager and client."""
    ctx = MagicMock()
    client = AsyncMock()
    auth_mgr = MagicMock()
    auth_mgr.get_active_client.return_value = client
    auth_mgr.auth_result = MagicMock(uid=42)
    ctx.auth_managers = {"session": auth_mgr}
    ctx.sanitize_error = lambda exc: str(exc)
    # Disable security_gate sub-checks so tests focus on plugin logic
    ctx.rate_limiter = None
    ctx.audit_logger = None
    ctx.rbac.check_tool_access.return_value = None
    return ctx, client


@pytest.fixture
def unauth_context():
    """Create a mock context with no auth managers (unauthenticated)."""
    ctx = MagicMock()
    ctx.auth_managers = {}
    ctx.rate_limiter = None
    ctx.audit_logger = None
    return ctx


@pytest.fixture
def tools(mock_context):
    """Register Project plugin and capture all tool functions."""
    ctx, _ = mock_context
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = ProjectPlugin()
    plugin.register(server, ctx)
    return captured


@pytest.fixture
def unauth_tools(unauth_context):
    """Register Project plugin with unauthenticated context."""
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = ProjectPlugin()
    plugin.register(server, unauth_context)
    return captured


# ── Plugin metadata ──────────────────────────────────────────────


class TestProjectPluginMetadata:
    def test_name(self):
        plugin = ProjectPlugin()
        assert plugin.name == "project"

    def test_description(self):
        plugin = ProjectPlugin()
        desc = plugin.description.lower()
        assert "task" in desc or "project" in desc

    def test_required_odoo_modules(self):
        plugin = ProjectPlugin()
        assert "project" in plugin.required_odoo_modules

    def test_required_models(self):
        plugin = ProjectPlugin()
        assert "project.project" in plugin.required_models
        assert "project.task" in plugin.required_models


# ── get_my_tasks tests ───────────────────────────────────────────


class TestGetMyTasks:
    async def test_returns_tasks(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = [
            {
                "name": "Fix bug",
                "project_id": [1, "Website"],
                "stage_id": [2, "In Progress"],
                "state": "01_in_progress",
                "priority": "1",
                "date_deadline": "2025-04-01",
                "tag_ids": [1, 2],
            },
        ]
        result = await tools["get_my_tasks"]()
        assert result["count"] == 1
        assert result["tasks"][0]["name"] == "Fix bug"

    async def test_with_project_filter(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_my_tasks"](project_id=5)
        assert result["count"] == 0
        # Verify domain included project filter
        call_args = client.execute_kw.call_args
        domain = call_args[0][2][0]
        assert ["project_id", "=", 5] in domain

    async def test_with_state_filter(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_my_tasks"](state="1_done")
        assert result["count"] == 0
        call_args = client.execute_kw.call_args
        domain = call_args[0][2][0]
        assert ["state", "=", "1_done"] in domain

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["get_my_tasks"]()
        assert result["error"] == "Not authenticated"

    async def test_handles_exception(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception("RPC error")
        result = await tools["get_my_tasks"]()
        assert "RPC error" in result["error"]

    async def test_limit_capped(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = []
        await tools["get_my_tasks"](limit=500)
        call_args = client.execute_kw.call_args
        kwargs = call_args[0][3]
        assert kwargs["limit"] == 100


# ── get_project_summary tests ────────────────────────────────────


class TestGetProjectSummary:
    async def test_returns_stats(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            # project search
            [
                {
                    "id": 1,
                    "name": "Website Redesign",
                    "user_id": [42, "Admin"],
                    "partner_id": False,
                    "date_start": "2025-01-01",
                    "date": "2025-12-31",
                },
            ],
            # tasks search
            [
                {
                    "name": "Task 1",
                    "stage_id": [1, "To Do"],
                    "state": "01_in_progress",
                    "date_deadline": "2020-01-01",  # overdue
                    "user_ids": [42],
                },
                {
                    "name": "Task 2",
                    "stage_id": [2, "In Progress"],
                    "state": "01_in_progress",
                    "date_deadline": "2099-01-01",
                    "user_ids": [42],
                },
                {
                    "name": "Task 3",
                    "stage_id": [1, "To Do"],
                    "state": "01_in_progress",
                    "date_deadline": False,
                    "user_ids": [42],
                },
            ],
        ]
        result = await tools["get_project_summary"](project_id=1)
        assert result["project"]["name"] == "Website Redesign"
        assert result["total_tasks"] == 3
        assert result["tasks_by_stage"]["To Do"] == 2
        assert result["tasks_by_stage"]["In Progress"] == 1
        assert result["overdue_tasks"] == 1

    async def test_project_not_found(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],  # no project
        ]
        result = await tools["get_project_summary"](project_id=999)
        assert result["error"] == "Project not found"

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["get_project_summary"](project_id=1)
        assert result["error"] == "Not authenticated"

    async def test_handles_exception(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Timeout")
        result = await tools["get_project_summary"](project_id=1)
        assert "Timeout" in result["error"]


# ── update_task_stage tests ──────────────────────────────────────


class TestUpdateTaskStage:
    async def test_success(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 10, "name": "Fix bug", "stage_id": [1, "To Do"]}],
            True,
        ]
        result = await tools["update_task_stage"](task_id=10, stage_id=2)
        assert result["status"] == "updated"
        assert result["task_id"] == 10
        assert result["task_name"] == "Fix bug"
        assert result["old_stage"] == [1, "To Do"]
        assert result["new_stage_id"] == 2

    async def test_task_not_found(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],
        ]
        result = await tools["update_task_stage"](task_id=999, stage_id=2)
        assert result["error"] == "Task not found"

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["update_task_stage"](task_id=1, stage_id=2)
        assert result["error"] == "Not authenticated"

    async def test_handles_exception(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Access denied")
        result = await tools["update_task_stage"](task_id=1, stage_id=2)
        assert "Access denied" in result["error"]
