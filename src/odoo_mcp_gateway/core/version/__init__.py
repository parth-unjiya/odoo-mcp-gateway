"""Odoo version detection and compatibility adapters."""

from odoo_mcp_gateway.core.version.adapters import VersionAdapter, get_adapter
from odoo_mcp_gateway.core.version.detector import VersionInfo, detect_version

__all__ = [
    "VersionAdapter",
    "VersionInfo",
    "detect_version",
    "get_adapter",
]
