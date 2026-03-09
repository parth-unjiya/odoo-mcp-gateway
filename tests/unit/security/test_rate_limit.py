"""Tests for the rate limiter."""

from __future__ import annotations

from unittest.mock import patch

from odoo_mcp_gateway.core.security.rate_limit import RateLimiter, TokenBucket

# ── TokenBucket ────────────────────────────────────────────────────


class TestTokenBucket:
    def test_initial_capacity_available(self) -> None:
        bucket = TokenBucket(rate=1.0, capacity=10)
        allowed, retry_after = bucket.consume(1)
        assert allowed is True
        assert retry_after == 0.0

    def test_exhaust_capacity(self) -> None:
        bucket = TokenBucket(rate=1.0, capacity=3)
        for _ in range(3):
            allowed, _ = bucket.consume(1)
            assert allowed is True

        allowed, retry_after = bucket.consume(1)
        assert allowed is False
        assert retry_after > 0

    def test_consume_multiple_tokens(self) -> None:
        bucket = TokenBucket(rate=1.0, capacity=5)
        allowed, _ = bucket.consume(5)
        assert allowed is True
        allowed, _ = bucket.consume(1)
        assert allowed is False

    def test_refill_over_time(self) -> None:
        bucket = TokenBucket(rate=10.0, capacity=10)
        # Exhaust all tokens
        for _ in range(10):
            bucket.consume(1)

        # Simulate time passing
        with patch("time.monotonic", return_value=bucket._last_refill + 1.0):
            allowed, _ = bucket.consume(1)
            assert allowed is True

    def test_capacity_not_exceeded_after_refill(self) -> None:
        bucket = TokenBucket(rate=100.0, capacity=5)
        # Simulate a long time passing
        with patch("time.monotonic", return_value=bucket._last_refill + 100.0):
            # Even after long time, tokens capped at capacity
            allowed, _ = bucket.consume(5)
            assert allowed is True
            allowed, _ = bucket.consume(1)
            assert allowed is False

    def test_retry_after_calculation(self) -> None:
        bucket = TokenBucket(rate=2.0, capacity=1)
        bucket.consume(1)  # exhaust
        allowed, retry_after = bucket.consume(1)
        assert allowed is False
        assert 0 < retry_after <= 1.0

    def test_zero_rate_infinite_retry(self) -> None:
        bucket = TokenBucket(rate=0.0, capacity=1)
        bucket.consume(1)
        allowed, retry_after = bucket.consume(1)
        assert allowed is False
        assert retry_after == float("inf")


# ── RateLimiter ────────────────────────────────────────────────────


class TestRateLimiter:
    def test_within_global_limit(self) -> None:
        limiter = RateLimiter(global_rate=60, write_rate=20)
        allowed, msg = limiter.check("session-1")
        assert allowed is True
        assert msg == ""

    def test_exceeding_global_limit(self) -> None:
        limiter = RateLimiter(global_rate=3, write_rate=20)
        for _ in range(3):
            allowed, _ = limiter.check("session-1")
            assert allowed is True
        allowed, msg = limiter.check("session-1")
        assert allowed is False
        assert "Rate limit exceeded" in msg

    def test_within_write_limit(self) -> None:
        limiter = RateLimiter(global_rate=60, write_rate=20)
        allowed, msg = limiter.check("session-1", is_write=True)
        assert allowed is True
        assert msg == ""

    def test_exceeding_write_limit(self) -> None:
        limiter = RateLimiter(global_rate=100, write_rate=2)
        for _ in range(2):
            allowed, _ = limiter.check("session-1", is_write=True)
            assert allowed is True
        allowed, msg = limiter.check("session-1", is_write=True)
        assert allowed is False
        assert "Write rate limit exceeded" in msg

    def test_per_session_isolation(self) -> None:
        limiter = RateLimiter(global_rate=2, write_rate=20)
        # Exhaust session-1
        limiter.check("session-1")
        limiter.check("session-1")
        allowed, _ = limiter.check("session-1")
        assert allowed is False

        # session-2 should still work
        allowed, _ = limiter.check("session-2")
        assert allowed is True

    def test_reset_clears_session(self) -> None:
        limiter = RateLimiter(global_rate=2, write_rate=20)
        limiter.check("session-1")
        limiter.check("session-1")
        allowed, _ = limiter.check("session-1")
        assert allowed is False

        limiter.reset("session-1")
        allowed, _ = limiter.check("session-1")
        assert allowed is True

    def test_reset_does_not_affect_other_sessions(self) -> None:
        limiter = RateLimiter(global_rate=2, write_rate=20)
        limiter.check("session-1")
        limiter.check("session-1")
        limiter.check("session-2")
        limiter.check("session-2")

        limiter.reset("session-1")
        allowed, _ = limiter.check("session-1")
        assert allowed is True
        allowed, _ = limiter.check("session-2")
        assert allowed is False

    def test_write_consumes_global_too(self) -> None:
        limiter = RateLimiter(global_rate=2, write_rate=20)
        # Write operations consume both global and write budget
        limiter.check("session-1", is_write=True)
        limiter.check("session-1", is_write=True)
        # Global should be exhausted
        allowed, msg = limiter.check("session-1")
        assert allowed is False
        assert "Rate limit" in msg

    def test_recovery_over_time(self) -> None:
        limiter = RateLimiter(global_rate=60, write_rate=20)
        # Exhaust both buckets
        for _ in range(60):
            limiter.check("sess-1")

        # Simulate time passing by manipulating the bucket
        bucket = limiter._buckets["sess-1"]
        with patch("time.monotonic", return_value=bucket._last_refill + 2.0):
            allowed, _ = limiter.check("sess-1")
            assert allowed is True

    def test_default_rates(self) -> None:
        limiter = RateLimiter()
        # Should allow at least 60 requests
        for _ in range(60):
            allowed, _ = limiter.check("sess-1")
            assert allowed is True

    def test_read_does_not_consume_write_budget(self) -> None:
        limiter = RateLimiter(global_rate=100, write_rate=2)
        # Do many reads
        for _ in range(50):
            limiter.check("sess-1", is_write=False)
        # Writes should still work
        allowed, _ = limiter.check("sess-1", is_write=True)
        assert allowed is True

    def test_reset_nonexistent_session(self) -> None:
        limiter = RateLimiter()
        # Should not raise
        limiter.reset("nonexistent-session")


# ── RateLimiter._cleanup ──────────────────────────────────────────


class TestRateLimiterCleanup:
    """Tests for the _cleanup method of RateLimiter.

    Important implementation detail: _cleanup() is called at the START of
    check(), BEFORE the new session bucket is created. So:
    - After adding N sessions, we have N buckets.
    - Cleanup triggers when len(_buckets) > _max_buckets.
    - Eviction happens on the NEXT check() call after exceeding the limit.
    """

    def test_evicts_oldest_sessions(self) -> None:
        """When bucket count exceeds max, oldest sessions are evicted on next check."""
        limiter = RateLimiter(global_rate=100, write_rate=50)
        limiter._max_buckets = 3  # small for testing

        # Add 4 sessions: after session4, we have 4 buckets (> 3)
        limiter.check("session1")
        limiter.check("session2")
        limiter.check("session3")
        limiter.check("session4")
        # Now _buckets has 4 entries. cleanup hasn't run for 4 yet.
        # Next check triggers cleanup which sees 4 > 3 and evicts 1 (oldest)
        limiter.check("session5")
        # session1 was the oldest, should have been evicted
        assert "session1" not in limiter._buckets
        assert "session5" in limiter._buckets

    def test_no_cleanup_under_limit(self) -> None:
        """No sessions should be evicted when under the max_buckets limit."""
        limiter = RateLimiter(global_rate=100, write_rate=50)
        limiter._max_buckets = 10

        for i in range(5):
            limiter.check(f"session{i}")

        # All 5 should be present
        for i in range(5):
            assert f"session{i}" in limiter._buckets

    def test_no_cleanup_at_limit(self) -> None:
        """No sessions should be evicted when exactly at the limit."""
        limiter = RateLimiter(global_rate=100, write_rate=50)
        limiter._max_buckets = 3

        limiter.check("session1")
        limiter.check("session2")
        limiter.check("session3")

        # Exactly 3 sessions = limit, no eviction
        assert len(limiter._buckets) == 3

    def test_cleanup_removes_from_all_dicts(self) -> None:
        """Cleanup should remove from _buckets, _write_buckets, and _access_times."""
        limiter = RateLimiter(global_rate=100, write_rate=50)
        limiter._max_buckets = 2

        # Add 3 sessions (all with writes to populate _write_buckets)
        limiter.check("sess1", is_write=True)
        limiter.check("sess2", is_write=True)
        limiter.check("sess3", is_write=True)
        # Now 3 buckets (> 2). Trigger cleanup with another check.
        limiter.check("sess4", is_write=True)

        # sess1 (oldest) should be evicted from all tracking dicts
        assert "sess1" not in limiter._buckets
        assert "sess1" not in limiter._write_buckets
        assert "sess1" not in limiter._access_times

    def test_cleanup_preserves_bucket_functionality(self) -> None:
        """Sessions that survive cleanup should still work correctly."""
        limiter = RateLimiter(global_rate=100, write_rate=20)
        limiter._max_buckets = 2

        limiter.check("session1")
        limiter.check("session2")
        limiter.check("session3")
        # Now 3 > 2. Next check triggers cleanup.
        # session2 should survive and still work
        allowed, _ = limiter.check("session2")
        assert allowed is True

    def test_access_time_updated_on_recheck(self) -> None:
        """Re-checking a session should update its access time, preventing eviction."""
        limiter = RateLimiter(global_rate=100, write_rate=50)
        limiter._max_buckets = 2

        limiter.check("session1")
        limiter.check("session2")
        # Re-access session1 so it becomes the most recently accessed
        limiter.check("session1")
        # Add session3 (now we have 3 > 2)
        limiter.check("session3")
        # Next check triggers cleanup; session2 (oldest access) should be evicted
        limiter.check("session4")

        assert "session1" in limiter._buckets
        assert "session2" not in limiter._buckets

    def test_multiple_evictions(self) -> None:
        """When many sessions exceed the limit, multiple should be evicted."""
        limiter = RateLimiter(global_rate=100, write_rate=50)
        limiter._max_buckets = 2

        # Add 5 sessions
        for i in range(5):
            limiter.check(f"session{i}")

        # Now we have 5 buckets. Trigger cleanup.
        limiter.check("session5")
        # Should have evicted down to _max_buckets (2) sessions
        # The most recent 2 (plus the new one) remain
        assert len(limiter._buckets) <= limiter._max_buckets + 1
