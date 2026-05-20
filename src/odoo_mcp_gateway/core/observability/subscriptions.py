"""Resource subscription state tracker (ADR Sprint 5 stretch).

The MCP spec defines ``resources/subscribe`` and
``resources/unsubscribe`` so clients can express "tell me when this
resource changes." When a subscribed resource updates, the server
sends ``notifications/resources/updated`` carrying the URI.

This module is the in-process state — which URIs has each session
subscribed to. The notification CHANNEL (how subscribed clients
actually receive the push) lands in v0.4.0 with the Odoo bus
integration (ADR-V040 webhooks). v0.3.0 ships the tracker so plugin
authors and Sprint 6 release-time tooling can rely on a stable API
surface even though the push side is still empty.

Design:

* Subscriptions are keyed by ``(session_key, uri)``. Two sessions
  subscribing to the same URI get independent notifications.
* The tracker is **process-local**. A multi-process deployment would
  need a shared backend (Redis, Postgres LISTEN/NOTIFY); we don't
  ship one in v0.3.0 since multi-tenant SaaS is explicitly out of
  scope.
* ``notify_resource_changed(uri)`` is the future-push hook. Today
  it logs at DEBUG; v0.4.0 will route the notification through the
  active MCP session's notification stream.

Threading: the tracker uses a plain ``dict`` because all access goes
through the same asyncio loop. If we ever move to true threading,
wrap mutations in an ``asyncio.Lock``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)


class SubscriptionTracker:
    """In-process registry of (session_key → set[uri]) subscriptions.

    Mounted on :class:`GatewayContext` as ``gateway.subscriptions``.
    Plugin lifecycle hooks (especially ``on_session_close``) read
    from it to clean up dangling subscriptions when a session ends.
    """

    def __init__(self) -> None:
        # session_key → set of subscribed URIs.
        self._subs: dict[str, set[str]] = {}

    def subscribe(self, session_key: str, uri: str) -> None:
        """Register *session_key*'s interest in *uri*.

        Idempotent — subscribing twice from the same session is a
        no-op (matches the MCP spec's "subscribe" semantics).
        """
        self._subs.setdefault(session_key, set()).add(uri)
        logger.debug("Session %s subscribed to %s", session_key, uri)

    def unsubscribe(self, session_key: str, uri: str) -> bool:
        """Remove *session_key*'s interest in *uri*.

        Returns True if the subscription existed, False otherwise
        (clients sending unsubscribe for an unknown URI shouldn't
        error — the spec says servers SHOULD treat it as a no-op).
        """
        bucket = self._subs.get(session_key)
        if bucket is None or uri not in bucket:
            return False
        bucket.discard(uri)
        if not bucket:
            self._subs.pop(session_key, None)
        logger.debug("Session %s unsubscribed from %s", session_key, uri)
        return True

    def clear_session(self, session_key: str) -> int:
        """Drop every subscription for *session_key*. Returns count cleared.

        Called from ``on_session_close`` lifecycle hooks so dangling
        subscriptions don't keep the tracker growing.
        """
        bucket = self._subs.pop(session_key, None)
        return len(bucket) if bucket else 0

    def subscribers(self, uri: str) -> list[str]:
        """Return session_keys currently subscribed to *uri*."""
        return [sk for sk, uris in self._subs.items() if uri in uris]

    def all_uris_for_session(self, session_key: str) -> list[str]:
        """Return URIs *session_key* is currently subscribed to."""
        return sorted(self._subs.get(session_key, ()))

    def __len__(self) -> int:
        return sum(len(uris) for uris in self._subs.values())


async def notify_resource_changed(
    gateway: GatewayContext,
    uri: str,
) -> int:
    """Notify subscribed sessions that *uri* changed.

    v0.3.0 scope: identify subscribers and log. The actual
    ``notifications/resources/updated`` push channel ships in
    v0.4.0 alongside Odoo bus integration. The function returns
    the count of (would-be) notifications so callers / tests can
    confirm the subscription state is correct.
    """
    tracker = getattr(gateway, "subscriptions", None)
    if tracker is None:
        return 0
    subscribers = tracker.subscribers(uri)
    if subscribers:
        logger.debug(
            "Resource %s changed; %d session(s) subscribed (push deferred to v0.4.0)",
            uri,
            len(subscribers),
        )
    return len(subscribers)
