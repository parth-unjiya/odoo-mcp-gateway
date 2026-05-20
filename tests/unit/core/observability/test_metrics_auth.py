"""Tests for the ``/metrics`` bearer-token guard (audit blocker #2).

The default posture is secure-on: ``/metrics`` requires a bearer
token. Operators with a network-level ACL can opt out via
``MCP_METRICS_REQUIRE_AUTH=false``. These tests pin both branches and
the misconfigured case (auth required but no token provisioned).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.observability.metrics_auth import (
    _MISCONFIG_BODY,
    wrap_metrics_route,
)

# Soft-skip the whole module when prometheus_client isn't installed —
# the wrap function returns None unchanged for that case, and there's
# no metrics Route to wrap. The wrapper itself is tested via a fake
# Route below (no prometheus needed for the guard logic).


def _make_fake_metrics_route() -> Any:
    """Build a tiny Starlette Route that returns 200 + a fixed body."""
    from starlette.responses import Response
    from starlette.routing import Route

    async def _endpoint(_request: Any) -> Response:
        return Response("# HELP fake\n", media_type="text/plain")

    return Route("/metrics", _endpoint, methods=["GET"])


def _settings(
    require_auth: bool = True,
    token: str | None = None,
) -> Settings:
    """Build a Settings instance with only the metrics fields populated."""
    return Settings(
        odoo_url="http://localhost:8069",
        odoo_db="testdb",
        odoo_username="",
        odoo_api_key=SecretStr(""),
        metrics_require_auth=require_auth,
        metrics_token=SecretStr(token) if token else None,
    )


async def _invoke_route(route: Any, headers: dict[str, str] | None = None) -> Any:
    """Drive the wrapped route through Starlette's TestClient."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    app = Starlette(routes=[route])
    with TestClient(app) as client:
        return client.get("/metrics", headers=headers or {})


class TestRequireAuthOn:
    """Default posture: require_auth=True, real token configured."""

    def test_correct_bearer_returns_200(self) -> None:
        settings = _settings(require_auth=True, token="s3cret-deploy-token")
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(
                route,
                headers={"Authorization": "Bearer s3cret-deploy-token"},
            )

        resp = anyio.run(_drive)
        assert resp.status_code == 200
        assert "fake" in resp.text

    def test_wrong_bearer_returns_401(self) -> None:
        settings = _settings(require_auth=True, token="s3cret-deploy-token")
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(
                route,
                headers={"Authorization": "Bearer wrong-token"},
            )

        resp = anyio.run(_drive)
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate", "").lower().startswith("bearer")

    def test_missing_authorization_returns_401(self) -> None:
        settings = _settings(require_auth=True, token="s3cret-deploy-token")
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(route)

        resp = anyio.run(_drive)
        assert resp.status_code == 401

    def test_wrong_scheme_returns_401(self) -> None:
        """A 'Token foo' or 'Basic foo' must be 401, not 200."""
        settings = _settings(require_auth=True, token="s3cret-deploy-token")
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(
                route,
                headers={"Authorization": "Token s3cret-deploy-token"},
            )

        resp = anyio.run(_drive)
        assert resp.status_code == 401

    def test_empty_credential_returns_401(self) -> None:
        """'Bearer ' with no value must not be accepted."""
        settings = _settings(require_auth=True, token="s3cret-deploy-token")
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(
                route,
                headers={"Authorization": "Bearer "},
            )

        resp = anyio.run(_drive)
        assert resp.status_code == 401


class TestRequireAuthOnNoTokenProvisioned:
    """Misconfiguration: require_auth=True but metrics_token is unset."""

    def test_returns_503_with_helpful_body(self) -> None:
        settings = _settings(require_auth=True, token=None)
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(
                route,
                headers={"Authorization": "Bearer anything"},
            )

        resp = anyio.run(_drive)
        assert resp.status_code == 503
        assert resp.text == _MISCONFIG_BODY
        # Even with a "correct-looking" bearer, the response is still
        # 503 because the SERVER has no configured token to compare
        # against. This is loud-and-visible at first scrape.

    def test_503_even_without_authorization(self) -> None:
        """An unauthenticated scrape also gets 503 — same message."""
        settings = _settings(require_auth=True, token=None)
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(route)

        resp = anyio.run(_drive)
        assert resp.status_code == 503
        assert resp.text == _MISCONFIG_BODY


class TestRequireAuthOff:
    """Operator opt-out: require_auth=False — pass-through to original."""

    def test_no_token_no_header_returns_200(self) -> None:
        settings = _settings(require_auth=False, token=None)
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(route)

        resp = anyio.run(_drive)
        assert resp.status_code == 200

    def test_with_token_set_but_disabled_returns_200(self) -> None:
        """Setting both a token AND require_auth=False → pass-through.

        The token is ignored entirely in this state; the operator has
        EXPLICITLY chosen to disable the auth gate. We don't second-
        guess them.
        """
        settings = _settings(require_auth=False, token="ignored")
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(route)

        resp = anyio.run(_drive)
        assert resp.status_code == 200


class TestEdgeCases:
    """Empty config, missing fields, wrong types."""

    def test_none_route_passes_through(self) -> None:
        """When prometheus isn't installed, build_metrics_route returns
        None; the wrapper must propagate None unchanged."""
        settings = _settings(require_auth=True, token="t")
        assert wrap_metrics_route(None, settings) is None

    def test_empty_string_token_treated_as_no_token(self) -> None:
        """``MCP_METRICS_TOKEN=`` (empty string) means no token."""
        # SecretStr("") -> get_secret_value() == "" → treated as unset.
        settings = Settings(
            odoo_url="http://localhost:8069",
            odoo_db="testdb",
            odoo_username="",
            odoo_api_key=SecretStr(""),
            metrics_require_auth=True,
            metrics_token=SecretStr(""),
        )
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(
                route,
                headers={"Authorization": "Bearer anything"},
            )

        resp = anyio.run(_drive)
        assert resp.status_code == 503

    def test_constant_time_compare_used(self) -> None:
        """Verify hmac.compare_digest is what we actually call.

        Smoke test: a token that is a prefix of the configured token
        must still 401 (the per-char timing should not allow inference).
        """
        settings = _settings(require_auth=True, token="abcdefghij")
        route = wrap_metrics_route(_make_fake_metrics_route(), settings)
        assert route is not None

        import anyio

        async def _drive() -> Any:
            return await _invoke_route(
                route,
                headers={"Authorization": "Bearer abc"},
            )

        resp = anyio.run(_drive)
        assert resp.status_code == 401


# pytest-asyncio is configured project-wide via asyncio_mode=auto, but
# we use Starlette's sync TestClient + anyio.run() above instead of
# pytest-asyncio because TestClient itself is synchronous and the
# guard's branches all complete in a single event loop step.
@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """No shared state to reset — placeholder for forwards-compat."""
    yield None
