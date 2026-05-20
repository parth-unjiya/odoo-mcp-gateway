"""Structured JSON logging via ``structlog`` (optional).

Like the rest of the observability stack, this is gated behind the
``[observability]`` extra. If ``structlog`` isn't installed,
``configure_structlog`` is a no-op and the gateway continues using
the stdlib ``logging`` configured in ``__main__.py``.

Why structlog over plain logging:

* JSON output by default — friendly to log aggregators (Loki,
  Elasticsearch, Datadog).
* ContextVar-aware — the request's session_key, trace_id, and tool
  name can be bound once at request entry and auto-injected into
  every log line in that task.
* Compatible with OTel propagation processors when tracing arrives
  in Sprint 5 (just add ``structlog_otel`` processor).

The configuration here is intentionally minimal — we don't try to
replace the stdlib logging entirely; we wrap it. Application code
that uses ``logging.getLogger(__name__).info(...)`` keeps working;
new call sites that want structured fields use
``structlog.get_logger().info("event", session=session_key, ...)``.
"""

from __future__ import annotations

from typing import Any

# Soft import — observability stack is optional.
try:
    import structlog  # type: ignore[import-not-found, unused-ignore]

    STRUCTLOG_AVAILABLE = True
except ImportError:  # pragma: no cover - import-guard branch
    structlog = None  # type: ignore[assignment]
    STRUCTLOG_AVAILABLE = False


def configure_structlog(json_output: bool = True) -> None:
    """Configure structlog to emit JSON-line records with auto-injected
    ContextVar fields (``mcp_session_id``, ``trace_id``, etc.).

    No-op when ``structlog`` isn't installed.

    The ``json_output`` flag is true by default — production
    deployments almost always want JSON. Tests can pass ``False`` to
    get human-readable console output for debugging.
    """
    if not STRUCTLOG_AVAILABLE or structlog is None:
        return

    processors: list[Any] = [
        # Pull bound ContextVars (mcp_session_id, trace_id, etc.) into
        # every event dict automatically.
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:  # pragma: no cover - human-readable mode for dev only
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        # Use the stdlib logging backend so existing
        # ``logging.getLogger(__name__)`` call sites coexist cleanly.
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_request_context(
    session_key: str | None = None,
    trace_id: str | None = None,
    tool: str | None = None,
) -> None:
    """Bind per-request ContextVars so every log line in this task
    automatically carries them.

    No-op when structlog isn't installed. Safe to call at the top of
    every tool handler; the binding is task-local thanks to PEP 567,
    so concurrent requests don't pollute each other's context.
    """
    if not STRUCTLOG_AVAILABLE or structlog is None:
        return
    bindings: dict[str, Any] = {}
    if session_key is not None:
        bindings["mcp_session_id"] = session_key
    if trace_id is not None:
        bindings["trace_id"] = trace_id
    if tool is not None:
        bindings["tool"] = tool
    if bindings:
        structlog.contextvars.bind_contextvars(**bindings)


def clear_request_context() -> None:
    """Clear ContextVars bound by ``bind_request_context``.

    Call from the request-cleanup path (e.g. middleware ``finally``)
    so the next request starts with a clean slate.
    """
    if not STRUCTLOG_AVAILABLE or structlog is None:
        return
    structlog.contextvars.clear_contextvars()
