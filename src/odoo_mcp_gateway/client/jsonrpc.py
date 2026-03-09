"""JSON-RPC client for Odoo's ``/web`` endpoints."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from odoo_mcp_gateway.client.base import AuthResult, OdooClientBase
from odoo_mcp_gateway.client.exceptions import (
    OdooAccessError,
    OdooAuthError,
    OdooConnectionError,
    OdooError,
    OdooMissingError,
    OdooUserError,
    OdooValidationError,
)

logger = logging.getLogger(__name__)

# Maps Odoo server-side exception names to our hierarchy.
_EXCEPTION_MAP: dict[str, type[OdooError]] = {
    "odoo.exceptions.AccessDenied": OdooAuthError,
    "odoo.exceptions.AccessError": OdooAccessError,
    "odoo.exceptions.ValidationError": OdooValidationError,
    "odoo.exceptions.UserError": OdooUserError,
    "odoo.exceptions.MissingError": OdooMissingError,
}


class JsonRpcClient(OdooClientBase):
    """Async JSON-RPC client backed by *httpx*."""

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

        # Stored credentials for automatic re-auth on session expiry.
        self._db: str | None = None
        self._login: str | None = None
        self._password: str | None = None
        self._session_id: str | None = None
        self._uid: int | None = None

        self._rpc_id = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _build_cookies(self) -> dict[str, str]:
        if self._session_id:
            return {"session_id": self._session_id}
        return {}

    async def _rpc(self, path: str, params: dict[str, Any]) -> Any:
        """Send a JSON-RPC 2.0 request and return the ``result`` value."""
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "call",
            "params": params,
        }
        try:
            response = await self._client.post(
                path,
                json=payload,
                cookies=self._build_cookies(),
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

        # Extract session cookie if present.
        sid = response.cookies.get("session_id")
        if sid:
            self._session_id = sid

        data: dict[str, Any] = response.json()

        if "error" in data:
            self._raise_for_error(data["error"])

        return data.get("result")

    @staticmethod
    def _raise_for_error(error: dict[str, Any]) -> None:
        """Classify an Odoo JSON-RPC error and raise the right exception."""
        err_data: dict[str, Any] = error.get("data", {})
        exc_name: str = err_data.get("name", "")
        message: str = err_data.get("message", error.get("message", "Unknown error"))

        exc_cls = _EXCEPTION_MAP.get(exc_name)
        if exc_cls is not None:
            raise exc_cls(message, code=exc_name)

        # Fallback: if the message itself hints at session expiry, treat it
        # as an auth error so the retry logic can kick in.
        raise OdooUserError(message, code=exc_name or None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def authenticate(self, db: str, login: str, password: str) -> AuthResult:
        self._db = db
        self._login = login
        self._password = password

        result = await self._rpc(
            "/web/session/authenticate",
            {"db": db, "login": login, "password": password},
        )

        uid: int = result.get("uid", 0)
        if not uid:
            raise OdooAuthError("Authentication failed: invalid credentials")

        self._uid = uid

        user_context: dict[str, Any] = result.get("user_context", {})
        is_admin: bool = result.get("is_admin", False)
        username: str = result.get("username", login)

        return AuthResult(
            uid=uid,
            session_id=self._session_id,
            user_context=user_context,
            is_admin=is_admin,
            groups=[],
            username=username,
            database=db,
        )

    async def execute_kw(
        self,
        model: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        kw = kwargs or {}
        try:
            return await self._rpc(
                "/web/dataset/call_kw",
                {
                    "model": model,
                    "method": method,
                    "args": args,
                    "kwargs": kw,
                },
            )
        except OdooAuthError:
            # Auto-retry once on session expiry.
            if self._db and self._login and self._password:
                logger.info("Session expired, re-authenticating...")
                await self.authenticate(self._db, self._login, self._password)
                return await self._rpc(
                    "/web/dataset/call_kw",
                    {
                        "model": model,
                        "method": method,
                        "args": args,
                        "kwargs": kw,
                    },
                )
            raise

    async def get_version(self) -> dict[str, Any]:
        result = await self._rpc("/web/webclient/version_info", {})
        return result  # type: ignore[no-any-return]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
