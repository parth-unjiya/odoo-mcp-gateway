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


class LoginRateLimiter:
    """Tracks failed login attempts and enforces lockout.

    After ``max_failures`` consecutive failures for a username,
    further attempts are blocked for ``lockout_seconds``.
    A successful login resets the counter.
    """

    _max_entries: int = 10_000

    def __init__(
        self,
        max_failures: int = 5,
        lockout_seconds: int = 300,
    ) -> None:
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        # username -> (failure_count, last_failure_timestamp)
        self._failures: dict[str, tuple[int, float]] = {}

    def _cleanup(self) -> None:
        """Evict expired and oldest entries when the dict exceeds max size."""
        now = time.monotonic()
        # First pass: remove expired lockouts
        expired = [
            k for k, (count, ts) in self._failures.items()
            if count >= self._max_failures and now - ts > self._lockout_seconds
        ]
        for k in expired:
            del self._failures[k]

        # Second pass: evict oldest if still over limit
        if len(self._failures) > self._max_entries:
            sorted_keys = sorted(
                self._failures, key=lambda k: self._failures[k][1]
            )
            for k in sorted_keys[: len(self._failures) - self._max_entries]:
                del self._failures[k]

    def check_allowed(self, username: str) -> str | None:
        """Check if login is allowed.

        Returns error message if locked out, None if OK.
        """
        if not username:
            return None
        key = username.lower().strip()
        if key not in self._failures:
            return None
        count, last_failure = self._failures[key]
        if count < self._max_failures:
            return None
        elapsed = time.monotonic() - last_failure
        remaining = self._lockout_seconds - elapsed
        if remaining <= 0:
            # Lockout expired, reset
            del self._failures[key]
            return None
        minutes = int(remaining // 60) + 1
        return (
            f"Too many failed login attempts for '{username}'. "
            f"Please wait {minutes} minute(s) before trying again."
        )

    def record_failure(self, username: str) -> None:
        """Record a failed login attempt.

        If the user is already locked out (count >= max_failures and the
        lockout window hasn't expired), do NOT update the timestamp. This
        prevents an attacker from perpetually extending another user's
        lockout via repeated failed attempts (DoS primitive).
        """
        if not username:
            return
        key = username.lower().strip()
        count, last_failure = self._failures.get(key, (0, 0.0))
        # If already locked out, don't extend the lockout — keep the
        # original timestamp so the lockout duration stays fixed.
        if count >= self._max_failures:
            elapsed = time.monotonic() - last_failure
            if elapsed < self._lockout_seconds:
                return  # Lockout in progress, don't extend it
        self._failures[key] = (count + 1, time.monotonic())
        # Cleanup AFTER insertion so the entry count reflects the new
        # state (otherwise eviction lags by one insert).
        self._cleanup()

    def record_success(self, username: str) -> None:
        """Reset failure counter on successful login."""
        if not username:
            return
        key = username.lower().strip()
        self._failures.pop(key, None)


class LoginIpRateLimiter:
    """Tracks failed login attempts per source (IP/connection) to prevent
    username-rotation attacks.

    The per-username ``LoginRateLimiter`` alone is bypassed by an attacker
    who rotates usernames (e.g. 4 attempts each across 1000 usernames).
    This limiter tracks failures per source identifier (IP, session_id, or
    any caller-provided key) with a higher threshold than per-username,
    since multiple legitimate users may share an IP behind NAT.
    """

    _max_entries: int = 10_000

    def __init__(
        self,
        max_failures: int = 30,
        lockout_seconds: int = 900,  # 15 minutes
    ) -> None:
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        # source_id -> (failure_count, last_failure_timestamp)
        self._failures: dict[str, tuple[int, float]] = {}

    def _cleanup(self) -> None:
        """Evict expired and oldest entries when the dict exceeds max size."""
        now = time.monotonic()
        # First pass: remove expired lockouts
        expired = [
            k for k, (count, ts) in self._failures.items()
            if count >= self._max_failures and now - ts > self._lockout_seconds
        ]
        for k in expired:
            del self._failures[k]

        # Second pass: evict oldest if still over limit
        if len(self._failures) > self._max_entries:
            sorted_keys = sorted(
                self._failures, key=lambda k: self._failures[k][1]
            )
            for k in sorted_keys[: len(self._failures) - self._max_entries]:
                del self._failures[k]

    def check_allowed(self, source_id: str) -> str | None:
        """Check if login is allowed from this source.

        Returns error message if the source is locked out, None if OK.
        """
        if not source_id:
            return None
        key = source_id.strip()
        if key not in self._failures:
            return None
        count, last_failure = self._failures[key]
        if count < self._max_failures:
            return None
        elapsed = time.monotonic() - last_failure
        remaining = self._lockout_seconds - elapsed
        if remaining <= 0:
            # Lockout expired, reset
            del self._failures[key]
            return None
        minutes = int(remaining // 60) + 1
        return (
            f"Too many failed login attempts from this source. "
            f"Please wait {minutes} minute(s) before trying again."
        )

    def record_failure(self, source_id: str) -> None:
        """Record a failed login attempt from this source.

        Same fixed-duration lockout semantics as ``LoginRateLimiter``: if
        the source is already locked out, do not extend the lockout.
        """
        if not source_id:
            return
        key = source_id.strip()
        count, last_failure = self._failures.get(key, (0, 0.0))
        if count >= self._max_failures:
            elapsed = time.monotonic() - last_failure
            if elapsed < self._lockout_seconds:
                return  # Lockout in progress, don't extend it
        self._failures[key] = (count + 1, time.monotonic())
        # Cleanup AFTER insertion so the entry count reflects the new state.
        self._cleanup()

    def record_success(self, source_id: str) -> None:
        """Reset failure counter on successful login from this source."""
        if not source_id:
            return
        key = source_id.strip()
        self._failures.pop(key, None)
