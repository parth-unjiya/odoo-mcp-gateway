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
    ctx.restrictions.check_field_write.return_value = None
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


# ── IDOR Protection tests ──────────────────────────────────────


def _make_idor_context(*, uid: int, is_admin: bool):
    """Create a mock context with explicit admin/non-admin settings."""
    ctx = MagicMock()
    client = AsyncMock()
    auth_mgr = MagicMock()
    auth_mgr.get_active_client.return_value = client
    auth_mgr.auth_result = MagicMock(
        uid=uid,
        is_admin=is_admin,
        groups=["base.group_system" if is_admin else "base.group_user"],
    )
    ctx.auth_managers = {"session": auth_mgr}
    ctx.sanitize_error = lambda exc: str(exc)
    ctx.rate_limiter = None
    ctx.audit_logger = None
    ctx.rbac.check_tool_access.return_value = None
    ctx.restrictions.check_field_write.return_value = None
    return ctx, client


def _register_project(ctx):
    """Register Project plugin on a mock server and return captured tools."""
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
def nonadmin_project_context():
    return _make_idor_context(uid=42, is_admin=False)


@pytest.fixture
def admin_project_context():
    return _make_idor_context(uid=1, is_admin=True)


@pytest.fixture
def nonadmin_project_tools(nonadmin_project_context):
    ctx, _ = nonadmin_project_context
    return _register_project(ctx)


@pytest.fixture
def admin_project_tools(admin_project_context):
    ctx, _ = admin_project_context
    return _register_project(ctx)


class TestUpdateTaskStageAclScope:
    """UAT HIGH-1 (parity with helpdesk) — defer task visibility to ir.rule.

    The previous implementation clamped non-admin callers to ``user_ids in
    [uid]``, which is strictly narrower than Odoo's row-level ACL for
    ``project.task``. A project_manager who could read a task via
    ``search_read`` (because their group's ir.rule grants project-wide
    visibility) but did not have it assigned got "Task not found" — a
    misleading 404 for an operation the role was authorised to perform.
    The fix removes the clamp and trusts Odoo's ir.rule.

    Note: ``get_project_summary`` keeps its scoping (see the dedicated
    summary tests below). Only the stage-transition path was flagged as
    narrower than the ACL surface.
    """

    async def test_nonadmin_update_task_stage_no_user_ids_clamp(
        self, nonadmin_project_tools, nonadmin_project_context
    ):
        """Search domain must not narrow below ``["id", "=", task_id]``."""
        _, client = nonadmin_project_context
        client.execute_kw.side_effect = [
            [{"id": 10, "name": "Fix bug", "stage_id": [1, "To Do"]}],
            True,
        ]
        await nonadmin_project_tools["update_task_stage"](task_id=10, stage_id=2)
        call_args = client.execute_kw.call_args_list[0]
        domain = call_args[0][2][0]
        assert all(
            not (isinstance(d, list) and d[0] == "user_ids") for d in domain
        )
        assert ["id", "=", 10] in domain

    async def test_admin_update_task_stage_uses_id_only_domain(
        self, admin_project_tools, admin_project_context
    ):
        _, client = admin_project_context
        client.execute_kw.side_effect = [
            [{"id": 10, "name": "Fix bug", "stage_id": [1, "To Do"]}],
            True,
        ]
        await admin_project_tools["update_task_stage"](task_id=10, stage_id=2)
        call_args = client.execute_kw.call_args_list[0]
        domain = call_args[0][2][0]
        assert ["id", "=", 10] in domain
        user_ids_entries = [
            d for d in domain if isinstance(d, list) and d[0] == "user_ids"
        ]
        assert user_ids_entries == []

    async def test_manager_can_update_task_not_assigned_to_them(
        self, nonadmin_project_tools, nonadmin_project_context
    ):
        """A project_manager who can read a task via ir.rule can move it."""
        _, client = nonadmin_project_context
        client.execute_kw.side_effect = [
            [{"id": 77, "name": "Other person's task", "stage_id": [1, "To Do"]}],
            True,
        ]
        result = await nonadmin_project_tools["update_task_stage"](
            task_id=77, stage_id=4
        )
        assert result.get("status") == "updated"
        assert result.get("task_id") == 77
        assert result.get("new_stage_id") == 4

    async def test_task_stage_write_acl_error_surfaces_verbatim(
        self, nonadmin_project_tools, nonadmin_project_context
    ):
        _, client = nonadmin_project_context
        client.execute_kw.side_effect = [
            [{"id": 80, "name": "Read OK", "stage_id": [1, "To Do"]}],
            Exception("AccessError: write denied"),
        ]
        result = await nonadmin_project_tools["update_task_stage"](
            task_id=80, stage_id=4
        )
        assert result.get("error") is not None
        assert "Task not found" not in result["error"]

    async def test_nonadmin_get_project_summary_scopes_project_and_tasks(
        self, nonadmin_project_tools, nonadmin_project_context
    ):
        """Non-admin get_project_summary scopes BOTH project + task queries.

        The project query must include visibility scoping (manager OR
        employees-visible) and the task query must include the user_ids
        filter so the caller only sees stats for tasks they're assigned to.
        """
        _, client = nonadmin_project_context
        client.execute_kw.side_effect = [
            # Project visibility check passes — caller is the manager.
            [
                {
                    "id": 1,
                    "name": "Internal Tooling",
                    "user_id": [42, "Bob"],
                    "partner_id": False,
                    "date_start": "2026-01-01",
                    "date": "2026-12-31",
                },
            ],
            # Task stats for tasks assigned to the caller.
            [
                {
                    "name": "Task A",
                    "stage_id": [1, "To Do"],
                    "state": "01_in_progress",
                    "date_deadline": False,
                    "user_ids": [42],
                },
            ],
        ]
        result = await nonadmin_project_tools["get_project_summary"](project_id=1)

        assert result["project"]["name"] == "Internal Tooling"
        assert result["total_tasks"] == 1

        # Project query must scope visibility for non-admin callers.
        project_call = client.execute_kw.call_args_list[0]
        project_domain = project_call[0][2][0]
        assert ["id", "=", 1] in project_domain
        assert ["user_id", "=", 42] in project_domain
        assert ["privacy_visibility", "=", "employees"] in project_domain

        # Task query must include the user_ids scoping clause.
        task_call = client.execute_kw.call_args_list[1]
        task_domain = task_call[0][2][0]
        assert ["project_id", "=", 1] in task_domain
        assert ["user_ids", "in", [42]] in task_domain

    async def test_admin_get_project_summary_does_not_scope(
        self, admin_project_tools, admin_project_context
    ):
        """Admin get_project_summary must NOT scope project or task queries."""
        _, client = admin_project_context
        client.execute_kw.side_effect = [
            [
                {
                    "id": 1,
                    "name": "Customer Portal",
                    "user_id": [99, "Alice"],
                    "partner_id": False,
                    "date_start": "2026-01-01",
                    "date": "2026-12-31",
                },
            ],
            [],  # no tasks
        ]
        await admin_project_tools["get_project_summary"](project_id=1)

        # Project query: only the id constraint, no visibility scoping.
        project_domain = client.execute_kw.call_args_list[0][0][2][0]
        assert project_domain == [["id", "=", 1]]

        # Task query: only the project_id constraint, no user_ids scoping.
        task_domain = client.execute_kw.call_args_list[1][0][2][0]
        assert ["project_id", "=", 1] in task_domain
        user_ids_clauses = [
            d for d in task_domain if isinstance(d, list) and d[0] == "user_ids"
        ]
        assert user_ids_clauses == []

    async def test_nonadmin_get_project_summary_hidden_project_returns_not_found(
        self, nonadmin_project_tools, nonadmin_project_context
    ):
        """Non-admin caller without project visibility gets a 'not found' error.

        The scoped project query returns no rows, so the call surfaces
        ``Project not found`` rather than leaking the project's existence.
        """
        _, client = nonadmin_project_context
        # First call (scoped project lookup) returns empty -> hidden from caller.
        client.execute_kw.side_effect = [[]]
        result = await nonadmin_project_tools["get_project_summary"](project_id=1)
        assert result["error"] == "Project not found"
