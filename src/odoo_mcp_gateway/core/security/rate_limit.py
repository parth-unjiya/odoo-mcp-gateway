"""Rate limiter using token-bucket algorithm."""

from __future__ import annotations

import time


class TokenBucket:
    """Token-bucket rate limiter.

    Tokens are added at a steady rate and consumed on each request.
    When the bucket is empty, requests are rejected.
    """

    def __init__(self, rate: float, capacity: int) -> None:
        """Initialize a token bucket.

        Args:
            rate: Tokens added per second.
            capacity: Maximum tokens the bucket can hold.
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()

    def consume(self, tokens: int = 1) -> tuple[bool, float]:
        """Attempt to consume tokens from the bucket.

        Returns:
            Tuple of (allowed, retry_after_seconds).
            If allowed is True, retry_after is 0.0.
            If allowed is False, retry_after is the estimated wait time.
        """
        now = time.monotonic()
        self._refill(now)

        if self._tokens >= tokens:
            self._tokens -= tokens
            return True, 0.0

        # Calculate how long until enough tokens are available
        deficit = tokens - self._tokens
        retry_after = deficit / self._rate if self._rate > 0 else float("inf")
        return False, retry_after

    def _refill(self, now: float) -> None:
        """Add tokens based on elapsed time since last refill."""
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return

        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * self._rate,
        )
        self._last_refill = now


class RateLimiter:
    """Per-session rate limiter with separate global and write budgets.

    Each session gets its own pair of token buckets: one for all requests
    and one specifically for write operations (create, write, delete).
    """

    _max_buckets: int = 10000

    def __init__(
        self,
        global_rate: int = 60,
        write_rate: int = 20,
    ) -> None:
        """Initialize the rate limiter.

        Args:
            global_rate: Maximum requests per minute (all operations).
            write_rate: Maximum write operations per minute.
        """
        self._global_rate_per_sec = global_rate / 60.0
        self._global_capacity = global_rate
        self._write_rate_per_sec = write_rate / 60.0
        self._write_capacity = write_rate
        self._buckets: dict[str, TokenBucket] = {}
        self._write_buckets: dict[str, TokenBucket] = {}
        self._access_times: dict[str, float] = {}

    def _cleanup(self) -> None:
        """Evict oldest sessions when bucket count exceeds the maximum."""
        if len(self._buckets) <= self._max_buckets:
            return

        # Sort sessions by last access time (oldest first) and remove excess
        sorted_sessions = sorted(self._access_times, key=self._access_times.get)  # type: ignore[arg-type]
        to_remove = len(self._buckets) - self._max_buckets
        for session_id in sorted_sessions[:to_remove]:
            self._buckets.pop(session_id, None)
            self._write_buckets.pop(session_id, None)
            self._access_times.pop(session_id, None)

    def check(self, session_id: str, is_write: bool = False) -> tuple[bool, str]:
        """Check if the request is allowed under rate limits.

        Returns:
            Tuple of (allowed, error_message).
            If allowed is True, error_message is empty.
        """
        self._cleanup()

        # Ensure buckets exist
        if session_id not in self._buckets:
            self._buckets[session_id] = TokenBucket(
                rate=self._global_rate_per_sec,
                capacity=self._global_capacity,
            )
        if session_id not in self._write_buckets:
            self._write_buckets[session_id] = TokenBucket(
                rate=self._write_rate_per_sec,
                capacity=self._write_capacity,
            )

        global_bucket = self._buckets[session_id]
        write_bucket = self._write_buckets[session_id]

        # Check availability BEFORE consuming (avoid draining global on write denial)
        now = time.monotonic()
        global_bucket._refill(now)
        if global_bucket._tokens < 1:
            deficit = 1 - global_bucket._tokens
            rate = global_bucket._rate
            retry = deficit / rate if rate > 0 else float("inf")
            return False, (f"Rate limit exceeded. Retry after {retry:.1f} seconds.")

        if is_write:
            write_bucket._refill(now)
            if write_bucket._tokens < 1:
                deficit = 1 - write_bucket._tokens
                rate = write_bucket._rate
                retry = deficit / rate if rate > 0 else float("inf")
                return False, (
                    f"Write rate limit exceeded. Retry after {retry:.1f} seconds."
                )

        # Both checks passed — consume tokens
        global_bucket._tokens -= 1
        if is_write:
            write_bucket._tokens -= 1

        # Only record access time on successful (allowed) requests
        self._access_times[session_id] = time.monotonic()

        return True, ""

    def reset(self, session_id: str) -> None:
        """Reset rate limit state for a session."""
        self._buckets.pop(session_id, None)
        self._write_buckets.pop(session_id, None)
        self._access_times.pop(session_id, None)
