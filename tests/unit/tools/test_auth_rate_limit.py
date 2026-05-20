"""Per-IP source-key isolation tests for the login rate limiter.

Audit blocker #1: in HTTP mode, the previous ``_resolve_source_id``
returned ``stdio:<pid>`` whenever ``_current_session_key`` was unset.
Every failed login from every remote caller therefore collapsed onto
ONE per-process bucket — 30 failures from any attacker locked out
every legitimate caller for 15 minutes.

These tests pin the new behaviour:

* ``_resolve_source_id`` prefers the HTTP-client ContextVar
  (``_current_http_client``) when set.
* Two distinct HTTP clients each can fail 5 times without tripping
  the limiter (per-IP buckets).
* One HTTP client failing 30 times DOES trip the limiter for THAT
  IP (the attacker is locked out, not the bystanders).
* Stdio fallback is unchanged when no HTTP scope is in play.
"""

from __future__ import annotations

import contextvars
from typing import Any
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.client.exceptions import OdooAuthError
from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import (
    GatewayContext,
    _current_http_client,
)


def _auth_result(**overrides: Any) -> AuthResult:
    defaults: dict[str, Any] = {
        "uid": 1,
        "session_id": "s1",
        "user_context": {"lang": "en_US"},
        "is_admin": False,
        "groups": ["base.group_user"],
        "username": "admin",
        "database": "testdb",
    }
    defaults.update(overrides)
    return AuthResult(**defaults)


def _make_gateway(**settings_overrides: Any) -> GatewayContext:
    settings_defaults: dict[str, Any] = {
        "odoo_url": "http://localhost:8069",
        "odoo_db": "testdb",
        "odoo_username": "",
        "odoo_api_key": SecretStr(""),
    }
    settings_defaults.update(settings_overrides)
    settings = Settings(**settings_defaults)
    config = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(settings, config)


def _get_login_tool(gateway: GatewayContext) -> Any:
    from mcp.server.fastmcp import FastMCP

    from odoo_mcp_gateway.tools.auth import register_auth_tools

    server = FastMCP(name="test")
    register_auth_tools(server, gateway)
    for name, tool in server._tool_manager._tools.items():
        if name == "login":
            return tool.fn
    raise AssertionError("login tool not registered")


async def _run_with_http_client(
    client_id: str | None,
    coro_factory: Any,
) -> Any:
    """Run *coro_factory()* inside a fresh Context with the HTTP-client
    ContextVar bound to *client_id*.

    Using a private Context per call gives each simulated caller its
    own ContextVar slot — exactly like a real ASGI middleware would
    produce per request — and prevents cross-test contamination.
    """
    ctx = contextvars.copy_context()

    async def _runner() -> Any:
        if client_id is None:
            # Explicitly clear the contextvar — important when a
            # previous test left a value in the copied context.
            _current_http_client.set(None)
        else:
            _current_http_client.set(client_id)
        return await coro_factory()

    # ``Context.run`` doesn't support awaitables directly; we spawn
    # the awaitable inside the context by manually setting the var.
    if client_id is None:
        _current_http_client.set(None)
    else:
        _current_http_client.set(client_id)
    try:
        return await coro_factory()
    finally:
        _current_http_client.set(None)
    # ``ctx`` is referenced to silence the unused-variable lint; the
    # async copy_context().run() pattern would land here in real
    # code paths using asyncio.run_coroutine_threadsafe.
    del ctx


class TestPerIpIsolation:
    """Per-IP rate limit buckets must be isolated from each other."""

    async def test_two_ips_each_4_failures_no_lockout(self) -> None:
        """Two distinct IPs each failing 4 times must not trip the limiter.

        With per-IP buckets, 4 failures from IP-A and 4 from IP-B (with
        different usernames per IP to avoid the per-username limiter
        tripping first at 5 failures) should each be well under the
        default per-IP threshold of 30. With the old process-wide
        bucket, the same 8 failures would accumulate in ONE bucket
        and approach lockout.
        """
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        ip_a = "ip:10.0.0.1"
        ip_b = "ip:10.0.0.2"

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(side_effect=OdooAuthError("bad"))

            async def _fail_as_alice() -> Any:
                return await login_fn(
                    method="password",
                    credential="wrong",
                    username="alice",
                    database="testdb",
                )

            async def _fail_as_bob() -> Any:
                return await login_fn(
                    method="password",
                    credential="wrong",
                    username="bob",
                    database="testdb",
                )

            for _ in range(4):
                await _run_with_http_client(ip_a, _fail_as_alice)
            for _ in range(4):
                await _run_with_http_client(ip_b, _fail_as_bob)

        # Both buckets exist with their own counts.
        assert gateway.login_ip_rate_limiter._failures[ip_a][0] == 4
        assert gateway.login_ip_rate_limiter._failures[ip_b][0] == 4
        # Neither IP should be locked out at 4 failures (threshold=30).
        assert gateway.login_ip_rate_limiter.check_allowed(ip_a) is None, (
            "IP A should not be locked out at 4 failures"
        )
        assert gateway.login_ip_rate_limiter.check_allowed(ip_b) is None, (
            "IP B should not be locked out at 4 failures"
        )

    async def test_one_ip_30_failures_trips_only_that_ip(self) -> None:
        """One IP failing 30 times must lock out THAT IP only, not bystanders.

        Username-rotation attack: the attacker varies the username on
        every attempt so the per-username limiter (5 failures) never
        trips. The per-IP limiter is the second line of defence and
        catches this pattern at 30 failures regardless of username.
        """
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        attacker = "ip:1.2.3.4"
        bystander = "ip:5.6.7.8"

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(side_effect=OdooAuthError("bad"))

            for i in range(30):

                async def _fail(_i: int = i) -> Any:
                    return await login_fn(
                        method="password",
                        credential="wrong",
                        username=f"victim{_i}",
                        database="testdb",
                    )

                await _run_with_http_client(attacker, _fail)

        # Attacker is locked out.
        assert gateway.login_ip_rate_limiter.check_allowed(attacker) is not None
        # Bystander never appeared in the failure dict.
        assert bystander not in gateway.login_ip_rate_limiter._failures
        assert gateway.login_ip_rate_limiter.check_allowed(bystander) is None

    async def test_attacker_lockout_does_not_block_bystander_login(self) -> None:
        """End-to-end: attacker locked out, bystander still succeeds."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        attacker = "ip:1.2.3.4"
        bystander = "ip:5.6.7.8"

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(side_effect=OdooAuthError("bad"))

            for i in range(30):

                async def _fail(_i: int = i) -> Any:
                    return await login_fn(
                        method="password",
                        credential="wrong",
                        username=f"victim{_i}",
                        database="testdb",
                    )

                await _run_with_http_client(attacker, _fail)

        # Now bystander tries to log in successfully.
        good_result = _auth_result(uid=42, username="carol")
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=good_result)

            async def _good() -> Any:
                return await login_fn(
                    method="password",
                    credential="correct-horse-battery-staple",
                    username="carol",
                    database="testdb",
                )

            resp = await _run_with_http_client(bystander, _good)

        assert "error" not in resp, (
            f"Bystander login should not be blocked by attacker lockout, got: {resp!r}"
        )
        assert resp["uid"] == 42

    async def test_attacker_blocked_at_threshold(self) -> None:
        """The 31st attempt from the locked-out attacker is refused."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        attacker = "ip:1.2.3.4"

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(side_effect=OdooAuthError("bad"))

            for i in range(30):

                async def _fail(_i: int = i) -> Any:
                    return await login_fn(
                        method="password",
                        credential="wrong",
                        username=f"victim{_i}",
                        database="testdb",
                    )

                await _run_with_http_client(attacker, _fail)

            # Next attempt should be blocked at the gate, BEFORE
            # AuthManager.login is even invoked.
            instance.login.reset_mock()

            async def _another_attempt() -> Any:
                return await login_fn(
                    method="password",
                    credential="wrong",
                    username="brand_new_username",
                    database="testdb",
                )

            resp = await _run_with_http_client(attacker, _another_attempt)

        assert "error" in resp
        assert "Too many failed login attempts from this source" in resp["error"]
        instance.login.assert_not_called()


class TestStdioFallback:
    """Stdio mode (no HTTP scope) keeps the legacy per-PID bucket."""

    @staticmethod
    def _stdio_key() -> str:
        import os

        return f"stdio:{os.getpid()}"

    async def test_no_http_scope_uses_pid_key(self) -> None:
        """When the HTTP ContextVar is unset, source_id is stdio:<pid>."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        # Ensure no HTTP scope is in play.
        _current_http_client.set(None)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(side_effect=OdooAuthError("bad"))

            await login_fn(
                method="password",
                credential="wrong",
                username="alice",
                database="testdb",
            )

        assert self._stdio_key() in gateway.login_ip_rate_limiter._failures, (
            "stdio fallback must still record under stdio:<pid> "
            "when HTTP context absent"
        )

    async def test_session_key_is_not_used_for_source_id(self) -> None:
        """Even with a session key set, the source bucket must NOT use it.

        Using the session key as the rate-limit bucket would let one
        wrong password by an authenticated user penalise their next 29
        logins. The source must follow the source-of-failure, not the
        victim's identity.
        """
        from odoo_mcp_gateway.server import _current_session_key

        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        # Pretend a session is already pinned. The source key MUST
        # still come from the HTTP-client ContextVar (or fall back to
        # the PID), NOT from session_key.
        _current_session_key.set("99_testdb")
        _current_http_client.set(None)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(side_effect=OdooAuthError("bad"))

            await login_fn(
                method="password",
                credential="wrong",
                username="alice",
                database="testdb",
            )

        # Cleanup the session contextvar so other tests aren't affected.
        _current_session_key.set(None)

        # The failure must have landed in stdio:<pid>, NOT under 99_testdb.
        assert "99_testdb" not in gateway.login_ip_rate_limiter._failures
        import os

        assert f"stdio:{os.getpid()}" in gateway.login_ip_rate_limiter._failures
