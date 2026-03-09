"""Tests for the Odoo exception hierarchy."""

from __future__ import annotations

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


class TestOdooError:
    def test_base_message(self) -> None:
        exc = OdooError("something went wrong")
        assert str(exc) == "something went wrong"

    def test_base_code_none_by_default(self) -> None:
        exc = OdooError("oops")
        assert exc.code is None

    def test_base_code_set(self) -> None:
        exc = OdooError("oops", code="ERR001")
        assert exc.code == "ERR001"

    def test_is_exception(self) -> None:
        assert issubclass(OdooError, Exception)


class TestSubclasses:
    """Every concrete exception must inherit from OdooError."""

    SUBCLASSES = [
        OdooConnectionError,
        OdooAuthError,
        OdooAccessError,
        OdooValidationError,
        OdooUserError,
        OdooMissingError,
        OdooVersionError,
    ]

    def test_all_inherit_from_odoo_error(self) -> None:
        for cls in self.SUBCLASSES:
            assert issubclass(cls, OdooError), f"{cls.__name__} must inherit OdooError"

    def test_each_can_be_instantiated_with_message(self) -> None:
        for cls in self.SUBCLASSES:
            exc = cls("test message")
            assert str(exc) == "test message"

    def test_each_can_be_instantiated_with_code(self) -> None:
        for cls in self.SUBCLASSES:
            exc = cls("msg", code="C1")
            assert exc.code == "C1"

    def test_connection_error_str(self) -> None:
        exc = OdooConnectionError("cannot connect")
        assert str(exc) == "cannot connect"

    def test_auth_error_str(self) -> None:
        exc = OdooAuthError("bad credentials")
        assert str(exc) == "bad credentials"

    def test_version_error_str(self) -> None:
        exc = OdooVersionError("unsupported", code="V1")
        assert str(exc) == "unsupported"
        assert exc.code == "V1"
