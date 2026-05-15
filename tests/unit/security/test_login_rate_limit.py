"""Tests for login brute force protection."""
from __future__ import annotations

import time

from odoo_mcp_gateway.core.security.rate_limit import (
    LoginIpRateLimiter,
    LoginRateLimiter,
)


class TestLoginRateLimiter:
    def test_first_attempt_allowed(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, lockout_seconds=60)
        assert limiter.check_allowed("admin") is None

    def test_under_limit_allowed(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, lockout_seconds=60)
        limiter.record_failure("admin")
        limiter.record_failure("admin")
        assert limiter.check_allowed("admin") is None

    def test_at_limit_locked_out(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, lockout_seconds=60)
        for _ in range(3):
            limiter.record_failure("admin")
        msg = limiter.check_allowed("admin")
        assert msg is not None
        assert "Too many" in msg
        assert "minute" in msg

    def test_success_resets_counter(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, lockout_seconds=60)
        for _ in range(2):
            limiter.record_failure("admin")
        limiter.record_success("admin")
        # Should be reset - can fail again
        for _ in range(2):
            limiter.record_failure("admin")
        assert limiter.check_allowed("admin") is None

    def test_lockout_expires(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, lockout_seconds=1)
        for _ in range(3):
            limiter.record_failure("admin")
        # Wait for lockout to expire
        time.sleep(1.1)
        assert limiter.check_allowed("admin") is None

    def test_case_insensitive(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, lockout_seconds=60)
        limiter.record_failure("Admin")
        limiter.record_failure("ADMIN")
        limiter.record_failure("admin")
        msg = limiter.check_allowed("aDmIn")
        assert msg is not None

    def test_different_users_independent(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, lockout_seconds=60)
        for _ in range(3):
            limiter.record_failure("admin")
        # admin is locked out
        assert limiter.check_allowed("admin") is not None
        # demo is not
        assert limiter.check_allowed("demo") is None

    def test_empty_username_always_allowed(self) -> None:
        limiter = LoginRateLimiter(max_failures=1, lockout_seconds=60)
        limiter.record_failure("")
        assert limiter.check_allowed("") is None

    def test_default_limits(self) -> None:
        limiter = LoginRateLimiter()
        assert limiter._max_failures == 5
        assert limiter._lockout_seconds == 300


class TestLockoutNotExtended:
    def test_lockout_duration_fixed_after_threshold(self) -> None:
        """Failures during active lockout do NOT extend the lockout."""
        limiter = LoginRateLimiter(max_failures=3, lockout_seconds=2)
        for _ in range(3):
            limiter.record_failure("victim")
        # Locked out
        assert limiter.check_allowed("victim") is not None
        time.sleep(1)
        # Attacker tries again — should NOT extend
        limiter.record_failure("victim")
        time.sleep(1.2)  # Total elapsed > 2s, lockout should expire
        assert limiter.check_allowed("victim") is None

    def test_cleanup_evicts_when_over_max_entries(self) -> None:
        """When _failures exceeds _max_entries, oldest entries evicted."""
        limiter = LoginRateLimiter(max_failures=2, lockout_seconds=300)
        limiter._max_entries = 5
        # Fill with 6 unique users - 6th triggers cleanup
        for i in range(6):
            limiter.record_failure(f"user{i}")
        # Should have at most 5 entries (oldest evicted)
        assert len(limiter._failures) <= 5

    def test_cleanup_removes_expired_lockouts(self) -> None:
        """Expired lockouts are pruned during cleanup."""
        limiter = LoginRateLimiter(max_failures=2, lockout_seconds=1)
        limiter.record_failure("expired_user")
        limiter.record_failure("expired_user")  # Now locked out
        time.sleep(1.2)  # Lockout expired
        # Trigger cleanup via another record_failure
        limiter.record_failure("fresh_user")
        # expired_user should be cleaned up
        expired = limiter._failures.get("expired_user")
        assert expired is None or expired[0] < 2


class TestLoginIpRateLimiter:
    def test_first_attempt_allowed(self) -> None:
        limiter = LoginIpRateLimiter(max_failures=5, lockout_seconds=60)
        assert limiter.check_allowed("127.0.0.1") is None

    def test_locked_out_after_threshold(self) -> None:
        limiter = LoginIpRateLimiter(max_failures=5, lockout_seconds=60)
        for _ in range(5):
            limiter.record_failure("attacker_ip")
        msg = limiter.check_allowed("attacker_ip")
        assert msg is not None
        assert "Too many" in msg or "wait" in msg.lower()

    def test_different_ips_independent(self) -> None:
        limiter = LoginIpRateLimiter(max_failures=2, lockout_seconds=60)
        for _ in range(2):
            limiter.record_failure("attacker")
        assert limiter.check_allowed("attacker") is not None
        assert limiter.check_allowed("victim_ip") is None

    def test_success_resets(self) -> None:
        limiter = LoginIpRateLimiter(max_failures=2, lockout_seconds=60)
        limiter.record_failure("source")
        limiter.record_success("source")
        assert limiter.check_allowed("source") is None

    def test_default_limits_higher_than_username(self) -> None:
        ip_limiter = LoginIpRateLimiter()
        assert ip_limiter._max_failures == 30
        assert ip_limiter._lockout_seconds == 900
