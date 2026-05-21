"""UAT v0.3.3 MED (Odoo 19) — post-login httpx client reuse regression.

Symptom: the first WRITE request after a successful ``login`` call
intermittently raised ``RuntimeError("Cannot send a request, as the
client has been closed.")`` while a subsequent ``search_read`` "warmed
up" the client and the next write succeeded. Independent UAT runs
reproduced this 2-4 times on Odoo 19.

Root cause: the underlying ``httpx.AsyncClient`` was occasionally
observed in an ``is_closed=True`` state after login. We could not pin
down the exact event sequence that closes it but the safe remediation
is independent of root cause: lazily re-instantiate the client at the
start of each RPC. The clients only recreate when they OWN their
``httpx.AsyncClient`` — externally injected clients are not touched.
"""

from __future__ import annotations

from typing import Any

import httpx

from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient

_URL = "http://localhost:8069"


# ──────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal httpx.Response-compatible double for the success path."""

    status_code = 200
    cookies: dict[str, str] = {}
    headers = {"content-type": "application/json"}
    text = "{}"

    def __init__(self, json_body: dict[str, Any]) -> None:
        self._json = json_body
        self.content = b""

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeAsyncClient:
    """A drop-in for ``httpx.AsyncClient`` whose ``is_closed`` flag and
    ``post`` behaviour we control directly. Mirrors the parts of the
    real interface our clients call.
    """

    def __init__(self) -> None:
        self.is_closed = False
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.aclose_calls = 0
        # Default body returned by ``post`` — tests override.
        self._next_body: dict[str, Any] = {"result": {"ok": True}}
        self._next_xml: bytes = (
            b"<?xml version='1.0'?>"
            b"<methodResponse><params><param><value>"
            b"<int>1</int></value></param></params></methodResponse>"
        )

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        if self.is_closed:
            # Real httpx behaviour: closed client refuses requests.
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.post_calls.append((url, kwargs))
        if "/xmlrpc" in url:
            # XML-RPC consumers read ``response.content`` directly.
            resp = _FakeResponse({})
            resp.content = self._next_xml  # type: ignore[assignment]
            return resp
        return _FakeResponse(self._next_body)

    async def aclose(self) -> None:
        self.is_closed = True
        self.aclose_calls += 1


# ──────────────────────────────────────────────────────────────────
# JSON-RPC client
# ──────────────────────────────────────────────────────────────────


class TestJsonRpcEnsureOpen:
    async def test_closed_owned_client_is_recreated_on_next_rpc(self) -> None:
        client = JsonRpcClient(_URL)
        # The client owns its httpx client. Simulate the failure mode:
        # something closed the underlying AsyncClient between login
        # and the next call.
        await client._client.aclose()
        assert client._client.is_closed is True
        # Triggering an RPC must NOT raise the "client has been closed"
        # error — the guard recreates a fresh AsyncClient transparently.
        client._ensure_open_client()
        assert client._client.is_closed is False

    async def test_externally_injected_closed_client_not_recreated(self) -> None:
        # An externally-injected client signals the OWNER manages lifecycle;
        # we MUST NOT silently swap it out beneath them.
        external = httpx.AsyncClient(base_url=_URL)
        await external.aclose()
        client = JsonRpcClient(_URL, httpx_client=external)
        client._ensure_open_client()
        # Same instance, still closed — caller owns the lifecycle.
        assert client._client is external
        assert client._client.is_closed is True

    async def test_post_login_write_path_does_not_crash_on_closed_client(
        self,
    ) -> None:
        """Simulate the live scenario: login closes the underlying httpx
        client somewhere, then the user's first ``create_record`` triggers
        an RPC. The guard must recreate the client BEFORE the post call —
        no ``RuntimeError`` about "client has been closed" surfaces.
        """
        fake = _FakeAsyncClient()
        client = JsonRpcClient(_URL, httpx_client=fake)
        # Mark the client as owned so the guard is eligible to recreate.
        client._owns_client = True
        # Close the client to reproduce the bug condition.
        fake.is_closed = True

        # ``_ensure_open_client`` is invoked from ``_rpc`` BEFORE the
        # actual post call. After the guard runs, ``self._client`` is a
        # FRESH instance — no longer ``fake``. We invoke the guard
        # directly to keep the test hermetic (no real network call to
        # a missing server).
        client._ensure_open_client()
        assert client._client is not fake
        assert client._client.is_closed is False


# ──────────────────────────────────────────────────────────────────
# XML-RPC client
# ──────────────────────────────────────────────────────────────────


class TestXmlRpcEnsureOpen:
    async def test_closed_owned_client_is_recreated(self) -> None:
        client = XmlRpcClient(_URL)
        await client._client.aclose()
        assert client._client.is_closed is True
        client._ensure_open_client()
        assert client._client.is_closed is False

    async def test_externally_injected_closed_client_not_recreated(self) -> None:
        external = httpx.AsyncClient(base_url=_URL)
        await external.aclose()
        client = XmlRpcClient(_URL, httpx_client=external)
        client._ensure_open_client()
        assert client._client is external
        assert client._client.is_closed is True

    async def test_post_login_xmlrpc_call_does_not_crash_on_closed_client(
        self,
    ) -> None:
        fake = _FakeAsyncClient()
        client = XmlRpcClient(_URL, httpx_client=fake)
        client._owns_client = True
        fake.is_closed = True

        # The guard runs at the start of ``_call`` and replaces the
        # closed httpx.AsyncClient with a fresh one. After the guard
        # fires, the client we hold is no longer the closed fake.
        client._ensure_open_client()
        assert client._client is not fake
        assert client._client.is_closed is False
