# Plugin Authoring Guide — Plugin SDK 1.0

This guide is for authors writing plugins that extend the
`odoo-mcp-gateway` with domain-specific tools, resources, and
prompts. The SDK landed in v0.3.0 as ADR-003.

## Quick start

Minimum viable plugin in 30 lines:

```python
# my_plugin/plugin.py
from __future__ import annotations
from typing import Any
from mcp.server.fastmcp import FastMCP
from odoo_mcp_gateway.plugins.base import OdooPlugin
from odoo_mcp_gateway.plugins.sdk import PluginContext


class MyPlugin(OdooPlugin):
    """A tiny example plugin."""

    plugin_sdk_version = ">=1.0,<2.0"

    @property
    def name(self) -> str:
        return "my_plugin"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["base"]

    def register(self, server: FastMCP, context: PluginContext) -> None:
        @server.tool()
        async def my_tool() -> dict[str, Any]:
            return {"hello": "world"}
```

Publish via `entry_points` in your `pyproject.toml`:

```toml
[project.entry-points."odoo_mcp_gateway.plugins"]
my_plugin = "my_plugin.plugin:MyPlugin"
```

`pip install my-plugin` next to `odoo-mcp-gateway` and the gateway
discovers it automatically at startup.

## SDK version compatibility

Declare which SDK majors your plugin supports:

```python
class MyPlugin(OdooPlugin):
    plugin_sdk_version = ">=1.0,<2.0"
    ...
```

Compatibility policy:

| Plugin range | Gateway SDK | Behaviour |
|---|---|---|
| `>=1.0,<2.0` | `1.0.0` | ✅ Load |
| `>=1.0,<2.0` | `1.3.0` | ✅ Load |
| `>=1.5,<2.0` | `1.0.0` | ⚠️ Load + warn (minor mismatch) |
| `>=2.0,<3.0` | `1.0.0` | ❌ Refuse (major mismatch) |
| *missing*     | any        | ⚠️ Load + deprecation warning |

The registry runs the check at startup and logs the result. CI tests
should exercise the compat check (see
`tests/unit/plugins/test_plugin_sdk.py`).

## The 7 lifecycle hooks

Hooks are async (except `register`). All have no-op defaults on
`OdooPlugin`, so override only what you need:

| Hook | When | Use for |
|---|---|---|
| `register` | once at server startup | register tools / resources / prompts |
| `pre_register` | after Odoo login, before `register` | adaptive registration (query Odoo, then decide what to register) |
| `post_register` | after all plugins called `register` | cross-plugin discovery |
| `pre_call` | before every tool invocation | policy enforcement; raising aborts the call |
| `post_call` | after every tool returns | response transformation; the returned value REPLACES the tool's result |
| `on_session_close` | when a session is evicted | per-user cleanup |
| `on_external_event` | RESERVED for v0.4.0 webhooks | implement now to be future-ready |

### Example: a `pre_call` policy hook

```python
from odoo_mcp_gateway.plugins.sdk import PluginContext

class QuotaPlugin(OdooPlugin):
    plugin_sdk_version = ">=1.0,<2.0"

    @property
    def name(self) -> str:
        return "quota"

    def register(self, server, context):
        pass  # no new tools; we only gate existing ones

    async def pre_call(self, tool, arguments, context):
        if tool in ("bulk_create", "bulk_update"):
            current = self._quota_used()
            if current > 1000:
                raise PermissionError(
                    "Daily bulk-operation quota exhausted"
                )
```

### Example: a `post_call` transformer

```python
class TimestampPlugin(OdooPlugin):
    plugin_sdk_version = ">=1.0,<2.0"

    @property
    def name(self) -> str:
        return "timestamp"

    def register(self, server, context):
        pass

    async def post_call(self, tool, result, context):
        if isinstance(result, dict):
            result.setdefault(
                "_handled_at",
                datetime.utcnow().isoformat() + "Z",
            )
        return result
```

## The `PluginContext` object

Every async hook receives a `PluginContext`. It exposes the
gateway-side primitives plugin code should use:

| Attribute | What |
|---|---|
| `gateway` | the full `GatewayContext` (full backdoor; new plugins should prefer the focused helpers below) |
| `rbac` | the `RBACManager` |
| `restrictions` | the `RestrictionChecker` |
| `audit_logger` | the `AuditLogger` |
| `error_sanitizer` | the `ErrorSanitizer` |
| `field_inspector` | cached field-schema reader |
| `model_registry` | model-allowlist resolver |
| `version_adapter` | active `V17Adapter` / `V18Adapter` / `V19Adapter`, or `None` |
| `plugin_sdk_version` | the gateway's SDK version (for branching on multi-major compat) |
| `plugin_name` | your plugin's name |

Read fields through `field_inspector` (it caches; raw `execute_kw`
bypasses the cache):

```python
async def my_tool(model: str):
    ctx: PluginContext = ...
    client = get_client(ctx.gateway)
    fields = await ctx.field_inspector.get_fields(client, model)
    ...
```

## Required attributes

Your plugin class MUST set:

| Attribute | Type | Purpose |
|---|---|---|
| `name` | property → str | unique plugin id |
| `register` | method | the synchronous entry point |
| `plugin_sdk_version` | class str | SDK compat range |
| `required_odoo_modules` | property → list[str] | Odoo modules the registry verifies are installed |
| `required_models` | property → list[str] | Odoo models the plugin queries |

`version` and `description` are optional (defaults: "0.1.0" / "").

## Security expectations

The gateway's two-layer security model (YAML restrictions + Odoo
ACLs) applies to plugin tools too. Plugin tools MUST call:

1. `security_gate(gateway, tool_name, session_key)` at the top.
2. `gateway.restrictions.check_model_access(model, op, is_admin)` before any read/write.
3. `gateway.restrictions.check_field_write(model, field, is_admin)` for every field in a write payload.
4. `gateway.rbac.sanitize_write_values(values, model, groups, is_admin)` before passing values to Odoo.

Bypass any of those and the plugin authors should expect rejection
in code review. See `plugins/core/hr.py` for a worked example.

## Testing

Plugins should ship unit tests that:

* Mock the AuthManager at `execute_kw` level (the gateway's own test
  fixture pattern; see `tests/unit/plugins/test_hr_plugin.py`).
* Verify the plugin's tools register on a real `FastMCP` instance.
* Exercise the `plugin_sdk_version` field via
  `check_plugin_sdk_compat(name, spec)`.

## Custom-module compatibility (v0.3.3 follow-up MED-3)

When the stock Odoo module name a plugin declares doesn't match what's
installed (e.g. the OCA `odoo_website_helpdesk` module ships
`ticket.helpdesk` instead of stock `helpdesk.ticket`), operators can
opt in to compatibility via `model_access.yaml`:

```yaml
plugin_overrides:
  helpdesk:
    accept_modules: ["helpdesk", "odoo_website_helpdesk"]
    accept_models:  ["helpdesk.ticket", "ticket.helpdesk"]
```

OR semantics — the plugin is satisfied if **any** of (`required_*` ∪
`accept_*`) is present. The first entry of `accept_models` wins as the
effective model name; the registry exposes it as
`PluginInfo.effective_model_name`.

### Authoring pattern for plugin tools

Plugins that may target multiple model names should:

1. Declare a class-level default (e.g. `ticket_model = "helpdesk.ticket"`).
2. Resolve the runtime name inside each tool via the registry:

```python
def _resolve_my_model() -> str:
    registry = getattr(context, "plugin_registry", None)
    if registry is None:
        return self.my_model_default
    info = registry.get_plugin(self.name)
    if info is None:
        return self.my_model_default
    return getattr(info, "effective_model_name", None) or self.my_model_default
```

3. Pass the resolved value to every `execute_kw`, `check_model_access`,
   `check_field_write`, and RBAC call in the tool body.

The reference implementation is `plugins/core/helpdesk.py`. Other
built-in plugins (`hr.py`, `project.py`, `sales.py`) still hard-code
their model names — migration is opt-in and can land in v0.4.0
without behaviour change for stock-module deployments.

## Reference

* `src/odoo_mcp_gateway/plugins/sdk.py` — the Protocol + compat check.
* `src/odoo_mcp_gateway/plugins/base.py` — convenience base class with no-op hooks.
* `src/odoo_mcp_gateway/plugins/core/hr.py` — full reference implementation.
* `src/odoo_mcp_gateway/plugins/core/helpdesk.py` — `plugin_overrides`-aware reference.
