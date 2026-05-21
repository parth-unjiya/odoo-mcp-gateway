"""Tests for the error sanitizer."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer


@pytest.fixture()
def sanitizer() -> ErrorSanitizer:
    return ErrorSanitizer()


# ── File path stripping ────────────────────────────────────────────


class TestStripPaths:
    def test_strip_python_path(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Error in /home/user/odoo/addons/sale/models/sale.py:42"
        result = sanitizer.sanitize(msg)
        assert "/home/user" not in result
        assert ".py" not in result

    def test_strip_multiple_paths(self, sanitizer: ErrorSanitizer) -> None:
        msg = "File /a/b.py, line 10, in /c/d.py"
        result = sanitizer.sanitize(msg)
        assert "/a/b.py" not in result
        assert "/c/d.py" not in result


# ── SQL stripping ──────────────────────────────────────────────────


class TestStripSQL:
    def test_strip_select(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Query failed: SELECT * FROM res_partner WHERE id = 1"
        result = sanitizer.sanitize(msg)
        assert "SELECT" not in result

    def test_strip_insert(self, sanitizer: ErrorSanitizer) -> None:
        msg = "INSERT INTO res_partner (name) VALUES ('test') failed"
        result = sanitizer.sanitize(msg)
        assert "INSERT" not in result

    def test_strip_update(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Error: UPDATE res_partner SET name='x' WHERE id=1"
        result = sanitizer.sanitize(msg)
        assert "UPDATE" not in result

    def test_strip_delete(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Failed: DELETE FROM res_partner WHERE id = 1"
        result = sanitizer.sanitize(msg)
        assert "DELETE FROM" not in result

    def test_strip_drop(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Error: DROP TABLE res_partner"
        result = sanitizer.sanitize(msg)
        assert "DROP TABLE" not in result


# ── Traceback stripping ────────────────────────────────────────────


class TestStripTracebacks:
    def test_strip_traceback(self, sanitizer: ErrorSanitizer) -> None:
        msg = (
            "Traceback (most recent call last):\n"
            '  File "/odoo/models.py", line 42\n'
            "    raise ValueError()\n"
            "ValueError: bad value"
        )
        result = sanitizer.sanitize(msg)
        assert "Traceback" not in result
        assert "most recent call" not in result

    def test_strip_nested_traceback(self, sanitizer: ErrorSanitizer) -> None:
        msg = (
            "Something went wrong\n"
            "Traceback (most recent call last):\n"
            '  File "test.py", line 1\n'
            "Error happened"
        )
        result = sanitizer.sanitize(msg)
        assert "Traceback" not in result


# ── Database name stripping ────────────────────────────────────────


class TestStripDatabaseNames:
    def test_strip_database_reference(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Connection to database 'my_production_db' failed"
        result = sanitizer.sanitize(msg)
        assert "my_production_db" not in result

    def test_strip_db_equals(self, sanitizer: ErrorSanitizer) -> None:
        msg = "db=my_db connection timeout"
        result = sanitizer.sanitize(msg)
        assert "my_db" not in result

    def test_strip_database_colon(self, sanitizer: ErrorSanitizer) -> None:
        msg = 'database: "prod_odoo" unreachable'
        result = sanitizer.sanitize(msg)
        assert "prod_odoo" not in result


# ── Preserve user-friendly parts ───────────────────────────────────


class TestPreserveUserFriendly:
    def test_preserve_simple_message(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Record not found"
        result = sanitizer.sanitize(msg)
        assert result == "Record not found"

    def test_empty_message_returns_default(self, sanitizer: ErrorSanitizer) -> None:
        result = sanitizer.sanitize("")
        assert "unexpected error" in result.lower()

    def test_none_like_empty(self, sanitizer: ErrorSanitizer) -> None:
        result = sanitizer.sanitize("")
        assert result != ""


# ── Error map ──────────────────────────────────────────────────────


class TestErrorMap:
    def test_access_error_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.AccessError: not allowed to do that"
        result = sanitizer.sanitize(msg)
        assert "Access denied" in result

    def test_access_denied_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.AccessDenied: wrong password"
        result = sanitizer.sanitize(msg)
        assert "Authentication failed" in result

    def test_validation_error_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.ValidationError: field is required"
        result = sanitizer.sanitize(msg)
        assert "Validation error" in result

    def test_user_error_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.UserError: cannot confirm"
        result = sanitizer.sanitize(msg)
        assert "Operation failed" in result

    def test_missing_error_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.MissingError: record deleted"
        result = sanitizer.sanitize(msg)
        assert "Record not found" in result

    def test_error_map_with_internals_in_remainder(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        msg = "odoo.exceptions.AccessError: SELECT * FROM res_partner WHERE id = 1"
        result = sanitizer.sanitize(msg)
        assert "SELECT" not in result
        assert "Access denied" in result


# ── sanitize_exception ─────────────────────────────────────────────


class TestSanitizeException:
    def test_generic_exception(self, sanitizer: ErrorSanitizer) -> None:
        exc = RuntimeError("Something failed at /home/user/test.py:42")
        result = sanitizer.sanitize_exception(exc)
        assert "/home/user" not in result

    def test_value_error(self, sanitizer: ErrorSanitizer) -> None:
        exc = ValueError("Invalid input")
        result = sanitizer.sanitize_exception(exc)
        assert "Invalid input" in result

    def test_exception_with_empty_module(self, sanitizer: ErrorSanitizer) -> None:
        """When type(exc).__module__ is empty/None, fall back to qualname only."""

        class _NoModuleError(Exception):
            pass

        exc = _NoModuleError("something broke")
        # Temporarily set __module__ to empty string
        original_module = _NoModuleError.__module__
        try:
            _NoModuleError.__module__ = ""
            result = sanitizer.sanitize_exception(exc)
            # Should still sanitize the message via the generic path
            assert "something broke" in result
        finally:
            _NoModuleError.__module__ = original_module

    def test_known_odoo_exception_by_full_name(self, sanitizer: ErrorSanitizer) -> None:
        """sanitize_exception maps a known Odoo exception by fully-qualified name."""
        # Dynamically create a class whose module+qualname matches an _ERROR_MAP key
        access_error_cls = type("AccessError", (Exception,), {})
        access_error_cls.__module__ = "odoo.exceptions"
        access_error_cls.__qualname__ = "AccessError"
        exc = access_error_cls("You cannot do this operation")
        result = sanitizer.sanitize_exception(exc)
        assert "Access denied" in result
        assert "You cannot do this operation" in result

    def test_known_odoo_exception_with_long_message(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        """Known Odoo exception with message > 200 chars returns friendly only."""
        long_msg = "x" * 250
        user_error_cls = type("UserError", (Exception,), {})
        user_error_cls.__module__ = "odoo.exceptions"
        user_error_cls.__qualname__ = "UserError"
        exc = user_error_cls(long_msg)
        result = sanitizer.sanitize_exception(exc)
        assert result == "Operation failed"
        assert long_msg not in result

    def test_known_odoo_exception_with_internal_details(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        """Known Odoo exception with internals in message returns friendly only."""
        msg_with_path = "Error at /home/user/odoo/addons/sale.py:42"
        missing_error_cls = type("MissingError", (Exception,), {})
        missing_error_cls.__module__ = "odoo.exceptions"
        missing_error_cls.__qualname__ = "MissingError"
        exc = missing_error_cls(msg_with_path)
        result = sanitizer.sanitize_exception(exc)
        assert result == "Record not found"
        assert "/home/user" not in result

    def test_empty_exception_message(self, sanitizer: ErrorSanitizer) -> None:
        """Exception with empty string message gets sanitized to default."""
        exc = RuntimeError("")
        result = sanitizer.sanitize_exception(exc)
        assert "unexpected error" in result.lower()

    def test_exception_with_url_in_message(self, sanitizer: ErrorSanitizer) -> None:
        """URLs in exception messages are stripped."""
        exc = RuntimeError(
            "Failed to connect to https://internal.server.local:8069/api"
        )
        result = sanitizer.sanitize_exception(exc)
        assert "https://internal" not in result


# ── sanitize edge cases ───────────────────────────────────────────


class TestSanitizeEdgeCases:
    def test_all_content_stripped_returns_default(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        """If all content is stripped the default message is returned."""
        # A message that is entirely a file path
        msg = "/home/user/odoo/server.py:123"
        result = sanitizer.sanitize(msg)
        # After path removal, if nothing meaningful remains, default kicks in
        assert result  # Should not be empty

    def test_url_stripping(self, sanitizer: ErrorSanitizer) -> None:
        """Internal URLs are replaced with [internal]."""
        msg = "Connection refused: http://10.0.0.1:8069/web/dataset/call_kw"
        result = sanitizer.sanitize(msg)
        assert "http://10.0.0.1" not in result
        assert "call_kw" not in result

    def test_create_alter_table_stripped(self, sanitizer: ErrorSanitizer) -> None:
        """CREATE TABLE and ALTER TABLE SQL statements are stripped."""
        msg = "Error: CREATE TABLE temp_export (id INT)"
        result = sanitizer.sanitize(msg)
        assert "CREATE TABLE" not in result

        msg2 = "Error: ALTER TABLE res_partner ADD COLUMN x INT"
        result2 = sanitizer.sanitize(msg2)
        assert "ALTER TABLE" not in result2

    def test_multiple_newlines_collapsed(self, sanitizer: ErrorSanitizer) -> None:
        """Multiple consecutive newlines get collapsed to at most two."""
        msg = "Error happened\n\n\n\n\nSomething else"
        result = sanitizer.sanitize(msg)
        assert "\n\n\n" not in result

    def test_error_map_remainder_empty_after_strip(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        """Error map entry with no remainder after colon returns friendly only."""
        msg = "odoo.exceptions.UserError:"
        result = sanitizer.sanitize(msg)
        assert result == "Operation failed"

    def test_error_map_remainder_too_long(self, sanitizer: ErrorSanitizer) -> None:
        """Error map entry with remainder > 200 chars returns friendly only."""
        long_remainder = "a" * 250
        msg = f"odoo.exceptions.ValidationError: {long_remainder}"
        result = sanitizer.sanitize(msg)
        assert result == "Validation error: please check your input"
        assert long_remainder not in result


# ── _contains_internals ──────────────────────────────────────────


class TestContainsInternals:
    def test_detects_file_path(self, sanitizer: ErrorSanitizer) -> None:
        assert sanitizer._contains_internals("/home/user/odoo/test.py") is True

    def test_detects_sql(self, sanitizer: ErrorSanitizer) -> None:
        assert sanitizer._contains_internals("SELECT id FROM res_partner") is True

    def test_detects_traceback(self, sanitizer: ErrorSanitizer) -> None:
        tb = "Traceback (most recent call last):\n  File test.py"
        assert sanitizer._contains_internals(tb) is True

    def test_detects_db_reference(self, sanitizer: ErrorSanitizer) -> None:
        assert sanitizer._contains_internals("database 'prod_db'") is True

    def test_detects_url(self, sanitizer: ErrorSanitizer) -> None:
        assert sanitizer._contains_internals("http://localhost:8069") is True

    def test_clean_text_passes(self, sanitizer: ErrorSanitizer) -> None:
        assert sanitizer._contains_internals("Record not found") is False

    def test_empty_text_passes(self, sanitizer: ErrorSanitizer) -> None:
        assert sanitizer._contains_internals("") is False


class TestWerkzeug404Mapping:
    """v0.2.2 S1: Werkzeug 404 boilerplate maps to friendly message."""

    def test_404_message_replaced_with_friendly(self) -> None:
        from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer

        sanitizer = ErrorSanitizer()
        raw = (
            "404 Not Found: The requested URL was not found on the server. "
            "If you entered the URL manually please check your spelling and "
            "try again."
        )
        result = sanitizer.sanitize(raw)

        assert "404 Not Found" not in result
        assert "The requested URL" not in result
        assert "Model or endpoint not found" in result

    def test_non_404_unaffected(self) -> None:
        from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer

        sanitizer = ErrorSanitizer()
        result = sanitizer.sanitize("Some other error message")
        assert "Model or endpoint" not in result


class TestOdooRecordUserRepr:
    """v0.3.3 LOW: strip the internal ``(Record: model(ids,), User: uid)``
    repr that Odoo appends to MissingError / AccessError messages.
    The ``User:`` portion leaks an EFFECTIVE-uid (may not match the
    caller's session uid) so it must be removed; the ``Record:`` portion
    is retained for debuggability.
    """

    def test_strips_user_uid_keeps_record(self, sanitizer: ErrorSanitizer) -> None:
        raw = (
            "Record does not exist or has been deleted.\n"
            "(Record: res.partner(999999999,), User: 6)"
        )
        result = sanitizer.sanitize(raw)
        assert "User: 6" not in result
        assert "User:" not in result
        # Model + ids retained
        assert "res.partner" in result
        assert "999999999" in result

    def test_strips_user_uid_from_missing_error(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        # When wrapped in the canonical Odoo MissingError prefix, the
        # mapped friendly message must also be free of the User leak.
        raw = (
            "odoo.exceptions.MissingError: Record does not exist or has "
            "been deleted.\n(Record: res.partner(42,), User: 6)"
        )
        result = sanitizer.sanitize(raw)
        assert "User: 6" not in result
        # Either the mapped "Record not found" prefix is kept, or the
        # remainder retains the Record context — but never the User leak.
        assert "User:" not in result

    def test_strips_multiple_user_uid_fragments(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        raw = (
            "Multi-error report:\n(Record: sale.order(1,), User: 6) "
            "and\n(Record: res.partner(2,), User: 7)"
        )
        result = sanitizer.sanitize(raw)
        assert "User: 6" not in result
        assert "User: 7" not in result
        # Both record contexts retained
        assert "sale.order" in result
        assert "res.partner" in result

    def test_non_record_message_unchanged_in_substance(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        # Ensure a benign message that does NOT contain the pattern
        # passes through without unintended structural damage.
        raw = "Plain validation message: field 'name' is required"
        result = sanitizer.sanitize(raw)
        assert "field 'name' is required" in result

    def test_pattern_strips_even_without_leading_newline(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        raw = "Some error (Record: res.partner(5,), User: 3) trailing"
        result = sanitizer.sanitize(raw)
        assert "User: 3" not in result
        assert "User:" not in result


# ── Odoo ACL boilerplate stripping (UAT v0.3.3 #5e systemic) ─────


class TestOdooAclBoilerplate:
    """Strip technical model names, allowed-group lists, and the
    ``Contact your administrator`` tail from Odoo's stock ACL-denial
    error message. These three sub-patterns are the systemic source
    of the #5d / #5e leaks observed in get_my_profile + list_models.
    """

    def test_strips_hr_employee_public_acl_leak(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        # The exact wire string observed for portal_test calling
        # mcp__testodoo19mcp__get_my_profile on Odoo 19.
        raw = (
            "You are not allowed to access 'Public Employee' "
            "(hr.employee.public) records.\n\n"
            "This operation is allowed for the following groups:\n"
            "\t- Role / Member\n\n"
            "Contact your administrator to request access if necessary."
        )
        result = sanitizer.sanitize(raw)
        # Technical model name gone
        assert "hr.employee.public" not in result
        assert "(hr.employee.public)" not in result
        # Group block gone
        assert "Role / Member" not in result
        assert "allowed for the following groups" not in result
        # Administrator tail gone
        assert "Contact your administrator" not in result
        # Display name + leading "You are not allowed" kept (Option A)
        assert "Public Employee" in result
        assert "You are not allowed" in result

    def test_strips_ir_model_acl_leak(self, sanitizer: ErrorSanitizer) -> None:
        # The #5e finding: portal_test on list_models (which hits
        # ir.model under the hood) leaked ``ir.model`` + ``Access Rights``.
        raw = (
            "You are not allowed to access 'Models' (ir.model) records.\n\n"
            "This operation is allowed for the following groups:\n"
            "\t- Administration / Access Rights\n\n"
            "Contact your administrator to request access if necessary."
        )
        result = sanitizer.sanitize(raw)
        assert "ir.model" not in result
        assert "(ir.model)" not in result
        assert "Access Rights" not in result
        assert "Administration / Access Rights" not in result
        assert "Contact your administrator" not in result
        assert "Models" in result  # display name retained

    def test_strips_multi_group_acl_block(self, sanitizer: ErrorSanitizer) -> None:
        raw = (
            "You are not allowed to access 'Sales Order' (sale.order) records.\n\n"
            "This operation is allowed for the following groups:\n"
            "\t- Sales / Administrator\n"
            "\t- Sales / User: Own Documents Only\n"
            "\t- Sales / User: All Documents\n\n"
            "Contact your administrator to request access if necessary."
        )
        result = sanitizer.sanitize(raw)
        assert "sale.order" not in result
        assert "Sales / Administrator" not in result
        assert "Own Documents Only" not in result
        assert "All Documents" not in result
        assert "Contact your administrator" not in result

    def test_strips_acl_block_embedded_in_longer_error(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        # The ACL boilerplate might be preceded by other text (e.g. a
        # method-call context line). The strippers must still fire.
        raw = (
            "Method execute_kw failed:\n"
            "You are not allowed to access 'Helpdesk Ticket' "
            "(helpdesk.ticket) records.\n\n"
            "This operation is allowed for the following groups:\n"
            "\t- Helpdesk / User\n\n"
            "Contact your administrator to request access if necessary."
        )
        result = sanitizer.sanitize(raw)
        assert "helpdesk.ticket" not in result
        assert "Helpdesk / User" not in result
        assert "Contact your administrator" not in result
        # Preserved context: the original "Method execute_kw failed"
        # framing is still there for debuggability.
        assert "Method execute_kw failed" in result
        # Display name preserved
        assert "Helpdesk Ticket" in result

    def test_non_acl_error_passes_through_unchanged(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        # Negative: a generic non-ACL error should not be mangled.
        result = sanitizer.sanitize("Record not found")
        assert result == "Record not found"

    def test_partial_acl_only_groups_block(self, sanitizer: ErrorSanitizer) -> None:
        # An error that contains only the groups-block fragment (no
        # leading "You are not allowed" line, no admin-contact tail)
        # still gets the group block stripped.
        raw = (
            "Some prefix\n\n"
            "This operation is allowed for the following groups:\n"
            "\t- Role / Member\n"
            "trailing text"
        )
        result = sanitizer.sanitize(raw)
        assert "allowed for the following groups" not in result
        assert "Role / Member" not in result
        assert "Some prefix" in result
        assert "trailing text" in result

    def test_partial_acl_only_admin_contact_tail(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        raw = (
            "Operation failed.\n\n"
            "Contact your administrator to request access if necessary."
        )
        result = sanitizer.sanitize(raw)
        assert "Contact your administrator" not in result
        assert "Operation failed" in result

    def test_acl_with_friendly_exception_prefix(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        # When the message also carries the ``odoo.exceptions.AccessError``
        # prefix (the legacy XMLRPC-style fault-string), the friendly
        # mapping branch must still produce a clean output — the
        # remainder must have already been stripped of the technical
        # name + groups + admin tail.
        raw = (
            "odoo.exceptions.AccessError: "
            "You are not allowed to access 'Public Employee' "
            "(hr.employee.public) records.\n\n"
            "This operation is allowed for the following groups:\n"
            "\t- Role / Member\n\n"
            "Contact your administrator to request access if necessary."
        )
        result = sanitizer.sanitize(raw)
        assert "hr.employee.public" not in result
        assert "Role / Member" not in result
        assert "Contact your administrator" not in result
        assert "Access denied" in result

    def test_acl_via_sanitize_exception(self, sanitizer: ErrorSanitizer) -> None:
        # Exercise the OdooAccessError-from-JSON-RPC path: the exception
        # carries the raw ACL string in its message but its type name is
        # NOT embedded — the dispatch goes through sanitize_exception.
        access_error_cls = type("AccessError", (Exception,), {})
        access_error_cls.__module__ = "odoo.exceptions"
        access_error_cls.__qualname__ = "AccessError"
        exc = access_error_cls(
            "You are not allowed to access 'Public Employee' "
            "(hr.employee.public) records.\n\n"
            "This operation is allowed for the following groups:\n"
            "\t- Role / Member\n\n"
            "Contact your administrator to request access if necessary."
        )
        result = sanitizer.sanitize_exception(exc)
        # Display name retained
        assert "Public Employee" in result
        # All internals stripped
        assert "hr.employee.public" not in result
        assert "Role / Member" not in result
        assert "Contact your administrator" not in result
        # Friendly prefix retained
        assert "Access denied" in result

    def test_acl_groups_block_with_asterisk_bullets(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        # Some translations / customisations render bullets as ``*``
        # instead of ``-``. Match either.
        raw = (
            "You are not allowed to access 'Stock Move' (stock.move) records.\n\n"
            "This operation is allowed for the following groups:\n"
            "\t* Inventory / Manager\n\n"
            "Contact your administrator to request access if necessary."
        )
        result = sanitizer.sanitize(raw)
        assert "stock.move" not in result
        assert "Inventory / Manager" not in result
        assert "Contact your administrator" not in result
        assert "Stock Move" in result
