"""Sales domain plugin: quotations, orders, pipeline."""

from __future__ import annotations

import re
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

_VALID_SALE_STATES = frozenset({"draft", "sent", "sale", "done", "cancel"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SalesPlugin(OdooPlugin):
    """Provides MCP tools for sales: quotations, orders, pipeline analysis."""

    @property
    def name(self) -> str:
        return "sales"

    @property
    def description(self) -> str:
        return "Sales tools: quotations, orders, pipeline analysis"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["sale"]

    @property
    def required_models(self) -> list[str]:
        return ["sale.order", "sale.order.line"]

    def register(self, server: FastMCP, context: Any) -> None:
        """Register sales tools on the MCP server."""

        @server.tool()
        async def get_my_quotations(
            state: str | None = None,
            limit: int = 20,
        ) -> dict[str, Any]:
            """Get quotations/orders where the current user is the salesperson.

            Args:
                state: Filter by state (draft, sent, sale, done, cancel)
                limit: Max records (default 20)
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "get_my_quotations")
            if gate_error:
                return {"error": gate_error}

            if state and state not in _VALID_SALE_STATES:
                return {"error": f"Invalid state: {state!r}"}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "sale.order", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                domain: list[Any] = [["user_id", "=", uid]]
                if state:
                    domain.append(["state", "=", state])

                records = await client.execute_kw(
                    "sale.order",
                    "search_read",
                    [domain],
                    {
                        "fields": [
                            "name",
                            "partner_id",
                            "date_order",
                            "amount_total",
                            "state",
                            "currency_id",
                        ],
                        "limit": min(max(limit, 1), 100),
                        "order": "date_order desc",
                    },
                )

                filtered = context.rbac.filter_response_fields(
                    records, "sale.order", user_groups, is_admin
                )
                if isinstance(filtered, list):
                    records = filtered

                return {"orders": records, "count": len(records)}
            except Exception as e:
                model_err = format_model_error("sale.order", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def get_order_details(order_id: int) -> dict[str, Any]:
            """Get full order details with lines, totals, and partner info.

            Args:
                order_id: The sale order ID
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            if order_id <= 0:
                return {"error": "order_id must be a positive integer"}

            gate_error = await check_security_gate(context, "get_order_details")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "sale.order", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                restriction_msg = context.restrictions.check_model_access(
                    "sale.order.line", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                # IDOR protection: scope to current user unless admin
                domain: list[Any] = [["id", "=", order_id]]
                if not is_admin:
                    domain.append(["user_id", "=", uid])

                orders = await client.execute_kw(
                    "sale.order",
                    "search_read",
                    [domain],
                    {
                        "fields": [
                            "name",
                            "partner_id",
                            "date_order",
                            "amount_untaxed",
                            "amount_tax",
                            "amount_total",
                            "state",
                            "currency_id",
                            "user_id",
                            "note",
                        ],
                        "limit": 1,
                    },
                )
                if not orders:
                    return {"error": "Order not found"}

                order = orders[0]

                # Fetch order lines
                lines = await client.execute_kw(
                    "sale.order.line",
                    "search_read",
                    [[["order_id", "=", order_id]]],
                    {
                        "fields": [
                            "product_id",
                            "name",
                            "product_uom_qty",
                            "price_unit",
                            "discount",
                            "price_subtotal",
                        ],
                        "limit": 500,
                    },
                )

                filtered_orders = context.rbac.filter_response_fields(
                    [order], "sale.order", user_groups, is_admin
                )
                if isinstance(filtered_orders, list):
                    order = filtered_orders[0]

                filtered_lines = context.rbac.filter_response_fields(
                    lines, "sale.order.line", user_groups, is_admin
                )
                if isinstance(filtered_lines, list):
                    lines = filtered_lines

                return {
                    "order": order,
                    "lines": lines,
                    "line_count": len(lines),
                }
            except Exception as e:
                model_err = format_model_error("sale.order", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def confirm_order(order_id: int) -> dict[str, Any]:
            """Confirm a quotation, turning it into a sale order.

            Args:
                order_id: The sale order ID to confirm (must be in draft/sent state)
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            if order_id <= 0:
                return {"error": "order_id must be a positive integer"}

            gate_error = await check_security_gate(context, "confirm_order")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "sale.order", "write", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                # Check method restriction for action_confirm
                method_msg = context.restrictions.check_method_access(
                    "sale.order", "action_confirm", is_admin
                )
                if isinstance(method_msg, str):
                    return {"error": method_msg}

                # IDOR protection: scope to current user unless admin
                domain: list[Any] = [["id", "=", order_id]]
                if not is_admin:
                    domain.append(["user_id", "=", uid])

                # Verify order exists and is in draft/sent state
                orders = await client.execute_kw(
                    "sale.order",
                    "search_read",
                    [domain],
                    {"fields": ["id", "name", "state"], "limit": 1},
                )
                if not orders:
                    return {"error": "Order not found"}

                order = orders[0]
                if order["state"] not in ("draft", "sent"):
                    return {
                        "error": (
                            f"Order is in '{order['state']}' state, "
                            "only draft or sent orders can be confirmed"
                        ),
                    }

                # Call action_confirm
                await client.execute_kw(
                    "sale.order",
                    "action_confirm",
                    [[order_id]],
                )
                return {
                    "status": "confirmed",
                    "order_id": order_id,
                    "order_name": order["name"],
                    "previous_state": order["state"],
                }
            except Exception as e:
                model_err = format_model_error("sale.order", e)
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def get_sales_summary(
            period: str | None = None,
        ) -> dict[str, Any]:
            """Get aggregated sales stats for the current user.

            Returns counts by state, totals by month, and top customers.

            Args:
                period: Filter period (YYYY-MM format, optional).
                    If omitted, returns all-time stats.
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            gate_error = await check_security_gate(context, "get_sales_summary")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    "sale.order", "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                domain: list[Any] = [["user_id", "=", uid]]
                if period:
                    domain.append(["date_order", ">=", f"{period}-01 00:00:00"])
                    domain.append(["date_order", "<", next_month(period)])

                orders = await client.execute_kw(
                    "sale.order",
                    "search_read",
                    [domain],
                    {
                        "fields": [
                            "state",
                            "amount_total",
                            "partner_id",
                            "date_order",
                        ],
                        "limit": 1000,
                    },
                )

                filtered = context.rbac.filter_response_fields(
                    orders, "sale.order", user_groups, is_admin
                )
                if isinstance(filtered, list):
                    orders = filtered

                # Aggregate by state
                by_state: dict[str, dict[str, Any]] = {}
                # Aggregate by customer
                by_customer: dict[str, float] = {}
                total_amount = 0.0

                for order in orders:
                    st = order.get("state", "unknown")
                    if st not in by_state:
                        by_state[st] = {"count": 0, "total": 0.0}
                    by_state[st]["count"] += 1
                    amount = order.get("amount_total", 0) or 0
                    by_state[st]["total"] += amount
                    total_amount += amount

                    partner = order.get("partner_id")
                    if isinstance(partner, list) and len(partner) > 1:
                        customer_name = partner[1]
                    else:
                        customer_name = "Unknown"
                    by_customer[customer_name] = (
                        by_customer.get(customer_name, 0) + amount
                    )

                # Top 5 customers by amount
                top_customers = sorted(
                    by_customer.items(), key=lambda x: x[1], reverse=True
                )[:5]

                return {
                    "total_orders": len(orders),
                    "total_amount": total_amount,
                    "by_state": by_state,
                    "top_customers": [
                        {"name": name, "total": total} for name, total in top_customers
                    ],
                }
            except Exception as e:
                model_err = format_model_error("sale.order", e)
                return {"error": model_err or context.sanitize_error(e)}
