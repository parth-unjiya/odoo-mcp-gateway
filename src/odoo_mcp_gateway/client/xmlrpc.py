"""Async XML-RPC client for Odoo using *httpx* (no stdlib xmlrpc)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import defusedxml.ElementTree as _safe_ET
import httpx

from odoo_mcp_gateway.client.base import AuthResult, OdooClientBase
from odoo_mcp_gateway.client.exceptions import (
    OdooAccessError,
    OdooAuthError,
    OdooConnectionError,
    OdooMissingError,
    OdooUserError,
    OdooValidationError,
)

logger = logging.getLogger(__name__)

_FAULT_MAP: dict[str, type[Exception]] = {
    "AccessDenied": OdooAuthError,
    "AccessError": OdooAccessError,
    "ValidationError": OdooValidationError,
    "UserError": OdooUserError,
    "MissingError": OdooMissingError,
}


# ------------------------------------------------------------------
# XML-RPC payload helpers
# ------------------------------------------------------------------


def _escape_xml(text: str) -> str:
    """Escape XML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _value_to_xml(value: Any) -> str:
    """Serialize a Python value to an XML-RPC ``<value>`` fragment."""
    if isinstance(value, bool):
        return f"<value><boolean>{1 if value else 0}</boolean></value>"
    if isinstance(value, int):
        return f"<value><int>{value}</int></value>"
    if isinstance(value, float):
        return f"<value><double>{value}</double></value>"
    if isinstance(value, str):
        return f"<value><string>{_escape_xml(value)}</string></value>"
    if value is None:
        return "<value><nil/></value>"
    if isinstance(value, (list, tuple)):
        items = "".join(_value_to_xml(v) for v in value)
        return f"<value><array><data>{items}</data></array></value>"
    if isinstance(value, dict):
        members = "".join(
            f"<member><name>{_escape_xml(str(k))}</name>{_value_to_xml(v)}</member>"
            for k, v in value.items()
        )
        return f"<value><struct>{members}</struct></value>"
    # Fallback: stringify
    return _value_to_xml(str(value))


def _build_request(method: str, params: list[Any]) -> str:
    """Build a complete XML-RPC ``<methodCall>`` document."""
    param_xml = "".join(f"<param>{_value_to_xml(p)}</param>" for p in params)
    return (
        "<?xml version='1.0'?>"
        f"<methodCall><methodName>{_escape_xml(method)}</methodName>"
        f"<params>{param_xml}</params></methodCall>"
    )


def _parse_value(elem: ET.Element) -> Any:
    """Recursively parse an XML-RPC ``<value>`` element into Python."""
    # A bare <value> with text and no child element is a string.
    if len(elem) == 0:
        return elem.text or ""
    child = elem[0]
    tag = child.tag
    if tag == "int" or tag == "i4":
        return int(child.text or "0")
    if tag == "boolean":
        return (child.text or "0") != "0"
    if tag == "double":
        return float(child.text or "0")
    if tag == "string":
        return child.text or ""
    if tag == "nil":
        return None
    if tag == "array":
        data = child.find("data")
        if data is None:
            return []
        return [_parse_value(v) for v in data.findall("value")]
    if tag == "struct":
        result: dict[str, Any] = {}
        for member in child.findall("member"):
            name_el = member.find("name")
            val_el = member.find("value")
            if name_el is not None and val_el is not None:
                result[name_el.text or ""] = _parse_value(val_el)
        return result
    # Unknown type: return text.
    return child.text or ""


def _parse_response(xml_bytes: bytes) -> Any:
    """Parse an XML-RPC response body and return the result or raise."""
    root = _safe_ET.fromstring(xml_bytes)

    # Check for <fault>.
    fault = root.find("fault")
    if fault is not None:
        val_el = fault.find("value")
        fault_data: Any = _parse_value(val_el) if val_el is not None else {}
        fault_string = ""
        if isinstance(fault_data, dict):
            fault_string = str(fault_data.get("faultString", ""))
        else:
            fault_string = str(fault_data)
        _raise_for_fault(fault_string)

    # Normal response: first <param> value.
    params_el = root.find("params")
    if params_el is None:
        return None
    param = params_el.find("param")
    if param is None:
        return None
    val_el = param.find("value")
    if val_el is None:
        return None
    return _parse_value(val_el)


def _raise_for_fault(fault_string: str) -> None:
    """Map an XML-RPC fault string to an exception."""
    for key, cls in _FAULT_MAP.items():
        if key in fault_string:
            raise cls(fault_string)
    raise OdooUserError(fault_string)


# ------------------------------------------------------------------
# Client
# ------------------------------------------------------------------


class XmlRpcClient(OdooClientBase):
    """Async XML-RPC client for Odoo."""

    def __init__(
        self,
        base_url: str,
        httpx_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._owns_client = httpx_client is None
        self._client = httpx_client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
        )

        self._db: str | None = None
        self._uid: int | None = None
        self._password: str | None = None

    async def _call(self, endpoint: str, method: str, params: list[Any]) -> Any:
        body = _build_request(method, params)
        try:
            response = await self._client.post(
                endpoint,
                content=body.encode("utf-8"),
                headers={"Content-Type": "text/xml"},
                timeout=self._timeout,
            )
        except httpx.ConnectError as exc:
            raise OdooConnectionError(
                f"Cannot connect to Odoo at {self._base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise OdooConnectionError(
                f"Timeout connecting to Odoo at {self._base_url}: {exc}"
            ) from exc
        return _parse_response(response.content)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def authenticate(self, db: str, login: str, password: str) -> AuthResult:
        uid = await self._call(
            "/xmlrpc/2/common",
            "authenticate",
            [db, login, password, {}],
        )

        if not uid or uid is False:
            raise OdooAuthError("Authentication failed: invalid credentials")

        uid_int: int = int(uid)

        self._db = db
        self._uid = uid_int
        self._password = password

        return AuthResult(
            uid=uid_int,
            session_id=None,
            user_context={},
            is_admin=False,
            groups=[],
            username=login,
            database=db,
        )

    async def execute_kw(
        self,
        model: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        if self._db is None or self._uid is None or self._password is None:
            raise OdooAuthError("Not authenticated. Call authenticate() first.")
        kw = kwargs or {}
        return await self._call(
            "/xmlrpc/2/object",
            "execute_kw",
            [self._db, self._uid, self._password, model, method, args, kw],
        )

    async def get_version(self) -> dict[str, Any]:
        result = await self._call(
            "/xmlrpc/2/common",
            "version",
            [],
        )
        if isinstance(result, dict):
            return result
        return {"server_version": str(result)}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
