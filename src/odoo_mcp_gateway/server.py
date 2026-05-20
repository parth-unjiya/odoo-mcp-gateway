"""MCP server setup with tool registration and session management."""

from __future__ import annotations

import asyncio
import atexit
import contextvars
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.auth.manager import AuthManager
from odoo_mcp_gateway.core.discovery.field_inspector import FieldInspector
from odoo_mcp_gateway.core.discovery.model_registry import ModelRegistry
from odoo_mcp_gateway.core.security.audit import AuditLogger
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    load_config,
)
from odoo_mcp_gateway.core.security.middleware import SecurityMiddleware
from odoo_mcp_gateway.core.security.rate_limit import (
    LoginIpRateLimiter,
    LoginRateLimiter,
    RateLimiter,
)
from odoo_mcp_gateway.core.security.rbac import RBACManager
from odoo_mcp_gateway.core.security.restrictions import RestrictionChecker
from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer
from odoo_mcp_gateway.core.version.adapters import VersionAdapter

logger = logging.getLogger(__name__)

# ContextVar for per-request session isolation in HTTP mode.
#
# WARNING: HTTP mode (streamable-http transport) currently has KNOWN session
# isolation limitations. The contextvar is only set inside the login tool;
# subsequent tool calls in different request contexts may resolve to the
# first available session via _get_client/_get_auth_manager fallback.
#
# For multi-tenant HTTP deployments, proper per-request session resolution
# middleware is required (TODO). Until that is implemented, deploy HTTP mode
# as SINGLE-TENANT only — one user per server process.
#
# stdio mode is single-session by design and not affected by this limitation.
_current_session_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_session_key", default=None
)

# ContextVar that carries the per-request HTTP client identifier
# (typically remote IP, or the first hop of X-Forwarded-For when
# ``gateway.trust_proxy`` is True). It is set by
# :class:`SessionResolverMiddleware` on request entry and reset on
# exit. Tools that want a stable "source" key for rate limiting
# (notably the login tool) read this preferentially so that, in
# HTTP mode, failed-login lockouts are SCOPED PER REMOTE PEER
# rather than collapsing onto a single per-process bucket (which
# would let any attacker DoS every other caller by hammering the
# server with bad credentials).
#
# In stdio mode this stays at the default (None) and callers fall
# back to a ``stdio:<pid>`` source key.
_current_http_client: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_http_client", default=None
)


class GatewayContext:
    """Holds shared state for the gateway: config, security, discovery."""

    def __init__(self, settings: Settings, gateway_config: GatewayConfig) -> None:
        self.settings = settings
        self.gateway_config = gateway_config
        self.auth_managers: dict[str, AuthManager] = {}
        # Lock that serializes mutations of ``auth_managers`` and any
        # session-eviction logic that runs alongside a login. Without
        # this, two concurrent login() calls (or a login() racing with
        # an in-flight tool call) can interleave the "pop prior session
        # + register new session" steps and leave the dict in a state
        # where the calling user resolves to someone else's session.
        # The lock is created lazily on first use because GatewayContext
        # is sometimes constructed outside an event loop (tests).
        self._auth_lock: asyncio.Lock | None = None
        # Opaque-bearer-token → session_key registry. Populated by the
        # ``login`` tool on successful authentication; consulted by
        # ``OdooTokenVerifier`` on every HTTP request. Tokens are
        # 256-bit URL-safe strings (``secrets.token_urlsafe(32)``);
        # rotation happens on re-login (old token revoked, new one
        # issued) so a leaked token's blast radius is bounded by the
        # session lifetime, not forever.
        #
        # Empty in stdio mode — tokens are only issued when the gateway
        # is reachable over HTTP, since the stdio caller is the only
        # client and doesn't need a bearer.
        self.token_index: dict[str, str] = {}
        self.restrictions = RestrictionChecker(
            config=gateway_config.restrictions,
            model_access=gateway_config.model_access,
        )
        self.rbac = RBACManager(
            config=gateway_config.rbac,
            model_access=gateway_config.model_access,
        )
        self.rate_limiter = RateLimiter(
            global_rate=settings.rate_limit_global,
            write_rate=settings.rate_limit_write,
        )
        self.login_rate_limiter = LoginRateLimiter()
        self.login_ip_rate_limiter = LoginIpRateLimiter()
        self.audit_logger = AuditLogger(
            backend="logger",
        )
        self.error_sanitizer = ErrorSanitizer()
        self.middleware = SecurityMiddleware(
            restrictions=self.restrictions,
            rbac=self.rbac,
            rate_limiter=self.rate_limiter,
            audit=self.audit_logger,
            sanitizer=self.error_sanitizer,
        )
        self.model_registry = ModelRegistry(
            model_access_config=gateway_config.model_access.model_dump(),
            blocked_models=gateway_config.restrictions.always_blocked,
        )
        self.field_inspector = FieldInspector(
            cache_ttl=settings.cache_ttl_seconds,
        )
        self._models_discovered = False
        # Version-specific adapter (set during login after version detection).
        # Drives field-rename translation, context normalization, etc.
        self.version_adapter: VersionAdapter | None = None
        # Observability registry — built ONCE per process by
        # ``__main__._run_streamable_http`` when HTTP transport
        # starts. In stdio mode or when ``[observability]`` isn't
        # installed, this stays at the no-op default so every
        # metric call is a free pass. Tools / middleware fetch via
        # ``gateway.metrics`` and never have to None-check the
        # individual counters.
        from odoo_mcp_gateway.core.observability.metrics import MetricsRegistry
        from odoo_mcp_gateway.core.observability.subscriptions import (
            SubscriptionTracker,
        )

        self.metrics: MetricsRegistry = MetricsRegistry()
        # Resource-subscription registry (Sprint 5). Sessions register
        # interest in odoo:// URIs via ``resources/subscribe``; the
        # notification push channel (``notifications/resources/updated``)
        # ships in v0.4.0 alongside Odoo bus integration. v0.3.0 only
        # tracks state so the on_session_close hook can clean up.
        self.subscriptions: SubscriptionTracker = SubscriptionTracker()

    async def cleanup(self) -> None:
        """Close all auth managers and their underlying connections."""
        for key, mgr in list(self.auth_managers.items()):
            try:
                await mgr.close()
            except Exception:
                logger.debug("Error closing auth manager %s", key, exc_info=True)
        self.auth_managers.clear()
        # Clear bearer tokens together with their owning sessions. The
        # tokens are useless without the AuthManager anyway, but
        # leaving them in the index would leak the (token, session_key)
        # mapping to a future debug log or memory dump.
        self.token_index.clear()

    def auth_lock(self) -> asyncio.Lock:
        """Return the lazily-created lock that serializes auth mutations.

        The lock must be acquired around every operation that mutates
        ``self.auth_managers`` together with its session-eviction side
        effects (e.g. popping prior sessions during single-user-per-
        process enforcement) so concurrent callers cannot observe the
        dict mid-swap.
        """
        if self._auth_lock is None:
            self._auth_lock = asyncio.Lock()
        return self._auth_lock

    def issue_bearer_token(self, session_key: str) -> str:
        """Mint a fresh opaque bearer token bound to *session_key*.

        Idempotency: if a previous token exists for *session_key* it is
        revoked first. Callers that re-login as the same user therefore
        always get a brand-new token, and the old token immediately
        stops working — bounding leak blast radius to the time between
        leak and next login.

        Caller is responsible for holding ``self.auth_lock()`` if the
        token issuance is paired with an ``auth_managers`` mutation
        (which it always is in the login flow).
        """
        # Revoke any prior token for this session.
        self.revoke_session_tokens(session_key)
        import secrets

        token = secrets.token_urlsafe(32)
        # 256-bit collision space — birthday probability is irrelevant
        # within a single process lifetime, but assert defensively
        # against the catastrophic case.
        if token in self.token_index:  # pragma: no cover - astronomically unlikely
            token = secrets.token_urlsafe(32)
        self.token_index[token] = session_key
        return token

    def revoke_token(self, token: str) -> bool:
        """Remove a single token from the index. Returns True if removed."""
        return self.token_index.pop(token, None) is not None

    def revoke_session_tokens(self, session_key: str) -> int:
        """Revoke every token bound to *session_key*. Returns count revoked.

        Used when the session itself is being closed (eviction, logout,
        expiry) so dangling tokens don't outlive their AuthManager.
        """
        stale = [t for t, sk in self.token_index.items() if sk == session_key]
        for t in stale:
            del self.token_index[t]
        return len(stale)

    def resolve_token(self, token: str) -> str | None:
        """Look up *token* and return its bound session_key, or None.

        Returns None for unknown tokens AND for tokens whose session
        has been evicted (the index is kept consistent by callers).
        """
        return self.token_index.get(token)

    def sanitize_error(self, exc: Exception) -> str:
        """Sanitize an exception message for client consumption."""
        from odoo_mcp_gateway.client.exceptions import (
            OdooAccessError,
            OdooAuthError,
            OdooMissingError,
            OdooValidationError,
        )

        prefix_map: dict[type, str] = {
            OdooAuthError: "Authentication failed",
            OdooAccessError: "Access denied",
            OdooValidationError: "Validation error",
            OdooMissingError: "Record not found",
        }
        for exc_type, prefix in prefix_map.items():
            if isinstance(exc, exc_type):
                body = self.error_sanitizer.sanitize(str(exc))
                if body and body != "An unexpected error occurred":
                    return f"{prefix}: {body}"
                return prefix

        return self.error_sanitizer.sanitize_exception(exc)


def set_current_session_key(key: str | None) -> None:
    """Set the current session key for the active request context."""
    _current_session_key.set(key)


def get_current_session_key() -> str | None:
    """Get the current session key from the active request context."""
    return _current_session_key.get(None)


def set_current_http_client(client_id: str | None) -> None:
    """Set the current HTTP-client identifier for the active request.

    Wrapper kept for symmetry with :func:`set_current_session_key`. The
    HTTP middleware uses ``ContextVar.set`` directly so it can capture
    the reset token; this helper is the public surface for any other
    code that wants to project an alternate source identity (e.g. a
    test scaffold simulating two distinct callers).
    """
    _current_http_client.set(client_id)


def get_current_http_client() -> str | None:
    """Return the HTTP-client id for the active request, or None.

    None means either the gateway is running in stdio mode (no HTTP
    request scope exists) OR the request middleware did not run yet
    (e.g. background task spawned outside the request task tree).
    """
    return _current_http_client.get(None)


def _resolve_session_auth_manager(gateway: GatewayContext) -> AuthManager:
    """Return the AuthManager for the current request session.

    Resolution mirrors ``plugins.core.helpers._resolve_auth_manager``:

    * If the ``_current_session_key`` ContextVar is SET, the call is
      bound strictly to that key. If the key has gone stale (session
      was popped by re-login or eviction) we raise rather than degrade
      to whichever single manager happens to remain — that residual
      manager almost certainly belongs to a different user, and the
      contextvar being set is the caller's contract that they want
      THAT session.
    * If the ContextVar is NOT set AND exactly ONE session exists,
      use it (stdio mode with single-user-per-process enforcement).
    * Otherwise raise.

    Shared helper for the two thin wrappers below — keeps the
    resolution logic in one place so future changes only have to land
    here.
    """
    if not gateway.auth_managers:
        raise ValueError("Not authenticated. Please call the login tool first.")
    session_key = _current_session_key.get(None)
    if session_key is not None:
        mgr = gateway.auth_managers.get(session_key)
        if mgr is None:
            # Contextvar set but stale — refuse rather than fall through
            # to a residual session that belongs to someone else.
            raise ValueError(
                "Session is no longer active. Please call the login "
                "tool again to re-authenticate."
            )
        return mgr
    # No contextvar — only safe to resolve in the single-session case.
    if len(gateway.auth_managers) == 1:
        return next(iter(gateway.auth_managers.values()))
    raise ValueError(
        "Multiple sessions exist but no request session is bound — "
        "cannot resolve unambiguously. This indicates HTTP multi-tenant "
        "use without per-request session middleware (not yet supported)."
    )


def _get_client(gateway: GatewayContext) -> Any:
    """Get the active authenticated Odoo client for the current session."""
    return _resolve_session_auth_manager(gateway).get_active_client()


def _get_auth_manager(gateway: GatewayContext) -> AuthManager:
    """Get the active AuthManager for the current session."""
    return _resolve_session_auth_manager(gateway)


def get_auth_context(gateway: GatewayContext) -> tuple[Any, int, bool, list[str]]:
    """Atomic capture of (client, uid, is_admin, groups) for the current request.

    Resolves the AuthManager ONCE and reads everything from it — preventing
    a TOCTOU window where ``_get_client`` and ``_get_auth_manager`` could
    each independently re-resolve and land on different sessions. CRUD
    tools should prefer this over the two-call pattern.

    Raises ``ValueError`` (same surface as ``_get_client``) when no
    unambiguous session can be resolved, so callers can handle the
    "not authenticated" case in one ``except`` clause.
    """
    mgr = _resolve_session_auth_manager(gateway)
    client = mgr.get_active_client()
    auth_result = mgr.auth_result
    if auth_result is None:
        raise ValueError("Session has no auth result. Please call login again.")
    return (
        client,
        auth_result.uid,
        auth_result.is_admin,
        list(auth_result.groups),
    )


def _sync_cleanup(gateway: GatewayContext) -> None:
    """Synchronous atexit handler that cleans up auth manager sessions.

    Since auth managers use async httpx clients, we attempt to run
    the async cleanup in an event loop.  If no loop is available
    (e.g. interpreter shutdown), we log a warning instead.
    """
    if not gateway.auth_managers:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Cannot block on a running loop; log a warning.
            logger.warning(
                "Event loop still running at shutdown; "
                "%d auth session(s) may not be closed cleanly",
                len(gateway.auth_managers),
            )
        else:
            loop.run_until_complete(gateway.cleanup())
    except RuntimeError:
        logger.warning(
            "No event loop available at shutdown; %d auth session(s) were not closed",
            len(gateway.auth_managers),
        )


def _build_fastmcp(settings: Settings, gateway: GatewayContext) -> FastMCP:
    """Construct the FastMCP instance, wiring auth when HTTP transport is used.

    Stdio mode runs with NO bearer-token enforcement — env-var credentials
    + the in-tool ``login`` exchange are the auth surface there. HTTP mode
    requires bearer auth on every request; we wire the MCP SDK's
    ``BearerAuthBackend`` + ``AuthContextMiddleware`` pair and supply our
    ``OdooTokenVerifier`` so the SDK consults our token index.

    AuthSettings has two required URLs — ``issuer_url`` and
    ``resource_server_url`` — even though we're not running a full
    OAuth issuer in Sprint 1. We point both at our own gateway URL so
    the SDK's PRM advertisement is internally consistent. ADR-005 will
    replace the issuer with a real IdP URL when OAuth lands.
    """
    if settings.mcp_transport != "streamable-http":
        return FastMCP(
            name="odoo-mcp-gateway",
            host=settings.mcp_host,
            port=settings.mcp_port,
        )

    # HTTP transport: bearer auth required.
    from mcp.server.auth.settings import AuthSettings

    from odoo_mcp_gateway.core.auth.token_verifier import OdooTokenVerifier

    # Self-referential issuer URL for Sprint 1 (opaque-token mode).
    # When OAuth lands in ADR-005, the issuer becomes the IdP's URL.
    gateway_url = f"http://{settings.mcp_host}:{settings.mcp_port}/"
    auth_settings = AuthSettings(
        issuer_url=gateway_url,  # type: ignore[arg-type]
        resource_server_url=gateway_url,  # type: ignore[arg-type]
        required_scopes=["odoo.session"],
    )
    return FastMCP(
        name="odoo-mcp-gateway",
        host=settings.mcp_host,
        port=settings.mcp_port,
        auth=auth_settings,
        token_verifier=OdooTokenVerifier(gateway),
    )


def create_server(settings: Settings) -> FastMCP:
    """Create and configure the MCP server with all tools registered."""
    gateway_config = load_config(settings.config_dir)
    gateway = GatewayContext(settings, gateway_config)
    server = _build_fastmcp(settings, gateway)

    # Register atexit handler so auth manager connections are cleaned up
    # when the server process exits.
    atexit.register(_sync_cleanup, gateway)

    # Import and register all tool groups
    from odoo_mcp_gateway.tools.auth import register_auth_tools
    from odoo_mcp_gateway.tools.bulk import register_bulk_tools
    from odoo_mcp_gateway.tools.crud import register_crud_tools
    from odoo_mcp_gateway.tools.schema import register_schema_tools

    register_auth_tools(server, gateway)
    register_schema_tools(server, gateway)
    register_crud_tools(server, gateway)
    register_bulk_tools(server, gateway)

    # Register MCP Resources and Prompts
    from odoo_mcp_gateway.prompts.handlers import register_prompts
    from odoo_mcp_gateway.resources.handlers import register_resources

    def _get_context() -> GatewayContext:
        return gateway

    register_resources(server, _get_context)
    register_prompts(server, _get_context)

    # Load and register workflow tools (if available)
    try:
        from odoo_mcp_gateway.core.workflow.registry import WorkflowRegistry
        from odoo_mcp_gateway.tools.workflow import register_workflow_tools

        workflow_registry = WorkflowRegistry()
        workflow_registry.load_stock_workflows()
        gateway.workflow_registry = workflow_registry  # type: ignore[attr-defined]
        register_workflow_tools(server, gateway, workflow_registry)
        logger.info("Workflow tools registered")
    except ImportError:
        logger.debug("Workflow module not available, skipping workflow tools")
    except Exception:
        logger.warning("Failed to load workflow tools", exc_info=True)

    # Discover and activate plugins
    from odoo_mcp_gateway.plugins.registry import PluginRegistry

    plugin_registry = PluginRegistry()

    # Discover plugins via entry_points (works when package is installed)
    plugin_registry.discover()
    if not plugin_registry._plugins:
        # Fallback: manual registration if entry points not available
        from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin
        from odoo_mcp_gateway.plugins.core.project import ProjectPlugin
        from odoo_mcp_gateway.plugins.core.sales import SalesPlugin

        plugin_registry.register_plugin(HRPlugin)
        plugin_registry.register_plugin(SalesPlugin)
        plugin_registry.register_plugin(ProjectPlugin)
        plugin_registry.register_plugin(HelpdeskPlugin)

    # Store the registry on the gateway so auth.py can check requirements
    gateway.plugin_registry = plugin_registry  # type: ignore[attr-defined]

    # Activate all enabled plugins
    activated = plugin_registry.activate(server, gateway)
    if activated:
        logger.info("Activated plugins: %s", ", ".join(activated))

    # Attach MCP tool annotations (readOnlyHint / destructiveHint /
    # idempotentHint / openWorldHint) to every registered tool. This
    # runs LAST so it covers core tools + plugin tools in one pass.
    # See tools/annotations.py for the per-tool annotation map.
    from odoo_mcp_gateway.tools.annotations import apply_pending_annotations

    annotation_report = apply_pending_annotations(server)
    missing = [
        n for n, status in annotation_report.items() if status == "missing_from_map"
    ]
    if missing:
        logger.warning(
            "Tools registered without annotation entries (add to "
            "tools/annotations.py): %s",
            ", ".join(sorted(missing)),
        )

    # Stash the gateway on the server so callers (notably the
    # HTTP-transport runner in __main__.py) can pull it out without
    # plumbing it through every function signature.
    server._odoo_gateway = gateway  # type: ignore[attr-defined]

    # Register the MCP completion handler (ADR-009). The handler
    # completes prompt arguments and resource-template arguments for
    # ``model``, ``method``, and ``record_id`` slots — Odoo's biggest
    # UX win since users can't be expected to guess
    # ``res.partner`` vs ``customer``.
    from odoo_mcp_gateway.core.discovery.completions import build_completion_handler

    # FastMCP's completion() decorator is loosely typed in the SDK;
    # the cast suppresses mypy's untyped-call complaint without
    # weakening any of our own typing.
    server.completion()(build_completion_handler(gateway))  # type: ignore[no-untyped-call]

    return server
