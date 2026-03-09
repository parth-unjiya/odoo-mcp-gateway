"""Tests for XML-RPC XXE / entity-bomb protection via defusedxml."""

from __future__ import annotations

import pytest
from defusedxml.common import EntitiesForbidden

from odoo_mcp_gateway.client.xmlrpc import _parse_response

# ------------------------------------------------------------------
# Normal parsing
# ------------------------------------------------------------------


class TestParseResponseNormal:
    """Verify _parse_response handles well-formed XML-RPC responses."""

    def test_integer_response(self) -> None:
        xml = (
            b"<?xml version='1.0'?>"
            b"<methodResponse><params><param>"
            b"<value><int>42</int></value>"
            b"</param></params></methodResponse>"
        )
        assert _parse_response(xml) == 42

    def test_string_response(self) -> None:
        xml = (
            b"<?xml version='1.0'?>"
            b"<methodResponse><params><param>"
            b"<value><string>hello world</string></value>"
            b"</param></params></methodResponse>"
        )
        assert _parse_response(xml) == "hello world"

    def test_boolean_response(self) -> None:
        xml = (
            b"<?xml version='1.0'?>"
            b"<methodResponse><params><param>"
            b"<value><boolean>1</boolean></value>"
            b"</param></params></methodResponse>"
        )
        assert _parse_response(xml) is True

    def test_array_response(self) -> None:
        xml = (
            b"<?xml version='1.0'?>"
            b"<methodResponse><params><param>"
            b"<value><array><data>"
            b"<value><int>1</int></value>"
            b"<value><int>2</int></value>"
            b"</data></array></value>"
            b"</param></params></methodResponse>"
        )
        assert _parse_response(xml) == [1, 2]

    def test_struct_response(self) -> None:
        xml = (
            b"<?xml version='1.0'?>"
            b"<methodResponse><params><param>"
            b"<value><struct>"
            b"<member><name>id</name><value><int>1</int></value></member>"
            b"<member><name>name</name><value><string>Test</string></value></member>"
            b"</struct></value>"
            b"</param></params></methodResponse>"
        )
        result = _parse_response(xml)
        assert result == {"id": 1, "name": "Test"}

    def test_empty_params(self) -> None:
        xml = b"<?xml version='1.0'?><methodResponse><params></params></methodResponse>"
        assert _parse_response(xml) is None


# ------------------------------------------------------------------
# XXE entity expansion attack
# ------------------------------------------------------------------


class TestXXEProtection:
    """Verify defusedxml blocks external entity (XXE) injection."""

    def test_rejects_external_entity(self) -> None:
        """Standard XXE: attempts to read /etc/passwd via SYSTEM entity."""
        xxe_payload = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b"<methodResponse><params><param>"
            b"<value><string>&xxe;</string></value>"
            b"</param></params></methodResponse>"
        )
        with pytest.raises(EntitiesForbidden):
            _parse_response(xxe_payload)

    def test_rejects_external_entity_http(self) -> None:
        """XXE variant: attempts to reach an external HTTP endpoint."""
        xxe_payload = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://evil.example.com/data">]>'
            b"<methodResponse><params><param>"
            b"<value><string>&xxe;</string></value>"
            b"</param></params></methodResponse>"
        )
        with pytest.raises(EntitiesForbidden):
            _parse_response(xxe_payload)

    def test_rejects_parameter_entity(self) -> None:
        """Parameter entities (% entity) used in blind XXE attacks."""
        xxe_payload = (
            b'<?xml version="1.0"?>'
            b"<!DOCTYPE foo ["
            b'<!ENTITY % xxe SYSTEM "file:///etc/hostname">'
            b"%xxe;"
            b"]>"
            b"<methodResponse><params><param>"
            b"<value><string>test</string></value>"
            b"</param></params></methodResponse>"
        )
        with pytest.raises(EntitiesForbidden):
            _parse_response(xxe_payload)


# ------------------------------------------------------------------
# Billion laughs / entity bomb
# ------------------------------------------------------------------


class TestBillionLaughsProtection:
    """Verify defusedxml blocks entity expansion bombs."""

    def test_rejects_billion_laughs(self) -> None:
        """Classic billion laughs: nested internal entities expand exponentially."""
        _x10 = "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;"
        bomb_payload = (
            b'<?xml version="1.0"?>'
            b"<!DOCTYPE lolz ["
            b'<!ENTITY lol "lol">'
            + f'<!ENTITY lol2 "{_x10}">'.encode()
            + f'<!ENTITY lol3 "{_x10.replace("lol", "lol2")}">'.encode()
            + f'<!ENTITY lol4 "{_x10.replace("lol", "lol3")}">'.encode()
            + f'<!ENTITY lol5 "{_x10.replace("lol", "lol4")}">'.encode()
            + b"]>"
            b"<methodResponse><params><param>"
            b"<value><string>&lol5;</string></value>"
            b"</param></params></methodResponse>"
        )
        with pytest.raises(EntitiesForbidden):
            _parse_response(bomb_payload)

    def test_rejects_simple_internal_entity(self) -> None:
        """Even a single internal entity definition is rejected by defusedxml."""
        payload = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE foo [<!ENTITY greeting "hello">]>'
            b"<methodResponse><params><param>"
            b"<value><string>&greeting;</string></value>"
            b"</param></params></methodResponse>"
        )
        with pytest.raises(EntitiesForbidden):
            _parse_response(payload)

    def test_rejects_quadratic_blowup(self) -> None:
        """Quadratic blowup: single large entity referenced many times."""
        big_string = "A" * 10000
        payload = (
            b'<?xml version="1.0"?>'
            b"<!DOCTYPE foo ["
            b'<!ENTITY big "' + big_string.encode() + b'">'
            b"]>"
            b"<methodResponse><params><param>"
            b"<value><string>&big;&big;&big;&big;&big;</string></value>"
            b"</param></params></methodResponse>"
        )
        with pytest.raises(EntitiesForbidden):
            _parse_response(payload)
