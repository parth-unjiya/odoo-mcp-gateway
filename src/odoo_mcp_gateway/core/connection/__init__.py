"""Connection pooling and circuit-breaker logic."""

from odoo_mcp_gateway.core.connection.manager import CircuitState, ConnectionManager

__all__ = [
    "CircuitState",
    "ConnectionManager",
]
