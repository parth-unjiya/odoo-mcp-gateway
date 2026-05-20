"""Authentication guard for the ``/metrics`` Prometheus endpoint.

Why this exists (audit blocker #2):

``/metrics`` exposes operational counters (login success/failure
rates, per-tool latency histograms, circuit-breaker state, rate-limit
rejections, ...) that are extremely useful to a defender — and equally
useful to an attacker. Counters reveal:

* Whether a credential-stuffing attack is succeeding (auth_attempts
  with result=success going up after result=failure).
* The shape of the rate-limit bucket (so an attacker can pace their
  attacks under the threshold).
* When Odoo is unavailable (circuit_breaker_state spike).

Therefore /metrics is NOT safe to expose unauthenticated to the open
internet. Operators behind their own network ACL can opt out via
``MCP_METRICS_REQUIRE_AUTH=false``, but the secure default is ON.

The guard is a thin Starlette wrapper. It:

* Requires ``Authorization: Bearer <token>`` matching
  ``settings.metrics_token`` (constant-time compare).
* Returns 503 with a helpful message when the operator has set
  ``metrics_require_auth=true`` but failed to provision a token —
  this is loud and visible at first scrape rather than silently
  accepting any caller.
* Passes through ANYTHING when ``metrics_require_auth=false`` (the
  explicit opt-out for ACL-fronted deployments).

/health and /ready are NOT wrapped — they're load-balancer probes
that must remain anonymous and they're already PII-clean.
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    from odoo_mcp_gateway.config import Settings

logger = logging.getLogger(__name__)

# Body returned with a 503 when the operator hasn't provisioned a
# token but auth is required. Phrased as actionable guidance so the
# first scrape after deploy makes the misconfiguration obvious.
_MISCONFIG_BODY = (
    "/metrics not configured: set MCP_METRICS_TOKEN or set "
    "MCP_METRICS_REQUIRE_AUTH=false"
)


def wrap_metrics_route(route: Route | None, settings: Settings) -> Route | None:
    """Wrap a Prometheus metrics ``Route`` in a bearer-token guard.

    Parameters
    ----------
    route:
        The unwrapped ``/metrics`` route returned by
        :func:`build_metrics_route`. May be ``None`` when
        ``prometheus_client`` isn't installed — in that case we just
        return None and the caller skips mounting metrics entirely
        (preserving existing soft-import behaviour).
    settings:
        The gateway settings. Reads ``metrics_require_auth`` and
        ``metrics_token``.

    Returns
    -------
    Route | None
        A new ``Route`` with the same path/methods whose endpoint
        performs bearer-token validation before delegating to the
        original endpoint. ``None`` is propagated unchanged.
    """
    if route is None:
        return None

    require_auth = bool(getattr(settings, "metrics_require_auth", True))
    raw_token = getattr(settings, "metrics_token", None)
    # Pydantic SecretStr → str. Empty/None means "no token configured".
    token_value: str | None = None
    if raw_token is not None:
        if hasattr(raw_token, "get_secret_value"):
            token_value = raw_token.get_secret_value() or None
        else:
            token_value = str(raw_token) or None

    original_endpoint: Any = route.endpoint
    from starlette.responses import Response
    from starlette.routing import Route

    async def _guarded(request: Request) -> Response:
        # Operator opt-out: pass-through. No-op wrapper.
        if not require_auth:
            return await original_endpoint(request)  # type: ignore[no-any-return]

        # Misconfiguration: auth required but no token provisioned.
        # 503 (not 500) because this is a deployment-state issue, not
        # a bug — the operator can fix it without restarting if their
        # framework supports config reload.
        if not token_value:
            logger.warning(
                "/metrics scrape refused: auth required but no token "
                "configured. Set MCP_METRICS_TOKEN or "
                "MCP_METRICS_REQUIRE_AUTH=false."
            )
            return Response(_MISCONFIG_BODY, status_code=503)

        # Standard Bearer challenge. We do constant-time compare to
        # avoid leaking the length / content of the configured token
        # via response-time side channel.
        header = request.headers.get("authorization", "")
        # Header parsing is split into scheme + value so a typo'd
        # scheme ("token foo") falls through to the same 401 path as
        # a missing header, not a 500.
        scheme, _, candidate = header.partition(" ")
        if scheme.lower() != "bearer" or not candidate:
            return Response(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )
        # Constant-time compare. The two byte strings must be the
        # same length for ``compare_digest`` to give meaningful timing
        # safety — but compare_digest itself handles unequal lengths
        # safely (it just returns False without leaking the length).
        if not hmac.compare_digest(
            candidate.encode("utf-8"),
            token_value.encode("utf-8"),
        ):
            return Response(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )

        return await original_endpoint(request)  # type: ignore[no-any-return]

    return Route("/metrics", _guarded, methods=list(route.methods or ["GET"]))
