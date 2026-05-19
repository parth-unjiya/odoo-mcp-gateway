"""Regression tests for sanitizer URL/PG/path broadening (P2-9, M-4).

Pre-fix gaps:
* ``_URL_RE`` only matched ``https?://`` — ftp://, file://, ldap://,
  data: URIs slipped through. file:// was the original SSRF motivator
  for the ir.attachment block; leaking it in errors undid that.
* ``_PATH_RE`` missed Windows-style paths and non-.py addons files.
* ``_PG_INVALID_INPUT_RE`` / ``_PG_COLUMN_RE`` patterns are new and
  catch the "invalid input syntax for type integer" + column-name
  leaks the Pass 5 test surfaced.
"""

from __future__ import annotations

from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer


class TestUrlBroadening:
    def setup_method(self) -> None:
        self.s = ErrorSanitizer()

    def test_file_scheme_stripped(self) -> None:
        msg = "Error reading file:///var/lib/odoo/secret.key"
        out = self.s.sanitize(msg)
        assert "file://" not in out
        assert "/var/lib/odoo/secret.key" not in out

    def test_ftp_scheme_stripped(self) -> None:
        out = self.s.sanitize("Could not connect to ftp://attacker.example/")
        assert "ftp://" not in out

    def test_ldap_scheme_stripped(self) -> None:
        out = self.s.sanitize("Bind failed against ldap://internal.corp:389/dc=corp")
        assert "ldap://" not in out

    def test_data_uri_stripped(self) -> None:
        out = self.s.sanitize("Embedded blob data:text/plain;base64,SGVsbG8K")
        assert "data:text/plain" not in out

    def test_https_still_stripped(self) -> None:
        out = self.s.sanitize("Webhook to https://internal.corp/secret failed")
        assert "https://" not in out


class TestPathBroadening:
    def setup_method(self) -> None:
        self.s = ErrorSanitizer()

    def test_windows_path_stripped(self) -> None:
        msg = r"Module load failed at C:\odoo\addons\helpdesk\models.py:42"
        out = self.s.sanitize(msg)
        assert "C:\\odoo" not in out and "C:/odoo" not in out

    def test_xml_addon_path_stripped(self) -> None:
        out = self.s.sanitize("Invalid view /opt/odoo/addons/sale/views/sale.xml:153")
        assert "/opt/odoo/addons" not in out


class TestPostgresLeakBroadening:
    def setup_method(self) -> None:
        self.s = ErrorSanitizer()

    def test_invalid_input_syntax_stripped(self) -> None:
        msg = (
            'invalid input syntax for type integer: "not_an_int" '
            "LINE 1: SELECT id FROM sale_order WHERE id='not_an_int'"
        )
        out = self.s.sanitize(msg)
        assert "not_an_int" not in out
        # LINE excerpt also stripped
        assert "LINE 1:" not in out

    def test_column_of_relation_stripped(self) -> None:
        msg = 'column "amount_total" of relation "sale_order" cannot be null'
        out = self.s.sanitize(msg)
        # The bare relation-column reference is sanitised away.
        assert '"amount_total" of relation' not in out

    def test_psycopg3_class_name_stripped(self) -> None:
        # psycopg (v3) class names match the same pattern as psycopg2.
        out = self.s.sanitize("psycopg.errors.UniqueViolation occurred")
        assert "psycopg.errors" not in out
        out2 = self.s.sanitize("psycopg2.IntegrityError: oops")
        assert "psycopg2.IntegrityError" not in out2

    def test_contains_internals_detects_new_patterns(self) -> None:
        # The detector now flags pg-invalid-input + column refs too, so
        # error-mapping won't accidentally pass them through unsanitised.
        assert self.s._contains_internals('invalid input syntax for type integer: "x"')
        assert self.s._contains_internals('column "x" of relation "y"')
