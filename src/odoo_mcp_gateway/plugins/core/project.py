"""Project domain plugin: task management and project overview."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.plugins.base import OdooPlugin
from odoo_mcp_gateway.plugins.core.helpers import (
    check_security_gate,
    format_model_error,
    get_auth_info,
    get_client,
    get_uid,
)

_VALID_TASK_STATES = frozenset(
    {
        "01_in_progress",
        "02_changes_requested",
        "03_approved",
        "1_done",
        "1_canceled",
        "04_waiting_normal",
    }
)


class ProjectPlugin(OdooPlugin):
    """Provides MCP tools for project management: tasks, stages, summaries."""

    @property
    def name(self) -> str:
        return "project"

    @property
    def description(self) -> str:
        return "Project tools: tasks, milestones, project summaries"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["project"]

    @property
    def required_models(self) -> list[str]:
        return ["project.project", "project.task"]

    def register(self, server: FastMCP, context: Any) -> None:
        """Register project tools on the MCP server."""

        @server.tool()
        async def get_my_tasks(
            state: str | None = None,
            project_id: int | None = None,
            limit: int = 20,
        ) -> dict[str, Any]:
            """Get tasks assigned to the current user.

            Args:
                state: Filter by task state (e.g. 01_in_progress, 1_done)
                project_id: Filter by project ID
                limit: Max records (default 20)
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "get_my_tasks")
            if gate_error:
                return {"error": gate_error}

            if state and state not in _VALID_TASK_STATES:
                return {"error": f"Invalid state: {state!r}"}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "project.task", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                domain: list[Any] = [["user_ids", "in", [uid]]]
                if state:
                    domain.append(["state", "=", state])
                if project_id is not None:
                    if project_id <= 0:
                        return {"error": "project_id must be a positive integer"}
                    domain.append(["project_id", "=", project_id])

                records = await client.execute_kw(
                    "project.task",
                    "search_read",
                    [domain],
                    {
                        "fields": [
                            "name",
                            "project_id",
                            "stage_id",
                            "state",
                            "priority",
                            "date_deadline",
                            "tag_ids",
                        ],
                        "limit": min(max(limit, 1), 100),
                        "order": "priority desc, date_deadline asc",
                    },
                )

                filtered = context.rbac.filter_response_fields(
                    records, "project.task", user_groups, is_admin
                )
                if isinstance(filtered, list):
                    records = filtered

                return {"tasks": records, "count": len(records)}
            except Exception as e:
                model_err = format_model_error("project.task", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def get_project_summary(project_id: int) -> dict[str, Any]:
            """Get project stats: task counts by stage, overdue, recent activity.

            Args:
                project_id: The project ID to summarize
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            if project_id <= 0:
                return {"error": "project_id must be a positive integer"}

            gate_error = await check_security_gate(context, "get_project_summary")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "project.project", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                restriction_msg = context.restrictions.check_model_access(
                    "project.task", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                # Fetch project info
                projects = await client.execute_kw(
                    "project.project",
                    "search_read",
                    [[["id", "=", project_id]]],
                    {
                        "fields": [
                            "name",
                            "user_id",
                            "partner_id",
                            "date_start",
                            "date",
                        ],
                        "limit": 1,
                    },
                )
                if not projects:
                    return {"error": "Project not found"}

                project = projects[0]

                filtered_projects = context.rbac.filter_response_fields(
                    [project], "project.project", user_groups, is_admin
                )
                if isinstance(filtered_projects, list):
                    project = filtered_projects[0]

                # Get tasks for the project with stage info
                tasks = await client.execute_kw(
                    "project.task",
                    "search_read",
                    [[["project_id", "=", project_id]]],
                    {
                        "fields": [
                            "name",
                            "stage_id",
                            "state",
                            "date_deadline",
                            "user_ids",
                        ],
                        "limit": 1000,
                    },
                )

                filtered_tasks = context.rbac.filter_response_fields(
                    tasks, "project.task", user_groups, is_admin
                )
                if isinstance(filtered_tasks, list):
                    tasks = filtered_tasks

                # Build stage counts
                stage_counts: dict[str, int] = {}
                overdue_count = 0

                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                for task in tasks:
                    stage_name = task.get("stage_id", [False, "No Stage"])
                    if isinstance(stage_name, list) and len(stage_name) > 1:
                        stage_label = stage_name[1]
                    else:
                        stage_label = "No Stage"
                    stage_counts[stage_label] = stage_counts.get(stage_label, 0) + 1

                    deadline = task.get("date_deadline")
                    if deadline and deadline < now_str:
                        overdue_count += 1

                return {
                    "project": {
                        "id": project["id"],
                        "name": project["name"],
                    },
                    "total_tasks": len(tasks),
                    "tasks_by_stage": stage_counts,
                    "overdue_tasks": overdue_count,
                }
            except Exception as e:
                model_err = format_model_error("project.task", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def update_task_stage(task_id: int, stage_id: int) -> dict[str, Any]:
            """Move a task to a different stage.

            Args:
                task_id: The task ID to update
                stage_id: The target stage ID
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            if task_id <= 0:
                return {"error": "task_id must be a positive integer"}
            if stage_id <= 0:
                return {"error": "stage_id must be a positive integer"}

            gate_error = await check_security_gate(context, "update_task_stage")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "project.task", "write", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                # IDOR protection: scope to current user unless admin
                domain: list[Any] = [["id", "=", task_id]]
                if not is_admin:
                    domain.append(["user_ids", "in", [uid]])

                # Verify task exists and belongs to user
                tasks = await client.execute_kw(
                    "project.task",
                    "search_read",
                    [domain],
                    {"fields": ["id", "name", "stage_id"], "limit": 1},
                )
                if not tasks:
                    return {"error": "Task not found"}

                task = tasks[0]
                old_stage = task.get("stage_id")

                values = {"stage_id": stage_id}
                sanitized = context.rbac.sanitize_write_values(
                    values, "project.task", user_groups, is_admin
                )
                if isinstance(sanitized, dict):
                    values = sanitized

                await client.execute_kw(
                    "project.task",
                    "write",
                    [[task_id], values],
                )
                return {
                    "status": "updated",
                    "task_id": task_id,
                    "task_name": task["name"],
                    "old_stage": old_stage,
                    "new_stage_id": stage_id,
                }
            except Exception as e:
                model_err = format_model_error("project.task", e)
                return {"error": model_err or context.sanitize_error(e)}
