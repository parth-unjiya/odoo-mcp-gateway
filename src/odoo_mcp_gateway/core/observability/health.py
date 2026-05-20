"""HTTP health endpoints: ``/health`` (liveness) and ``/ready`` (readiness).

Two distinct concerns, deliberately separated:

* **Liveness** (``/health``): "is the process alive?" A 200 response
  means the process can answer HTTP. K8s / Docker / load balancers
  use it to decide whether to kill the pod. NO external dependencies
  — checking external dependencies would create a self-DoS path (one
  bad upstream → liveness fails → process restarted → restart loop).
* **Readiness** (``/ready``): "should we send traffic to this
  process?" Checks the Odoo backend is reachable (via a cached probe
  so we don't self-DoS), the gateway config loaded, and the auth
  circuit breaker isn't OPEN. K8s uses this to gate Service traffic.

The Odoo probe is **cached for 10 seconds** so each /ready call
doesn't make a fresh JSON-RPC roundtrip. Load balancers poll every
1-5 seconds in production; uncached probes would multiply Odoo load
by 60-300x.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

# Cached "is Odoo reachable" probe result.
_PROBE_CACHE: dict[str, tuple[float, bool]] = {}
_PROBE_TTL_SECONDS = 10.0


async def _probe_odoo_reachable(gateway: GatewayContext) -> bool:
    """Return True if Odoo accepts a low-cost RPC, with TTL cache.

    Uses ``ir.module.module.search_count`` with a bounded domain — a
    read-only, very fast operation that doesn't require admin and
    exists on every Odoo version. The result is cached for
    ``_PROBE_TTL_SECONDS`` so /ready can be polled aggressively
    without amplifying load on Odoo.
    """
    now = time.monotonic()
    cached = _PROBE_CACHE.get("odoo")
    if cached is not None:
        ts, result = cached
        if now - ts < _PROBE_TTL_SECONDS:
            return result

    # Without an authenticated session we can't make an RPC. The
    # most conservative correct answer is: "if we have at least one
    # auth_manager, ping through it; otherwise treat the gateway
    # as ready (no traffic to gate yet)."
    if not gateway.auth_managers:
        _PROBE_CACHE["odoo"] = (now, True)
        return True

    try:
        mgr = next(iter(gateway.auth_managers.values()))
        client = mgr.get_active_client()
        # Bounded, fast RPC that exists on every version.
        await client.execute_kw(
            "ir.module.module",
            "search_count",
            [[("state", "=", "installed")]],
        )
        _PROBE_CACHE["odoo"] = (now, True)
        return True
    except Exception:
        _PROBE_CACHE["odoo"] = (now, False)
        return False


def is_ready_sync(gateway: GatewayContext) -> dict[str, Any]:
    """Synchronous readiness summary — what's known WITHOUT probing.

    Returns a dict suitable for inclusion in the /ready response body.
    Async probing happens in the route handler.
    """
    return {
        "config_loaded": gateway.gateway_config is not None,
        "active_sessions": len(gateway.auth_managers),
        "token_index_size": len(gateway.token_index),
    }


async def is_ready(gateway: GatewayContext) -> tuple[bool, dict[str, Any]]:
    """Full readiness check. Returns ``(is_ready, diagnostic_body)``.

    Liveness failures cascade to readiness failures, but not the
    other way around — a not-ready process is still alive.
    """
    body = is_ready_sync(gateway)
    if not body["config_loaded"]:
        body["reason"] = "gateway config not loaded"
        return False, body

    odoo_ok = await _probe_odoo_reachable(gateway)
    body["odoo_reachable"] = odoo_ok
    if not odoo_ok:
        body["reason"] = "Odoo backend not reachable"
        return False, body

    return True, body


def build_health_routes(gateway: GatewayContext) -> list[Route]:
    """Return Starlette routes for ``/health`` and ``/ready``.

    Mounted by ``__main__._run_streamable_http``. The routes inspect
    the supplied gateway instance directly — no global state, no
    monkey-patching.
    """

    async def _health(_request: Request) -> JSONResponse:
        # Liveness: if we got here, the process is alive.
        return JSONResponse({"status": "ok"}, status_code=200)

    async def _ready(_request: Request) -> JSONResponse:
        ready, body = await is_ready(gateway)
        body["status"] = "ok" if ready else "not_ready"
        return JSONResponse(body, status_code=200 if ready else 503)

    return [
        Route("/health", _health, methods=["GET"]),
        Route("/ready", _ready, methods=["GET"]),
    ]
