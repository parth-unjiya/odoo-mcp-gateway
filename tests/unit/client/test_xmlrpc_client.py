"""Tests for the XML-RPC client."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from odoo_mcp_gateway.client.exceptions import (
    OdooAuthError,
    OdooConnectionError,
    OdooMissingError,
    OdooValidationError,
)
from odoo_mcp_gateway.client.xmlrpc import (
    XmlRpcClient,
    _parse_response,
    _value_to_xml,
)

_URL = "http://odoo:8069"


# ------------------------------------------------------------------
# XML generation helpers
# ------------------------------------------------------------------


def _success_xml(value_xml: str) -> bytes:
    """Wrap a ``<value>`` fragment in an XML-RPC response."""
    return (
        '<?xml version="1.0"?>'
        "<methodResponse><params><param>"
        f"{value_xml}"
        "</param></params></methodResponse>"
    ).encode()


def _fault_xml(code: int, string: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        "<methodResponse><fault><value><struct>"
        "<member><name>faultCode</name>"
        f"<value><int>{code}</int></value></member>"
        "<member><name>faultString</name>"
        f"<value><string>{string}</string></value>"
        "</member>"
        "</struct></value></fault></methodResponse>"
    ).encode()


def _mock_client(
    responses: list[httpx.Response],
) -> httpx.AsyncClient:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=responses)
    client.aclose = AsyncMock()
    return client


def _resp(content: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"content-type": "text/xml"},
    )


# ------------------------------------------------------------------
# Value serialization
# ------------------------------------------------------------------


class TestValueToXml:
    def test_int(self) -> None:
        xml = _value_to_xml(42)
        assert "<int>42</int>" in xml

    def test_string(self) -> None:
        xml = _value_to_xml("hello")
        assert "<string>hello</string>" in xml

    def test_bool_true(self) -> None:
        xml = _value_to_xml(True)
        assert "<boolean>1</boolean>" in xml

    def test_bool_false(self) -> None:
        xml = _value_to_xml(False)
        assert "<boolean>0</boolean>" in xml

    def test_list(self) -> None:
        xml = _value_to_xml([1, 2])
        assert "<array>" in xml
        assert "<int>1</int>" in xml

    def test_dict(self) -> None:
        xml = _value_to_xml({"key": "val"})
        assert "<struct>" in xml
        assert "<name>key</name>" in xml

    def test_none(self) -> None:
        xml = _value_to_xml(None)
        assert "<nil/>" in xml

    def test_escaping(self) -> None:
        xml = _value_to_xml("<b>bold</b>")
        assert "&lt;b&gt;" in xml


# ------------------------------------------------------------------
# Response parsing
# ------------------------------------------------------------------


class TestParseResponse:
    def test_int_response(self) -> None:
        xml = _success_xml("<value><int>7</int></value>")
        assert _parse_response(xml) == 7

    def test_string_response(self) -> None:
        xml = _success_xml("<value><string>hello</string></value>")
        assert _parse_response(xml) == "hello"

    def test_fault_raises(self) -> None:
        xml = _fault_xml(1, "AccessDenied: bad creds")
        with pytest.raises(OdooAuthError):
            _parse_response(xml)


# ------------------------------------------------------------------
# Authenticate
# ------------------------------------------------------------------


class TestAuthenticate:
    async def test_success(self) -> None:
        resp = _resp(_success_xml("<value><int>5</int></value>"))
        mock = _mock_client([resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        result = await client.authenticate("mydb", "admin", "api_key_123")

        assert result.uid == 5
        assert result.database == "mydb"
        assert result.username == "admin"
        assert result.session_id is None

    async def test_api_key_auth(self) -> None:
        resp = _resp(_success_xml("<value><int>3</int></value>"))
        mock = _mock_client([resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        result = await client.authenticate("db", "user", "my-api-key")
        assert result.uid == 3

    async def test_invalid_credentials_false(self) -> None:
        resp = _resp(_success_xml("<value><boolean>0</boolean></value>"))
        mock = _mock_client([resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooAuthError, match="invalid credentials"):
            await client.authenticate("db", "bad", "creds")

    async def test_stores_credentials(self) -> None:
        resp = _resp(_success_xml("<value><int>1</int></value>"))
        mock = _mock_client([resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        assert client._db == "db"
        assert client._uid == 1
        assert client._password == "p"


# ------------------------------------------------------------------
# execute_kw
# ------------------------------------------------------------------


class TestExecuteKw:
    async def test_success(self) -> None:
        auth_resp = _resp(_success_xml("<value><int>1</int></value>"))
        kw_resp = _resp(
            _success_xml(
                "<value><array><data>"
                "<value><int>10</int></value>"
                "<value><int>20</int></value>"
                "</data></array></value>"
            )
        )
        mock = _mock_client([auth_resp, kw_resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        result = await client.execute_kw("res.partner", "search", [[]])
        assert result == [10, 20]

    async def test_not_authenticated_raises(self) -> None:
        mock = _mock_client([])
        client = XmlRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooAuthError, match="Not authenticated"):
            await client.execute_kw("res.partner", "search", [[]])

    async def test_fault_response(self) -> None:
        auth_resp = _resp(_success_xml("<value><int>1</int></value>"))
        fault_resp = _resp(_fault_xml(2, "ValidationError: bad field"))
        mock = _mock_client([auth_resp, fault_resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        with pytest.raises(OdooValidationError):
            await client.execute_kw("res.partner", "write", [[1], {"name": ""}])

    async def test_missing_error_fault(self) -> None:
        auth_resp = _resp(_success_xml("<value><int>1</int></value>"))
        fault_resp = _resp(_fault_xml(2, "MissingError: Record not found"))
        mock = _mock_client([auth_resp, fault_resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        await client.authenticate("db", "u", "p")
        with pytest.raises(OdooMissingError):
            await client.execute_kw("res.partner", "read", [[999]])


# ------------------------------------------------------------------
# get_version
# ------------------------------------------------------------------


class TestGetVersion:
    async def test_returns_dict(self) -> None:
        resp_xml = (
            "<value><struct>"
            "<member><name>server_version</name>"
            "<value><string>17.0</string></value>"
            "</member>"
            "<member><name>protocol_version</name>"
            "<value><int>1</int></value>"
            "</member>"
            "</struct></value>"
        )
        resp = _resp(_success_xml(resp_xml))
        mock = _mock_client([resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        result = await client.get_version()

        assert result["server_version"] == "17.0"
        assert result["protocol_version"] == 1

    async def test_non_dict_wrapped(self) -> None:
        resp = _resp(_success_xml("<value><string>17.0</string></value>"))
        mock = _mock_client([resp])
        client = XmlRpcClient(_URL, httpx_client=mock)

        result = await client.get_version()
        assert result == {"server_version": "17.0"}


# ------------------------------------------------------------------
# Connection errors
# ------------------------------------------------------------------


class TestConnectionErrors:
    async def test_connect_error(self) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock.aclose = AsyncMock()
        client = XmlRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooConnectionError, match="Cannot connect"):
            await client.get_version()

    async def test_timeout_error(self) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))
        mock.aclose = AsyncMock()
        client = XmlRpcClient(_URL, httpx_client=mock)

        with pytest.raises(OdooConnectionError, match="Timeout"):
            await client.get_version()


# ------------------------------------------------------------------
# close
# ------------------------------------------------------------------


class TestClose:
    async def test_close_owned(self) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.aclose = AsyncMock()
        client = XmlRpcClient(_URL, httpx_client=mock)
        client._owns_client = True
        await client.close()
        mock.aclose.assert_called_once()

    async def test_close_not_owned(self) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.aclose = AsyncMock()
        client = XmlRpcClient(_URL, httpx_client=mock)
        client._owns_client = False
        await client.close()
        mock.aclose.assert_not_called()
