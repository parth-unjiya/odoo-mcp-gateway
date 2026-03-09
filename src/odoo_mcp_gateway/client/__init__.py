"""Odoo RPC client layer."""

from odoo_mcp_gateway.client.base import AuthResult, OdooClientBase
from odoo_mcp_gateway.client.exceptions import (
    OdooAccessError,
    OdooAuthError,
    OdooConnectionError,
    OdooError,
    OdooMissingError,
    OdooUserError,
    OdooValidationError,
    OdooVersionError,
)
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient

__all__ = [
    "AuthResult",
    "JsonRpcClient",
    "OdooAccessError",
    "OdooAuthError",
    "OdooClientBase",
    "OdooConnectionError",
    "OdooError",
    "OdooMissingError",
    "OdooUserError",
    "OdooValidationError",
    "OdooVersionError",
    "XmlRpcClient",
]
