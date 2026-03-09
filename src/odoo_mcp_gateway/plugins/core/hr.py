"""HR domain plugin: attendance, leave, and employee tools."""

from __future__ import annotations

import re
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
    next_month,
)

_VALID_LEAVE_STATES = frozenset({"draft", "confirm", "validate1", "validate", "refuse"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HRPlugin(OdooPlugin):
    """Provides MCP tools for HR operations: attendance, leave, employee profile."""

    @property
    def name(self) -> str:
        return "hr"

    @property
    def description(self) -> str:
        return "HR tools: attendance, leave requests, employee profile"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["hr", "hr_attendance", "hr_holidays"]

    @property
    def required_models(self) -> list[str]:
        return ["hr.employee", "hr.attendance", "hr.leave"]

    def register(self, server: FastMCP, context: Any) -> None:
        """Register HR tools on the MCP server."""

        @server.tool()
        async def check_in() -> dict[str, Any]:
            """Record attendance check-in for the current user.

            Finds the employee linked to the current user and creates
            an attendance record with check_in set to now.
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "check_in")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                # Check access to hr.employee (read to find employee)
                restriction_msg = context.restrictions.check_model_access(
                    "hr.employee", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                # Check access to hr.attendance (read + create)
                restriction_msg = context.restrictions.check_model_access(
                    "hr.attendance", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}
                restriction_msg = context.restrictions.check_model_access(
                    "hr.attendance", "create", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                employees = await client.execute_kw(
                    "hr.employee",
                    "search_read",
                    [[["user_id", "=", uid]]],
                    {"fields": ["id", "name"], "limit": 1},
                )
                if not employees:
                    return {"error": "No employee record found for current user"}

                emp = employees[0]

                # Check if already checked in (open attendance)
                open_att = await client.execute_kw(
                    "hr.attendance",
                    "search_read",
                    [
                        [
                            ["employee_id", "=", emp["id"]],
                            ["check_out", "=", False],
                        ]
                    ],
                    {"fields": ["id", "check_in"], "limit": 1},
                )
                if open_att:
                    return {
                        "error": "Already checked in",
                        "check_in_time": open_att[0].get("check_in"),
                        "attendance_id": open_att[0]["id"],
                    }

                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                values: dict[str, Any] = {
                    "employee_id": emp["id"],
                    "check_in": now,
                }
                sanitized = context.rbac.sanitize_write_values(
                    values, "hr.attendance", user_groups, is_admin
                )
                if isinstance(sanitized, dict):
                    values = sanitized

                att_id = await client.execute_kw(
                    "hr.attendance",
                    "create",
                    [values],
                )
                return {
                    "status": "checked_in",
                    "attendance_id": att_id,
                    "employee": emp["name"],
                    "check_in": now,
                }
            except Exception as e:
                model_err = format_model_error("hr.employee", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def check_out() -> dict[str, Any]:
            """Record attendance check-out for the current user.

            Finds the open attendance record and sets check_out to now.
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "check_out")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                # Check access to hr.employee (read to find employee)
                restriction_msg = context.restrictions.check_model_access(
                    "hr.employee", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                # Check access to hr.attendance (read + write)
                restriction_msg = context.restrictions.check_model_access(
                    "hr.attendance", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}
                restriction_msg = context.restrictions.check_model_access(
                    "hr.attendance", "write", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                employees = await client.execute_kw(
                    "hr.employee",
                    "search_read",
                    [[["user_id", "=", uid]]],
                    {"fields": ["id", "name"], "limit": 1},
                )
                if not employees:
                    return {"error": "No employee record found for current user"}

                emp = employees[0]

                open_att = await client.execute_kw(
                    "hr.attendance",
                    "search_read",
                    [
                        [
                            ["employee_id", "=", emp["id"]],
                            ["check_out", "=", False],
                        ]
                    ],
                    {"fields": ["id", "check_in"], "limit": 1},
                )
                if not open_att:
                    return {"error": "Not checked in \u2014 no open attendance record"}

                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                values = {"check_out": now}
                sanitized = context.rbac.sanitize_write_values(
                    values, "hr.attendance", user_groups, is_admin
                )
                if isinstance(sanitized, dict):
                    values = sanitized

                await client.execute_kw(
                    "hr.attendance",
                    "write",
                    [[open_att[0]["id"]], values],
                )
                return {
                    "status": "checked_out",
                    "attendance_id": open_att[0]["id"],
                    "employee": emp["name"],
                    "check_in": open_att[0].get("check_in"),
                    "check_out": now,
                }
            except Exception as e:
                model_err = format_model_error("hr.employee", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def get_my_attendance(
            limit: int = 10,
            month: str | None = None,
        ) -> dict[str, Any]:
            """Get attendance records for the current user.

            Args:
                limit: Max records to return (default 10)
                month: Filter by month (YYYY-MM format, optional)
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "get_my_attendance")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            if month and not re.match(r"^\d{4}-\d{2}$", month):
                return {"error": f"Invalid month format: {month!r}. Expected YYYY-MM."}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "hr.employee", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                restriction_msg = context.restrictions.check_model_access(
                    "hr.attendance", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                employees = await client.execute_kw(
                    "hr.employee",
                    "search_read",
                    [[["user_id", "=", uid]]],
                    {"fields": ["id"], "limit": 1},
                )
                if not employees:
                    return {"error": "No employee record found"}

                domain: list[Any] = [["employee_id", "=", employees[0]["id"]]]
                if month:
                    domain.append(["check_in", ">=", f"{month}-01 00:00:00"])
                    domain.append(["check_in", "<", next_month(month)])

                records = await client.execute_kw(
                    "hr.attendance",
                    "search_read",
                    [domain],
                    {
                        "fields": ["check_in", "check_out", "worked_hours"],
                        "limit": min(max(limit, 1), 100),
                        "order": "check_in desc",
                    },
                )

                filtered = context.rbac.filter_response_fields(
                    records, "hr.attendance", user_groups, is_admin
                )
                if isinstance(filtered, list):
                    records = filtered

                return {"records": records, "count": len(records)}
            except Exception as e:
                model_err = format_model_error("hr.employee", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def get_my_leaves(
            state: str | None = None,
            limit: int = 20,
        ) -> dict[str, Any]:
            """Get leave requests for the current user.

            Args:
                state: Filter by state (draft, confirm, validate, refuse)
                limit: Max records (default 20)
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "get_my_leaves")
            if gate_error:
                return {"error": gate_error}

            if state and state not in _VALID_LEAVE_STATES:
                return {"error": f"Invalid state: {state!r}"}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "hr.employee", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                restriction_msg = context.restrictions.check_model_access(
                    "hr.leave", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                employees = await client.execute_kw(
                    "hr.employee",
                    "search_read",
                    [[["user_id", "=", uid]]],
                    {"fields": ["id"], "limit": 1},
                )
                if not employees:
                    return {"error": "No employee record found"}

                domain: list[Any] = [["employee_id", "=", employees[0]["id"]]]
                if state:
                    domain.append(["state", "=", state])

                records = await client.execute_kw(
                    "hr.leave",
                    "search_read",
                    [domain],
                    {
                        "fields": [
                            "name",
                            "holiday_status_id",
                            "date_from",
                            "date_to",
                            "number_of_days",
                            "state",
                        ],
                        "limit": min(max(limit, 1), 100),
                        "order": "date_from desc",
                    },
                )

                filtered = context.rbac.filter_response_fields(
                    records, "hr.leave", user_groups, is_admin
                )
                if isinstance(filtered, list):
                    records = filtered

                return {"records": records, "count": len(records)}
            except Exception as e:
                model_err = format_model_error("hr.employee", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def request_leave(
            leave_type_id: int,
            date_from: str,
            date_to: str,
            reason: str = "",
        ) -> dict[str, Any]:
            """Submit a leave request.

            Args:
                leave_type_id: Leave type ID (e.g. annual leave, sick leave)
                date_from: Start date (YYYY-MM-DD)
                date_to: End date (YYYY-MM-DD)
                reason: Optional description
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "request_leave")
            if gate_error:
                return {"error": gate_error}

            if leave_type_id <= 0:
                return {"error": "leave_type_id must be a positive integer"}
            if not _DATE_RE.match(date_from):
                return {"error": f"Invalid date_from: {date_from!r}"}
            if not _DATE_RE.match(date_to):
                return {"error": f"Invalid date_to: {date_to!r}"}
            try:
                datetime.strptime(date_from, "%Y-%m-%d")
                datetime.strptime(date_to, "%Y-%m-%d")
            except ValueError:
                return {"error": "Invalid calendar date in date_from or date_to"}
            if date_from > date_to:
                return {"error": "date_from must not be after date_to"}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                # Need to read hr.employee to find the employee
                restriction_msg = context.restrictions.check_model_access(
                    "hr.employee", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                # Need to create hr.leave
                restriction_msg = context.restrictions.check_model_access(
                    "hr.leave", "create", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                employees = await client.execute_kw(
                    "hr.employee",
                    "search_read",
                    [[["user_id", "=", uid]]],
                    {"fields": ["id", "name"], "limit": 1},
                )
                if not employees:
                    return {"error": "No employee record found"}

                values: dict[str, Any] = {
                    "employee_id": employees[0]["id"],
                    "holiday_status_id": leave_type_id,
                    "date_from": f"{date_from} 00:00:00",
                    "date_to": f"{date_to} 23:59:59",
                }
                if reason:
                    values["name"] = reason

                sanitized = context.rbac.sanitize_write_values(
                    values, "hr.leave", user_groups, is_admin
                )
                if isinstance(sanitized, dict):
                    values = sanitized

                leave_id = await client.execute_kw(
                    "hr.leave",
                    "create",
                    [values],
                )
                return {
                    "status": "created",
                    "leave_id": leave_id,
                    "employee": employees[0]["name"],
                    "date_from": date_from,
                    "date_to": date_to,
                }
            except Exception as e:
                model_err = format_model_error("hr.employee", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def get_my_profile() -> dict[str, Any]:
            """Get the current user's employee profile."""
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "get_my_profile")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "hr.employee", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                records = await client.execute_kw(
                    "hr.employee",
                    "search_read",
                    [[["user_id", "=", uid]]],
                    {
                        "fields": [
                            "name",
                            "job_id",
                            "department_id",
                            "work_email",
                            "work_phone",
                            "parent_id",
                            "coach_id",
                            "work_location_id",
                        ],
                        "limit": 1,
                    },
                )
                if not records:
                    return {"error": "No employee profile found"}

                filtered = context.rbac.filter_response_fields(
                    records, "hr.employee", user_groups, is_admin
                )
                if isinstance(filtered, list):
                    records = filtered

                return {"profile": records[0]}
            except Exception as e:
                model_err = format_model_error("hr.employee", e)
                return {"error": model_err or context.sanitize_error(e)}
