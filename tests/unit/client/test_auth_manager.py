"""Tests for the AuthManager."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.client.exceptions import OdooAuthError
from odoo_mcp_gateway.client.jsonrpc import JsonRpcClient
from odoo_mcp_gateway.client.xmlrpc import XmlRpcClient
from odoo_mcp_gateway.core.auth.manager import (
    AuthManager,
    _active_sessions,
    get_active_session_count,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _auth_result(**overrides: Any) -> AuthResult:
    defaults: dict[str, Any] = {
        "uid": 1,
        "session_id": "s1",
        "user_context": {"lang": "en_US"},
        "is_admin": False,
        "groups": [],
        "username": "admin",
        "database": "testdb",
    }
    defaults.update(overrides)
    return AuthResult(**defaults)


def _make_manager(
    jsonrpc_auth: AuthResult | Exception | None = None,
    xmlrpc_auth: AuthResult | Exception | None = None,
    execute_kw_result: Any = None,
    session_info: dict[str, Any] | Exception | None = None,
    session_timeout_seconds: int = 1800,
    max_concurrent_sessions: int = 100,
) -> AuthManager:
    """Build an AuthManager with mocked clients."""
    json_client = AsyncMock(spec=JsonRpcClient)
    xml_client = AsyncMock(spec=XmlRpcClient)

    if isinstance(jsonrpc_auth, Exception):
        json_client.authenticate = AsyncMock(side_effect=jsonrpc_auth)
    else:
        json_client.authenticate = AsyncMock(return_value=jsonrpc_auth)

    if isinstance(xmlrpc_auth, Exception):
        xml_client.authenticate = AsyncMock(side_effect=xmlrpc_auth)
    else:
        xml_client.authenticate = AsyncMock(return_value=xmlrpc_auth)

    # execute_kw is used for group fetching, get_external_id, and
    # has_group checks. v0.2.2-final routes the group-membership read
    # through ``res.users.read`` (groups_id / group_ids depending on
    # Odoo version) — we synthesize that response from
    # ``execute_kw_result`` (the legacy "list of group records") so
    # existing tests don't need a full RPC scenario.
    if isinstance(execute_kw_result, Exception):
        json_client.execute_kw = AsyncMock(side_effect=execute_kw_result)
        xml_client.execute_kw = AsyncMock(side_effect=execute_kw_result)
    else:
        legacy_groups = execute_kw_result or []
        # Synthesize group_ids from the records if the tests passed a
        # full-records list. Otherwise default to a single placeholder
        # group id so the second-step read of res.groups uses ITS keys.
        legacy_group_ids = (
            list(range(1, len(legacy_groups) + 1)) if legacy_groups else []
        )

        async def _route_execute_kw(
            model: str, method: str, args: list, kwargs: dict | None = None
        ) -> Any:
            # res.users.read for the membership field. The probe now
            # tries (all_group_ids → groups_id → group_ids); the legacy
            # simple-form helper returns the requested set on the v17/
            # v18 canonical name and signals "invalid field" for
            # ``all_group_ids`` (a v19-only field) so the production
            # code falls back as it would against a real v17/v18
            # server.
            if model == "res.users" and method == "read":
                requested_fields = args[1] if len(args) >= 2 else []
                if "all_group_ids" in requested_fields:
                    raise RuntimeError("Invalid field res.users.all_group_ids")
                if "groups_id" in requested_fields:
                    return [{"id": args[0][0], "groups_id": legacy_group_ids}]
                if "group_ids" in requested_fields:
                    return [{"id": args[0][0], "group_ids": legacy_group_ids}]
                return [{"id": args[0][0]}]
            # res.groups.read returning the display names supplied by
            # the test.
            if model == "res.groups" and method == "read":
                return legacy_groups
            # has_group calls: tests should override via _call_has_group
            # mocking. Default to False so admin status stays consistent.
            if model == "res.users" and method == "has_group":
                return False
            # res.groups.get_external_id — empty mapping by default so
            # tests using the simple-form helper don't accidentally
            # inject XML IDs that they didn't intend.
            if model == "res.groups" and method == "get_external_id":
                return {gid: "" for gid in args[0]}
            return legacy_groups

        json_client.execute_kw = AsyncMock(side_effect=_route_execute_kw)
        xml_client.execute_kw = AsyncMock(side_effect=_route_execute_kw)

    # For session strategy
    if isinstance(session_info, Exception):
        json_client._rpc = AsyncMock(side_effect=session_info)
    elif session_info is not None:
        json_client._rpc = AsyncMock(return_value=session_info)
    else:
        json_client._rpc = AsyncMock(return_value={"uid": 0})

    return AuthManager(
        jsonrpc_client=json_client,
        xmlrpc_client=xml_client,
        session_timeout_seconds=session_timeout_seconds,
        max_concurrent_sessions=max_concurrent_sessions,
    )


# ------------------------------------------------------------------
# API Key strategy
# ------------------------------------------------------------------


class TestApiKeyStrategy:
    async def test_success(self) -> None:
        result = _auth_result(uid=10)
        mgr = _make_manager(xmlrpc_auth=result)

        auth = await mgr.login("api_key", "admin", "my-api-key", "testdb")

        assert auth.uid == 10
        assert mgr.get_active_client() is mgr._xmlrpc

    async def test_invalid_key_raises(self) -> None:
        mgr = _make_manager(
            xmlrpc_auth=OdooAuthError("bad key"),
        )

        with pytest.raises(OdooAuthError, match="bad key"):
            await mgr.login("api_key", "admin", "wrong", "testdb")

    async def test_groups_fetched(self) -> None:
        result = _auth_result(uid=5)
        groups = [
            {"full_name": "base.group_user"},
            {"full_name": "sales.group_sale_manager"},
        ]
        mgr = _make_manager(
            xmlrpc_auth=result,
            execute_kw_result=groups,
        )

        auth = await mgr.login("api_key", "admin", "key", "testdb")

        assert "base.group_user" in auth.groups
        assert "sales.group_sale_manager" in auth.groups

    async def test_group_fetch_failure_does_not_break(
        self,
    ) -> None:
        result = _auth_result(uid=5)
        mgr = _make_manager(
            xmlrpc_auth=result,
            execute_kw_result=RuntimeError("network"),
        )

        auth = await mgr.login("api_key", "admin", "key", "testdb")
        # Should succeed despite group fetch failure
        assert auth.uid == 5
        assert auth.groups == []


# ------------------------------------------------------------------
# Password strategy
# ------------------------------------------------------------------


class TestPasswordStrategy:
    async def test_success(self) -> None:
        result = _auth_result(uid=2, session_id="sess-abc")
        mgr = _make_manager(jsonrpc_auth=result)

        auth = await mgr.login("password", "admin", "secret", "testdb")

        assert auth.uid == 2
        assert auth.session_id == "sess-abc"
        assert mgr.get_active_client() is mgr._jsonrpc

    async def test_invalid_password_raises(self) -> None:
        mgr = _make_manager(
            jsonrpc_auth=OdooAuthError("invalid credentials"),
        )

        with pytest.raises(OdooAuthError, match="invalid credentials"):
            await mgr.login("password", "admin", "wrong", "testdb")

    async def test_groups_fetched(self) -> None:
        result = _auth_result(uid=7)
        groups = [{"full_name": "base.group_system"}]
        mgr = _make_manager(
            jsonrpc_auth=result,
            execute_kw_result=groups,
        )

        auth = await mgr.login("password", "admin", "pass", "testdb")

        assert "base.group_system" in auth.groups

    async def test_auth_result_stored(self) -> None:
        result = _auth_result(uid=3)
        mgr = _make_manager(jsonrpc_auth=result)

        await mgr.login("password", "admin", "pass", "testdb")

        assert mgr.auth_result is not None
        assert mgr.auth_result.uid == 3


# ------------------------------------------------------------------
# Session strategy
# ------------------------------------------------------------------


class TestSessionStrategy:
    async def test_success(self) -> None:
        session_info: dict[str, Any] = {
            "uid": 42,
            "user_context": {"lang": "fr_FR"},
            "is_admin": True,
            "username": "admin",
        }
        mgr = _make_manager(session_info=session_info)
        # The login flow now verifies is_admin server-side via has_group.
        # Make has_group return True so the verified value is True.
        mgr._jsonrpc.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                True,  # _verify_admin_via_has_group → base.group_system
            ]
        )

        auth = await mgr.login("session", "", "session-token-xyz", "testdb")

        assert auth.uid == 42
        assert auth.user_context == {"lang": "fr_FR"}
        assert auth.is_admin is True
        assert mgr.get_active_client() is mgr._jsonrpc

    async def test_invalid_session_raises(self) -> None:
        mgr = _make_manager(session_info={"uid": 0})

        with pytest.raises(OdooAuthError, match="invalid or expired"):
            await mgr.login("session", "", "bad-token", "testdb")

    async def test_network_error_raises_auth_error(
        self,
    ) -> None:
        mgr = _make_manager(session_info=RuntimeError("connection reset"))

        with pytest.raises(OdooAuthError, match="validation failed"):
            await mgr.login("session", "", "token", "testdb")

    async def test_session_id_passed_to_client(self) -> None:
        session_info: dict[str, Any] = {
            "uid": 1,
            "user_context": {},
            "is_admin": False,
            "username": "u",
        }
        mgr = _make_manager(session_info=session_info)

        await mgr.login("session", "", "my-session-id", "testdb")

        assert mgr._jsonrpc._session_id.reveal() == "my-session-id"

    async def test_groups_fetched_after_session(self) -> None:
        session_info: dict[str, Any] = {
            "uid": 8,
            "user_context": {},
            "is_admin": False,
            "username": "u",
        }
        groups = [{"full_name": "base.group_portal"}]
        mgr = _make_manager(
            session_info=session_info,
            execute_kw_result=groups,
        )

        auth = await mgr.login("session", "", "tok", "testdb")

        assert "base.group_portal" in auth.groups


# ------------------------------------------------------------------
# Unknown strategy
# ------------------------------------------------------------------


class TestUnknownStrategy:
    async def test_raises(self) -> None:
        mgr = _make_manager()
        with pytest.raises(OdooAuthError, match="Unknown auth method"):
            await mgr.login("magic", "u", "p", "db")


# ------------------------------------------------------------------
# get_active_client
# ------------------------------------------------------------------


class TestGetActiveClient:
    def test_not_authenticated(self) -> None:
        mgr = _make_manager()
        with pytest.raises(OdooAuthError, match="Not authenticated"):
            mgr.get_active_client()

    async def test_returns_xmlrpc_after_api_key(self) -> None:
        result = _auth_result()
        mgr = _make_manager(xmlrpc_auth=result)
        await mgr.login("api_key", "u", "k", "db")
        assert mgr.get_active_client() is mgr._xmlrpc

    async def test_returns_jsonrpc_after_password(
        self,
    ) -> None:
        result = _auth_result()
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")
        assert mgr.get_active_client() is mgr._jsonrpc


# ------------------------------------------------------------------
# auth_result property
# ------------------------------------------------------------------


class TestAuthResultProperty:
    def test_none_before_login(self) -> None:
        mgr = _make_manager()
        assert mgr.auth_result is None

    async def test_set_after_login(self) -> None:
        result = _auth_result(uid=99)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")
        assert mgr.auth_result is not None
        assert mgr.auth_result.uid == 99


# ------------------------------------------------------------------
# close()
# ------------------------------------------------------------------


class TestAuthManagerClose:
    async def test_close_closes_both_clients(self) -> None:
        """close() should call close on both JSON-RPC and XML-RPC clients."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")

        await mgr.close()

        mgr._jsonrpc.close.assert_called_once()
        mgr._xmlrpc.close.assert_called_once()

    async def test_close_resets_active_client(self) -> None:
        """close() should reset _active_client to None."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")

        assert mgr._active_client is not None
        await mgr.close()
        assert mgr._active_client is None

    async def test_close_resets_auth_result(self) -> None:
        """close() should reset _auth_result to None."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")

        assert mgr.auth_result is not None
        await mgr.close()
        assert mgr.auth_result is None

    async def test_close_handles_jsonrpc_close_error(self) -> None:
        """close() should not raise if JSON-RPC client close fails."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")
        mgr._jsonrpc.close = AsyncMock(side_effect=RuntimeError("close fail"))

        await mgr.close()  # should not raise

        # XML-RPC close should still be called
        mgr._xmlrpc.close.assert_called_once()
        # State should still be cleaned up
        assert mgr._active_client is None
        assert mgr.auth_result is None

    async def test_close_handles_xmlrpc_close_error(self) -> None:
        """close() should not raise if XML-RPC client close fails."""
        result = _auth_result(uid=1)
        mgr = _make_manager(xmlrpc_auth=result)
        await mgr.login("api_key", "u", "k", "db")
        mgr._xmlrpc.close = AsyncMock(side_effect=RuntimeError("close fail"))

        await mgr.close()  # should not raise

        # JSON-RPC close should still have been attempted
        mgr._jsonrpc.close.assert_called_once()
        assert mgr._active_client is None

    async def test_close_handles_both_clients_failing(self) -> None:
        """close() should not raise even if both clients fail to close."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")
        mgr._jsonrpc.close = AsyncMock(side_effect=RuntimeError("fail 1"))
        mgr._xmlrpc.close = AsyncMock(side_effect=RuntimeError("fail 2"))

        await mgr.close()  # should not raise

        assert mgr._active_client is None
        assert mgr.auth_result is None

    async def test_close_idempotent(self) -> None:
        """close() should be safe to call multiple times."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result)
        await mgr.login("password", "u", "p", "db")

        await mgr.close()
        await mgr.close()  # second call should not raise

        assert mgr._active_client is None
        assert mgr.auth_result is None

    async def test_close_before_login(self) -> None:
        """close() should work even if no login was done."""
        mgr = _make_manager()

        await mgr.close()

        assert mgr._active_client is None
        assert mgr.auth_result is None


# ------------------------------------------------------------------
# Session timeout enforcement
# ------------------------------------------------------------------


class TestSessionTimeout:
    async def test_fresh_session_not_expired(self) -> None:
        """A freshly logged-in session should not be expired."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result, session_timeout_seconds=1800)
        await mgr.login("password", "u", "p", "db")

        # Should not raise
        client = mgr.get_active_client()
        assert client is not None

    async def test_expired_session_raises(self) -> None:
        """An expired session should raise OdooAuthError."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result, session_timeout_seconds=1)
        await mgr.login("password", "u", "p", "db")

        # Simulate time passing by setting last_activity in the past
        mgr._last_activity_time = time.monotonic() - 10

        with pytest.raises(OdooAuthError, match="expired"):
            mgr.get_active_client()

    async def test_activity_refreshes_timeout(self) -> None:
        """Each get_active_client call should refresh the timeout."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result, session_timeout_seconds=5)
        await mgr.login("password", "u", "p", "db")

        before = mgr.last_activity_time
        # Small delay to ensure monotonic time advances
        mgr.get_active_client()
        after = mgr.last_activity_time

        assert after >= before

    async def test_expired_session_invalidates_state(self) -> None:
        """After expiry, auth_result and active_client should be None."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result, session_timeout_seconds=1)
        await mgr.login("password", "u", "p", "db")
        mgr._last_activity_time = time.monotonic() - 10

        with pytest.raises(OdooAuthError):
            mgr.get_active_client()

        assert mgr.auth_result is None
        assert mgr._active_client is None

    def test_last_activity_time_zero_before_login(self) -> None:
        """Before login, last_activity_time should be 0."""
        mgr = _make_manager()
        assert mgr.last_activity_time == 0.0


# ------------------------------------------------------------------
# Max concurrent sessions
# ------------------------------------------------------------------


class TestMaxConcurrentSessions:
    def setup_method(self) -> None:
        """Clear global session registry before each test."""
        _active_sessions.clear()

    def teardown_method(self) -> None:
        """Clear global session registry after each test."""
        _active_sessions.clear()

    async def test_register_session(self) -> None:
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result, max_concurrent_sessions=5)
        await mgr.login("password", "u", "p", "db")
        mgr.register_session("session_1")
        assert get_active_session_count() == 1

    async def test_exceed_max_sessions_raises(self) -> None:
        managers = []
        for i in range(3):
            result = _auth_result(uid=i + 1)
            mgr = _make_manager(jsonrpc_auth=result, max_concurrent_sessions=3)
            await mgr.login("password", "u", "p", "db")
            mgr.register_session(f"session_{i}")
            managers.append(mgr)

        assert get_active_session_count() == 3

        # The 4th should fail
        result = _auth_result(uid=99)
        mgr = _make_manager(jsonrpc_auth=result, max_concurrent_sessions=3)
        await mgr.login("password", "u", "p", "db")
        with pytest.raises(OdooAuthError, match="Maximum concurrent sessions"):
            mgr.register_session("session_overflow")

    async def test_relogin_same_key_allowed(self) -> None:
        """Re-registering the same session key should not count as new."""
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result, max_concurrent_sessions=1)
        await mgr.login("password", "u", "p", "db")
        mgr.register_session("session_1")
        # Re-register same key (e.g., user re-logs in)
        mgr.register_session("session_1")
        assert get_active_session_count() == 1

    async def test_close_removes_from_registry(self) -> None:
        result = _auth_result(uid=1)
        mgr = _make_manager(jsonrpc_auth=result, max_concurrent_sessions=5)
        await mgr.login("password", "u", "p", "db")
        mgr.register_session("session_close_test")
        assert get_active_session_count() == 1

        await mgr.close()
        assert get_active_session_count() == 0


# ------------------------------------------------------------------
# Admin detection via XML IDs
# ------------------------------------------------------------------


class TestAdminDetectionViaXmlId:
    async def test_admin_detected_via_has_group(self) -> None:
        """has_group('base.group_system') returning True should set is_admin."""
        result = _auth_result(uid=1, is_admin=False)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        # Call sequence:
        # 1. groups search_read
        # 2. _detect_admin_via_xmlid → has_group base.group_system
        # 3. _verify_admin_via_has_group → has_group base.group_system (final override)
        json_client.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                True,  # has_group base.group_system (xmlid detect)
                True,  # has_group base.group_system (verify override)
            ]
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "admin", "pass", "testdb")

        assert auth.is_admin is True

    async def test_erp_manager_detected_via_has_group(self) -> None:
        """ERP manager detection runs in _detect_admin_via_xmlid, but the
        final ``is_admin`` is overridden by ``_verify_admin_via_has_group``
        which only checks ``base.group_system``. So a pure ERP manager
        (without group_system) ends up with is_admin=False after override.
        """
        result = _auth_result(uid=1, is_admin=False)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        # groups search_read, has_group system=False, has_group erp_manager=True,
        # then verify has_group system=False (final override).
        json_client.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                False,  # has_group base.group_system (xmlid detect)
                True,  # has_group base.group_erp_manager (xmlid detect)
                False,  # has_group base.group_system (verify override)
            ]
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "admin", "pass", "testdb")

        # _verify_admin_via_has_group only checks base.group_system, which
        # is False — so the ERP manager flag does not survive the override.
        # This is the intended fail-closed behavior.
        assert auth.is_admin is False

    async def test_non_admin_stays_non_admin(self) -> None:
        """If has_group returns False for system, is_admin stays False."""
        result = _auth_result(uid=1, is_admin=False)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        json_client.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                False,  # has_group base.group_system (xmlid detect)
                False,  # has_group base.group_erp_manager (xmlid detect)
                False,  # has_group base.group_system (verify override)
            ]
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "testdb")

        assert auth.is_admin is False

    async def test_tampered_is_admin_overridden(self) -> None:
        """A tampered auth response with is_admin=True must NOT survive
        the server-side verification step. Even if the auth payload claims
        admin, has_group is the source of truth."""
        result = _auth_result(uid=1, is_admin=True)  # Tampered/forged
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)
        # _detect_admin_via_xmlid is short-circuited because is_admin is
        # already True. _verify_admin_via_has_group still runs.
        json_client.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                False,  # has_group base.group_system (verify override)
            ]
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "admin", "pass", "testdb")

        # The forged is_admin=True is overridden by the verified False.
        assert auth.is_admin is False

    async def test_has_group_failure_does_not_break(self) -> None:
        """If has_group raises, admin detection gracefully degrades."""
        result = _auth_result(uid=1, is_admin=False)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        json_client.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                RuntimeError("network"),  # has_group base.group_system (xmlid)
                RuntimeError("network"),  # has_group base.group_erp_manager (xmlid)
                RuntimeError("network"),  # has_group base.group_system (verify)
            ]
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "testdb")

        # Should not raise, admin stays False (fail-closed).
        assert auth.is_admin is False

    async def test_odoo17_fallback_when_recordset_form_fails(self) -> None:
        """Regression test for v0.2.2 cross-version compat.

        Odoo 17's ``has_group`` does NOT accept a recordset arg — it raises
        "Users.has_group() takes 2 positional arguments but 3 were given".
        The gateway must fall back to the Odoo 17 form ``[xmlid]`` (no uid).

        This test simulates the Odoo 17 error on the first call and asserts
        a second call is made with the fallback form, and that the result
        is correctly recognized.
        """
        result = _auth_result(uid=2, is_admin=False)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        # Odoo 17 raises TypeError-like error on the recordset form,
        # then returns True for the no-recordset form.
        recordset_error = RuntimeError(
            "Users.has_group() takes 2 positional arguments but 3 were given"
        )
        json_client.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                recordset_error,  # xmlid detect — try recordset form (Odoo 18)
                True,  # xmlid detect — fallback no-recordset form (Odoo 17) succeeds
                recordset_error,  # verify — try recordset form
                True,  # verify — fallback succeeds
            ]
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "admin", "pass", "testdb")

        # Admin status should be correctly detected via fallback
        assert auth.is_admin is True

        # Verify the fallback call was made with just [xmlid]
        # The last execute_kw is the verify call's fallback
        last_call = json_client.execute_kw.call_args_list[-1].args
        assert last_call[0] == "res.users"
        assert last_call[1] == "has_group"
        # Fallback form: just [xmlid], no uid recordset
        assert last_call[2] == ["base.group_system"]

    async def test_both_call_forms_fail_demotes_admin(self) -> None:
        """Fail-closed: if both Odoo 17 and 18 call forms fail, admin demoted."""
        result = _auth_result(uid=2, is_admin=False)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        json_client.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                # xmlid detect: both forms fail for base.group_system
                RuntimeError("network"),  # recordset form
                RuntimeError("network"),  # no-recordset form
                # xmlid detect: both forms fail for base.group_erp_manager
                RuntimeError("network"),  # recordset form
                RuntimeError("network"),  # no-recordset form
                # verify: both forms fail
                RuntimeError("network"),  # recordset form
                RuntimeError("network"),  # no-recordset form
            ]
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "testdb")

        # Should not raise. Admin stays False (fail-closed).
        assert auth.is_admin is False

    async def test_verify_admin_via_has_group_passes_uid_recordset(self) -> None:
        """Regression test for v0.2.1 → v0.2.2 admin-demotion bug.

        ``has_group`` is an Odoo recordset method — it MUST be called with
        the user's UID as a recordset (``[[uid], group_xml_id]``), not just
        the group XML id. On Odoo 18+ the no-recordset form returns False
        unconditionally, demoting real admins to non-admin and (combined
        with ``default_policy=deny``) blocking every model access.

        This test verifies the call payload includes the uid recordset.
        """
        result = _auth_result(uid=42, is_admin=False)
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)
        json_client.execute_kw = AsyncMock(
            side_effect=[
                [],  # groups search_read
                True,  # _detect_admin_via_xmlid → has_group system
                True,  # _verify_admin_via_has_group → has_group system
            ]
        )

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        await mgr.login("password", "admin", "pass", "testdb")

        # Inspect the FINAL has_group call (the verify-override one).
        # It MUST pass [[42], "base.group_system"] — NOT just
        # ["base.group_system"].
        final_call_args = json_client.execute_kw.call_args_list[-1].args
        # args = (model, method, args_list, kwargs_dict?)
        assert final_call_args[0] == "res.users"
        assert final_call_args[1] == "has_group"
        # The third positional must be [[uid], group_xml_id]
        positional_args = final_call_args[2]
        assert positional_args[0] == [42], (
            f"has_group must receive uid recordset [[42]] as first arg, "
            f"got {positional_args[0]!r}. This was the v0.2.1 bug that "
            f"demoted real admins on Odoo 18 to non-admin."
        )
        assert positional_args[1] == "base.group_system"


class TestGroupXmlIds:
    """v0.2.2 S2: ``_fetch_groups`` populates both display names AND XML IDs.

    Previously RBAC matched ``user_groups`` (display names) against
    rbac.yaml's technical IDs (``base.group_system``) — never aligned,
    over-blocking non-admins. After the fix, ``result.groups`` contains
    the UNION so either form matches.
    """

    async def test_groups_includes_both_display_and_xml_ids(self) -> None:
        result = _auth_result(uid=5, groups=[])
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        # v0.2.2-final flow:
        #   1. res.users.read([uid], ['groups_id']) → returns group_ids
        #      (or 'group_ids' on Odoo 19+ via Invalid-field retry).
        #   2. res.groups.read([gids], ['full_name','name']) → display names
        #   3. res.groups.get_external_id([gids]) → {gid: 'module.name'}
        #   4. has_group probes for admin detection + verification
        async def _route(model: str, method: str, args: list, kw=None) -> Any:
            if model == "res.users" and method == "read":
                requested = args[1] if len(args) >= 2 else []
                if "all_group_ids" in requested:
                    raise RuntimeError("Invalid field res.users.all_group_ids")
                if "groups_id" in requested:
                    return [{"id": 5, "groups_id": [1, 4]}]
                return [{"id": 5}]
            if model == "res.groups" and method == "read":
                return [
                    {"id": 1, "full_name": "User types / Internal User"},
                    {"id": 4, "full_name": "Sales / User: All Documents"},
                ]
            if model == "res.groups" and method == "get_external_id":
                return {1: "base.group_user", 4: "sales_team.group_sale_salesman"}
            if model == "res.users" and method == "has_group":
                return False
            return []

        json_client.execute_kw = AsyncMock(side_effect=_route)

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "testdb")

        # Both forms must be in result.groups so rbac.yaml configs using
        # EITHER display names or XML IDs work.
        assert "User types / Internal User" in auth.groups
        assert "Sales / User: All Documents" in auth.groups
        assert "base.group_user" in auth.groups
        assert "sales_team.group_sale_salesman" in auth.groups

        # group_xml_ids contains ONLY the technical form
        assert "base.group_user" in auth.group_xml_ids
        assert "sales_team.group_sale_salesman" in auth.group_xml_ids
        assert "User types / Internal User" not in auth.group_xml_ids

    async def test_xml_id_fetch_failure_does_not_break_login(self) -> None:
        """If get_external_id AND ir.model.data both fail, display names still work."""
        result = _auth_result(uid=5, groups=[])
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        async def _route(model: str, method: str, args: list, kw=None) -> Any:
            if model == "res.users" and method == "read":
                requested = args[1] if len(args) >= 2 else []
                if "all_group_ids" in requested:
                    raise RuntimeError("Invalid field res.users.all_group_ids")
                if "groups_id" in requested:
                    return [{"id": 5, "groups_id": [1]}]
                return [{"id": 5}]
            if model == "res.groups" and method == "read":
                return [{"id": 1, "full_name": "Internal User"}]
            if model == "res.groups" and method == "get_external_id":
                raise RuntimeError("method not available")
            if model == "ir.model.data" and method == "search_read":
                raise RuntimeError("no model access")
            if model == "res.users" and method == "has_group":
                return False
            return []

        json_client.execute_kw = AsyncMock(side_effect=_route)

        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "testdb")

        # Display names still present
        assert "Internal User" in auth.groups
        # XML IDs is empty but that's OK — login still succeeded
        assert auth.group_xml_ids == []

    async def test_odoo_19_field_rename_falls_back(self) -> None:
        """Odoo 19+: ``groups_id`` was renamed to ``group_ids`` — must fall back."""
        result = _auth_result(uid=5, groups=[])
        json_client = AsyncMock(spec=JsonRpcClient)
        xml_client = AsyncMock(spec=XmlRpcClient)
        json_client.authenticate = AsyncMock(return_value=result)

        async def _route(model: str, method: str, args: list, kw=None) -> Any:
            if model == "res.users" and method == "read":
                fields = args[1] if len(args) >= 2 else []
                # v19 prefers all_group_ids (with implications)
                if "all_group_ids" in fields:
                    return [{"id": 5, "all_group_ids": [1]}]
                if "groups_id" in fields:
                    # v19 rejects this field name.
                    raise RuntimeError("Invalid field res.users.groups_id")
                if "group_ids" in fields:
                    return [{"id": 5, "group_ids": [1]}]
            if model == "res.groups" and method == "read":
                return [{"id": 1, "full_name": "Internal User"}]
            if model == "res.groups" and method == "get_external_id":
                return {1: "base.group_user"}
            if model == "res.users" and method == "has_group":
                return False
            return []

        json_client.execute_kw = AsyncMock(side_effect=_route)
        mgr = AuthManager(jsonrpc_client=json_client, xmlrpc_client=xml_client)
        auth = await mgr.login("password", "user", "pass", "testdb")

        # The fallback path picked up the v19 field name and still
        # resolved the user's groups.
        assert "base.group_user" in auth.group_xml_ids
        assert "Internal User" in auth.groups
