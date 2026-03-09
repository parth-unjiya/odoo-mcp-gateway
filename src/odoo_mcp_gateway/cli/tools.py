"""CLI utility tools: test-connection, validate-config, list-models."""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from odoo_mcp_gateway.core.security.config_loader import (
    load_config,
)
from odoo_mcp_gateway.core.security.restrictions import (
    RestrictionChecker,
)


def _test_connection(args: argparse.Namespace) -> int:
    """Test connectivity to an Odoo instance."""

    url = args.url.rstrip("/")
    endpoint = f"{url}/web/session/get_session_info"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "call",
        "params": {},
    }

    print(f"Testing connection to {url} ...")
    start = time.monotonic()
    try:
        resp = httpx.post(endpoint, json=payload, timeout=args.timeout)
        elapsed = time.monotonic() - start
        if resp.status_code == 200:
            print(f"OK — Odoo responded in {elapsed:.2f}s (status {resp.status_code})")
            return 0
        print(f"WARN — Odoo returned status {resp.status_code} in {elapsed:.2f}s")
        return 1
    except httpx.ConnectError as exc:
        elapsed = time.monotonic() - start
        print(f"FAIL — Connection refused after {elapsed:.2f}s: {exc}")
        return 1
    except httpx.TimeoutException:
        print(f"FAIL — Timed out after {args.timeout}s")
        return 1
    except Exception as exc:
        print(f"FAIL — {exc}")
        return 1


def _validate_config(args: argparse.Namespace) -> int:
    """Validate YAML configuration files."""
    config_dir = args.config_dir
    print(f"Validating configs in '{config_dir}' ...")

    try:
        cfg = load_config(config_dir)
    except Exception as exc:
        print(f"INVALID — {exc}")
        return 1

    r = cfg.restrictions
    m = cfg.model_access
    print("OK — Configuration is valid")
    print(
        f"  Restrictions: {len(r.always_blocked)} blocked, "
        f"{len(r.admin_only)} admin-only, "
        f"{len(r.blocked_methods)} blocked methods"
    )
    print(f"  Model access: default_policy={m.default_policy}")
    stock = m.stock_models or {}
    custom = m.custom_models or {}
    fc = len(stock.get("full_crud", []))
    fc += len(custom.get("full_crud", []))
    ro = len(stock.get("read_only", []))
    ro += len(custom.get("read_only", []))
    print(f"  Models: {fc} full_crud, {ro} read_only")
    return 0


def _list_models(args: argparse.Namespace) -> int:
    """List configured model access levels."""
    try:
        cfg = load_config(args.config_dir)
    except Exception as exc:
        print(f"Config error: {exc}")
        return 1

    checker = RestrictionChecker(
        config=cfg.restrictions,
        model_access=cfg.model_access,
    )

    admin_models = checker.get_accessible_models(is_admin=True)
    user_models = checker.get_accessible_models(is_admin=False)
    user_set = set(user_models)

    print(f"{'Model':<45} {'User Access':<15} {'Admin Access'}")
    print("-" * 75)
    for model in admin_models:
        user_flag = "yes" if model in user_set else "admin-only"
        print(f"{model:<45} {user_flag:<15} yes")

    print(f"\nTotal: {len(admin_models)} models ({len(user_models)} for regular users)")
    return 0


def main() -> None:
    """CLI entry point with subcommands."""
    parser = argparse.ArgumentParser(
        prog="odoo-mcp-tools",
        description="Odoo MCP Gateway utility tools",
    )
    sub = parser.add_subparsers(dest="command")

    # test-connection
    tc = sub.add_parser(
        "test-connection",
        help="Test connectivity to an Odoo instance",
    )
    tc.add_argument("--url", required=True, help="Odoo base URL")
    tc.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Request timeout in seconds (default: 10)",
    )

    # validate-config
    vc = sub.add_parser(
        "validate-config",
        help="Validate YAML configuration files",
    )
    vc.add_argument(
        "--config-dir",
        default="config",
        help="Config directory (default: config)",
    )

    # list-models
    lm = sub.add_parser(
        "list-models",
        help="List configured model access levels",
    )
    lm.add_argument(
        "--config-dir",
        default="config",
        help="Config directory (default: config)",
    )

    args = parser.parse_args()

    handlers = {
        "test-connection": _test_connection,
        "validate-config": _validate_config,
        "list-models": _list_models,
    }

    if args.command is None:
        parser.print_help()
        return

    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
