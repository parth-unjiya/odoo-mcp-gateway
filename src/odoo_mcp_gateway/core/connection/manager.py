"""Connection manager with circuit-breaker and retry logic."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from odoo_mcp_gateway.client.exceptions import OdooConnectionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthStatus:
    """Snapshot of the connection manager's health state.

    Attributes
    ----------
    circuit_state:
        Current circuit breaker state (``closed``, ``open``, ``half_open``).
    failure_count:
        Number of consecutive failures recorded.
    failure_threshold:
        The configured threshold before the circuit opens.
    seconds_until_recovery:
        Seconds remaining before the circuit moves from OPEN to HALF_OPEN.
        ``0.0`` when the circuit is not OPEN.
    base_url:
        The Odoo base URL this manager connects to.
    """

    circuit_state: str
    failure_count: int
    failure_threshold: int
    seconds_until_recovery: float
    base_url: str


class CircuitState(Enum):
    """States of the circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ConnectionManager:
    """HTTP connection pool with circuit breaker and exponential-backoff retry.

    Parameters
    ----------
    base_url:
        Root URL of the Odoo instance (e.g. ``http://localhost:8069``).
    pool_size:
        Maximum number of concurrent connections.
    failure_threshold:
        Consecutive failures before the circuit opens.
    recovery_timeout:
        Seconds to wait in OPEN state before probing (HALF_OPEN).
    max_retries:
        Number of retry attempts for transient errors.
    backoff_base:
        Base delay (seconds) for exponential backoff.
    """

    def __init__(
        self,
        base_url: str,
        pool_size: int = 10,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0

        limits = httpx.Limits(
            max_connections=pool_size,
            max_keepalive_connections=pool_size,
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            limits=limits,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Circuit breaker helpers
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current circuit-breaker state (may transition from OPEN to HALF_OPEN)."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def _record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def _record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if (
            self._failure_count >= self._failure_threshold
            or self._state == CircuitState.HALF_OPEN
        ):
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN after %d consecutive failures",
                self._failure_count,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send an HTTP request with circuit-breaker and retry logic.

        Retries (with exponential backoff) are only applied to transient
        network errors (``httpx.ConnectError``, ``httpx.TimeoutException``).
        HTTP 4xx responses are **not** retried.
        """
        current = self.state
        if current == CircuitState.OPEN:
            raise OdooConnectionError("Circuit breaker is OPEN -- requests are blocked")

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, path, **kwargs)

                # Treat 5xx as transient for retry purposes, but still
                # return the response on the last attempt.
                if response.status_code >= 500 and attempt < self._max_retries:
                    self._record_failure()
                    last_exc = OdooConnectionError(
                        f"Server returned {response.status_code}"
                    )
                    await self._backoff(attempt)
                    continue

                self._record_success()
                return response

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                self._record_failure()
                last_exc = exc
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    # Re-check circuit state after backoff.
                    if self.state == CircuitState.OPEN:
                        raise OdooConnectionError(
                            "Circuit breaker is OPEN -- requests are blocked"
                        ) from exc

        # All retries exhausted.
        raise OdooConnectionError(
            f"Request failed after {self._max_retries + 1} attempts: {last_exc}"
        ) from last_exc

    def get_health_status(self) -> HealthStatus:
        """Return a snapshot of the circuit breaker and connection health.

        This method is synchronous and does not make any network calls.
        """
        current = self.state
        seconds_until_recovery = 0.0
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            remaining = self._recovery_timeout - elapsed
            seconds_until_recovery = max(0.0, remaining)

        return HealthStatus(
            circuit_state=current.value,
            failure_count=self._failure_count,
            failure_threshold=self._failure_threshold,
            seconds_until_recovery=round(seconds_until_recovery, 2),
            base_url=self._base_url,
        )

    @property
    def base_url(self) -> str:
        """The Odoo base URL this manager connects to."""
        return self._base_url

    @property
    def client(self) -> httpx.AsyncClient:
        """The underlying ``httpx.AsyncClient``.

        Prefer :meth:`request` for circuit-breaker-wrapped calls.
        Direct access is provided for cases where callers need to
        configure session cookies or custom auth.
        """
        return self._client

    async def health_check(self) -> bool:
        """Probe Odoo for connectivity.

        Returns ``True`` if the server responds, ``False`` otherwise.
        """
        try:
            resp = await self._client.post(
                "/web/session/get_session_info",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "call",
                    "params": {},
                },
            )
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    async def close(self) -> None:
        """Shut down the underlying connection pool."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _backoff(self, attempt: int) -> None:
        """Sleep with exponential backoff.

        Uses ``asyncio.sleep`` so other tasks are not blocked.
        """
        import asyncio

        delay = self._backoff_base * (2**attempt)
        logger.debug("Retry backoff: %.2fs (attempt %d)", delay, attempt + 1)
        await asyncio.sleep(delay)
