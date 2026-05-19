"""JSON-RPC client for Odoo's ``/web`` endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

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
)

logger = logging.getLogger(__name__)

# Maps Odoo server-side exception names to our hierarchy.
_EXCEPTION_MAP: dict[str, type[OdooError]] = {
    "odoo.exceptions.AccessDenied": OdooAuthError,
    "odoo.exceptions.AccessError": OdooAccessError,
    "odoo.exceptions.ValidationError": OdooValidationError,
    "odoo.exceptions.UserError": OdooUserError,
    "odoo.exceptions.MissingError": OdooMissingError,
    # Session expiry is signalled by Odoo's web layer. Map it to a dedicated
    # subclass of OdooAuthError so retry logic can target it specifically
    # without also retrying on legitimate access-denied errors.
    "odoo.http.SessionExpiredException": OdooSessionExpiredError,
}

# Substrings (lower-cased) that indicate a session-expiry condition when the
# server-side exception name is missing or unrecognised.
_SESSION_EXPIRED_MARKERS: tuple[str, ...] = (
    "session expired",
    "sessionexpired",
    "session_expired",
)


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
        self._password: Credential = Credential(None)
        self._session_id: Credential = Credential(None)
        self._uid: int | None = None

        self._rpc_id = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _build_cookies(self) -> dict[str, str]:
        sid = self._session_id.reveal()
        if sid:
            return {"session_id": sid}
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
            self._session_id = Credential(sid)

        # Detect HTML responses up front (typically 404 pages from invalid
        # model/method names or a wrong base URL). Without this, ``.json()``
        # raises a low-level decoding error that leaks ``response.text``
        # — including the full Werkzeug stack trace — through the generic
        # ``Non-JSON response`` message.
        #
        # The headers / text attributes are wrapped in str() so we don't
        # crash on test doubles that return MagicMock for missing fields.
        content_type_raw = ""
        try:
            content_type_raw = str(response.headers.get("content-type", "") or "")
        except Exception:
            content_type_raw = ""
        content_type = content_type_raw.lower()

        body_preview = ""
        try:
            body_text = response.text
            if isinstance(body_text, str):
                body_preview = body_text[:5].lower()
        except Exception:
            body_preview = ""

        if "html" in content_type or body_preview in ("<!doc", "<html"):
            try:
                status = int(response.status_code)
            except Exception:
                status = 0
            if status == 404:
                raise OdooMissingError(
                    "Odoo endpoint not found (HTTP 404). This usually means "
                    "the model or method does not exist, or the URL path is "
                    "wrong.",
                    code="HTTP_404",
                )
            raise OdooConnectionError(
                f"Unexpected HTML response from Odoo (HTTP {status}). "
                "Verify the gateway URL points at a JSON-RPC endpoint.",
            )

        try:
            data: dict[str, Any] = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise OdooConnectionError(
                f"Non-JSON response from Odoo (HTTP {response.status_code})"
            ) from exc

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

        # Fallback: if the message itself hints at session expiry, surface it
        # as a session-expired error so the retry logic can kick in even when
        # Odoo doesn't send the canonical exception name.
        lower_msg = message.lower()
        if any(marker in lower_msg for marker in _SESSION_EXPIRED_MARKERS):
            raise OdooSessionExpiredError(message, code=exc_name or None)

        raise OdooUserError(message, code=exc_name or None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def authenticate(self, db: str, login: str, password: str) -> AuthResult:
        self._db = db
        self._login = login
        self._password = Credential(password)

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
            session_id=self._session_id.reveal(),
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
        except OdooSessionExpiredError:
            # Auto-retry once on a *genuine* session-expiry signal. Other
            # OdooAuthError subclasses (e.g. AccessDenied on a specific record)
            # propagate without retry so we don't double Odoo load or mask
            # legitimate access-control failures.
            password_str = self._password.reveal()
            if self._db and self._login and password_str:
                logger.info("Session expired, re-authenticating...")
                await self.authenticate(self._db, self._login, password_str)
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
        self._password.clear()
        self._session_id.clear()
        self._login = None
        self._db = None
        self._uid = None
