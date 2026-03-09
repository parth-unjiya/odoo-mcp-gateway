"""Exception hierarchy for Odoo client errors."""

from __future__ import annotations


class OdooError(Exception):
    """Base exception for all Odoo client errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


class OdooConnectionError(OdooError):
    """Cannot reach the Odoo server."""


class OdooAuthError(OdooError):
    """Invalid credentials or expired session."""


class OdooAccessError(OdooError):
    """Access denied by ir.model.access or ir.rule."""


class OdooValidationError(OdooError):
    """Field validation failure."""


class OdooUserError(OdooError):
    """Business-logic error raised by Odoo."""


class OdooMissingError(OdooError):
    """Record not found."""


class OdooVersionError(OdooError):
    """Unsupported Odoo version."""
