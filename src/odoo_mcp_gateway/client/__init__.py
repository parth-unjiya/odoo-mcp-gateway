"""Odoo RPC client layer."""

from odoo_mcp_gateway.client.base import AuthResult, Credential, OdooClientBase
from odoo_mcp_gateway.client.exceptions import (
    OdooAccessError,
    OdooAuthError,
    OdooConnectionError,
    OdooError,
    OdooMissingError,
    OdooSessionExpiredError,
    OdooUserError,
    OdooValidationError,
    OdooVersionError,
)
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient

__all__ = [
    "AuthResult",
    "Credential",
    "JsonRpcClient",
    "OdooAccessError",
    "OdooAuthError",
    "OdooClientBase",
    "OdooConnectionError",
    "OdooError",
    "OdooMissingError",
    "OdooSessionExpiredError",
    "OdooUserError",
    "OdooValidationError",
    "OdooVersionError",
    "XmlRpcClient",
]
