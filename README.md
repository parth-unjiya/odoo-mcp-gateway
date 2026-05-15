# odoo-mcp-gateway

Security-first, version-agnostic MCP gateway for Odoo 17/18/19. Works with stock and custom modules via YAML configuration. Zero Odoo-side code required.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Odoo](https://img.shields.io/badge/Odoo-17%20%7C%2018%20%7C%2019-714B67.svg)](https://www.odoo.com/)
[![Tests](https://img.shields.io/badge/tests-1476%20passing-brightgreen.svg)](#testing)
[![Coverage](https://img.shields.io/badge/coverage-93%25-brightgreen.svg)](#testing)

<!-- mcp-name: io.github.parth-unjiya/odoo-mcp-gateway -->

## 30-Second Quick Start

```bash
pip install odoo-mcp-gateway
```

Add to Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "odoo": {
      "command": "python",
      "args": ["-m", "odoo_mcp_gateway"],
      "env": {
        "ODOO_URL": "http://localhost:8069",
        "ODOO_DB": "your_database"
      }
    }
  }
}
```

Restart Claude Desktop. In any conversation:

```
login with method "password", username "admin", credential "your_password"
```

You're connected. Ask Claude to query, create, or update Odoo records — every call is rate-limited, audit-logged, and runs through two layers of security checks before reaching Odoo.

**No Odoo addon required. No Python code to write.** Just YAML config for fine-grained access control (optional — secure defaults work out of the box).

## What's New in v0.2.1

- **Brute-force protection** — per-username (5/5min) + per-source (30/15min) lockout
- **`dry_run` mode** on `create_record`, `update_record`, `delete_record`, `execute_method` — validate without executing
- **2 new tools**: `get_defaults` (preview Odoo defaults), `get_onchange` (preview field side effects)
- **Temporal grouping** in `read_group`: `create_date:month`, `date:quarter`, etc.
- **Hardened blocklists**: 32 always-blocked models (was 17), 29 always-blocked methods (was 18), 10 always-blocked write fields, 8 always read-only models
- **Server-side admin verification** via `has_group('base.group_system')` (was trusted from auth response)
- **Credential wrapper** prevents password leakage via `repr()`/traceback
- **JSON-RPC retry** only fires on `OdooSessionExpiredError` (was retrying on every auth error)

See [CHANGELOG.md](CHANGELOG.md) for the full list of 21 security fixes.

## Why This Exists

Existing Odoo MCP servers share common problems: hardcoded model lists that miss custom modules, security as an afterthought, mandatory custom Odoo addons, and single-version targets. This gateway solves all of them:

- **Two-layer security** — MCP restrictions (YAML) + Odoo's built-in ACLs (ir.model.access + ir.rule)
- **YAML-driven configuration** — model restrictions, RBAC, field-level access, rate limiting, audit logging
- **Custom module support** — auto-discovers models via `ir.model`, add YAML config and it works
- **Version-agnostic** — Odoo 17, 18, 19 with version-specific adapters
- **Zero Odoo-side code** — `pip install` + YAML config = done. No custom addon required
- **Full MCP primitives** — 31 Tools + 6 Resources + 12 Prompts (most servers only implement Tools)
- **Plugin architecture** — extend with pip-installable domain packs via entry_points

## Architecture

```
MCP Client (Claude Desktop / Claude Code / HTTP)
    |  User calls login tool with Odoo credentials
    v
MCP Server (FastMCP)
    |
    |-- security_gate()    --> Rate limit + RBAC tool access + audit logging
    |-- restrictions       --> Model/method/field block lists (YAML + hardcoded)
    |-- rbac               --> Field-level filtering + write sanitization
    |
    |-- tools/             --> 31 MCP tools (auth + schema + CRUD + workflow + plugins)
    |-- resources/         --> 6 MCP resources (odoo:// URIs)
    |-- prompts/           --> 12 reusable prompt templates
    |-- plugins/           --> Entry-point plugin system (HR, Sales, Project, Helpdesk)
    |
    |  JSON-RPC / XML-RPC as authenticated user
    v
Odoo 17/18/19 (security enforced per user via ir.model.access + ir.rule)
```

### Security Pipeline

Every tool and resource call passes through this pipeline:

```
Request --> Rate Limit --> Authentication Check --> RBAC Tool Access
    --> Model Restriction --> Method Restriction --> Field Validation
    --> Handler Execution --> RBAC Field Filtering --> Audit Log --> Response
```

Hardcoded safety guardrails that cannot be overridden by YAML:
- **32 always-blocked models** — system internals, auth/TOTP, payment tokens, attachments, mail.mail, base.automation, and more
- **8 always read-only models** — mail.message, mail.followers, mail.activity, discuss.channel, mail.notification, mail.compose.message, mail.alias, discuss.channel.member (reads OK, writes blocked for everyone)
- **10 always-blocked write fields** — password, password_crypt, groups_id, totp_secret, signup_token/type/expiration, api_key, share, active
- **29 always-blocked methods** — sudo, with_user/env/context, _sql, _write, _create, name_create, load, import_data, export_data, and more
- **28 ORM methods blocked in execute_method** (prevents bypassing field-level checks)
- **Per-username brute-force lockout** — 5 failures → 5 minute lockout (fixed duration, cannot be extended)
- **Per-source brute-force lockout** — 30 failures / 15 min, prevents username-rotation attacks
- **Credential wrapper class** — passwords stored with leak-safe `__repr__`/`__str__`, cleared on close
- **Server-side admin verification** — `has_group('base.group_system')` overrides auth-response `is_admin`

## Quick Start

```bash
pip install odoo-mcp-gateway

# Copy and edit config files
cp config/restrictions.yaml.example config/restrictions.yaml
cp config/model_access.yaml.example config/model_access.yaml
cp config/rbac.yaml.example config/rbac.yaml

# Set environment variables
export ODOO_URL=http://localhost:8069
export ODOO_DB=mydb

# Run (stdio mode for Claude Desktop / Claude Code)
python -m odoo_mcp_gateway

# Or HTTP mode for web clients
MCP_TRANSPORT=streamable-http python -m odoo_mcp_gateway
```

### Claude Desktop Configuration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "python",
      "args": ["-m", "odoo_mcp_gateway"],
      "env": {
        "ODOO_URL": "http://localhost:8069",
        "ODOO_DB": "mydb"
      }
    }
  }
}
```

### Claude Code Configuration

```bash
# Add as MCP server
claude mcp add odoo -- python -m odoo_mcp_gateway
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ODOO_URL` | `http://localhost:8069` | Odoo server URL |
| `ODOO_DB` | *(required)* | Odoo database name |
| `MCP_TRANSPORT` | `stdio` | Transport mode (`stdio` or `streamable-http`) |
| `MCP_HOST` | `127.0.0.1` | HTTP host (streamable-http mode) |
| `MCP_PORT` | `8080` | HTTP port (streamable-http mode) |
| `MCP_LOG_LEVEL` | `INFO` | Logging level |
| `CONFIG_DIR` | `.` | Directory for YAML config files |
| `SESSION_TIMEOUT_SECONDS` | `1800` | Session inactivity timeout |
| `MAX_CONCURRENT_SESSIONS` | `100` | Maximum concurrent sessions |
| `RATE_LIMIT_GLOBAL` | `60` | Requests per minute (global) |
| `RATE_LIMIT_WRITE` | `20` | Write operations per minute |

## Security

### Two-Layer Security Model

1. **MCP gateway restrictions** (YAML config + hardcoded guardrails) — blocks sensitive models, dangerous methods, privileged fields *before* any Odoo call is made
2. **Odoo's built-in ACLs** — enforces per-user access on actual records via `ir.model.access` and `ir.rule`

### Model Restriction Tiers

| Tier | Effect | Example |
|------|--------|---------|
| `always_blocked` | Nobody can access, including admins | `ir.config_parameter`, `res.users.apikeys` |
| `admin_only` | Only admin users | `ir.model`, `ir.model.fields` |
| `admin_write_only` | Read OK for all, write needs admin | `res.company`, `res.currency` |

### Hardcoded Safety Guardrails

These cannot be overridden by YAML configuration:

**Blocked models** (32): `ir.config_parameter`, `res.users`, `res.users.apikeys`, `res.users.log`, `ir.cron`, `ir.module.module`, `ir.model.access`, `ir.rule`, `ir.mail_server`, `ir.ui.view`, `ir.actions.server`, `ir.logging`, `ir.attachment`, `ir.exports`, `ir.exports.line`, `iap.account`, `auth.totp.wizard`, `auth.totp.device`, `payment.token`, `payment.provider`, `base.automation`, `digest.digest`, `res.config.settings`, `change.password.wizard`, `change.password.user`, `base.module.update`, `base.module.upgrade`, `base.module.uninstall`, `fetchmail.server`, `bus.bus`, `mail.mail`, `mail.template`

**Read-only models** (8): `mail.message`, `mail.followers`, `mail.activity`, `discuss.channel`, `mail.notification`, `mail.compose.message`, `mail.alias`, `discuss.channel.member` (reads allowed, writes blocked for everyone)

**Blocked write fields** (10): `password`, `password_crypt`, `groups_id`, `totp_secret`, `signup_token`, `signup_type`, `signup_expiration`, `api_key`, `share`, `active`

**Blocked methods** (29): `sudo`, `with_user`, `with_company`, `with_context`, `with_env`, `with_prefetch`, `_auto_init`, `_sql`, `_register_hook`, `_write`, `_create`, `_read`, `_setup_base`, `_setup_fields`, `_setup_complete`, `init`, `_table_query`, `_read_group_raw`, `name_create`, `load`, `import_data`, `export_data`, `flush_recordset`, `invalidate_recordset`, `_search_panel_select_range`, `_search_panel_select_multi_range`, `_search_panel_domain_image`, `_search`, `_read_progress_bar`

### Additional Security Features

- **Brute-force protection** — per-username lockout (5 fails → 5 min) AND per-source IP/connection lockout (30 fails → 15 min, blocks username-rotation attacks). Lockouts have fixed duration — cannot be extended by additional attempts (DoS-resistant).
- **Credential wrapper** — passwords/session IDs stored in a `Credential` class with leak-safe `__repr__`/`__str__`, explicit `.reveal()` for use, and `.clear()` on close
- **Server-side admin verification** — `is_admin` is re-verified via `has_group('base.group_system')` after authentication, defending against tampered auth responses
- **Private method guard** — underscore-prefixed methods (`_compute_*`, `_inverse_*`, etc.) blocked for everyone including admin unless explicitly whitelisted
- **Rate limiting** — per-session token bucket with separate global and write budgets
- **RBAC** — tool-level access control by user group, field-level response filtering, transparent drop reporting via `return_dropped=True`
- **Input validation** — model names, method names, field names, domain filters, ORDER BY clauses, groupby with temporal operators, write values (size/depth/type)
- **IDOR protection** — plugin tools scope data access to the authenticated user
- **Audit logging** — structured JSON logs for all allowed and denied operations
- **Error sanitization** — strips internal URLs, SQL fragments, file paths, stack traces from error messages
- **XXE protection** — XML-RPC responses parsed with `defusedxml`
- **Domain validation** — Odoo domain filters validated for operators, field names, value types, nesting depth, and list sizes
- **Session-expiry retry** — JSON-RPC retries only on `OdooSessionExpiredError`, not generic auth errors (no double round-trips on access denials)

## Authentication

Three stock Odoo auth methods — no custom addon needed:

| Method | Protocol | Use Case |
|--------|----------|----------|
| `api_key` | XML-RPC | Server-to-server, CI/CD pipelines |
| `password` | JSON-RPC | Interactive users, Claude Desktop |
| `session` | JSON-RPC | Reuse existing browser session (development) |

```
# Example: login via the MCP tool
> login(method="password", username="admin", credential="admin", database="mydb")
```

## Core MCP Tools (13)

| Tool | Description |
|------|-------------|
| `login` | Authenticate with Odoo (api_key / password / session) |
| `list_models` | List accessible models with metadata and keyword filter |
| `get_model_fields` | Get field definitions for a model with optional filter |
| `search_read` | Search records with domain filters, field selection, ordering |
| `get_record` | Get a single record by ID |
| `search_count` | Count matching records |
| `create_record` | Create a new record (supports `dry_run` for validation-only) |
| `update_record` | Update existing record (supports `dry_run` for validation-only) |
| `delete_record` | Delete a single record by ID (supports `dry_run`) |
| `read_group` | Aggregated grouped reads with temporal operators (`date:month`, `date:quarter`, etc.) |
| `get_defaults` | Preview Odoo default values before `create_record` |
| `get_onchange` | Preview field side effects (with RBAC filtering) |
| `execute_method` | Call allowed model methods (supports `dry_run`) |

## Workflow Tools (2)

| Tool | Description |
|------|-------------|
| `get_create_requirements` | Get required fields and validation rules before creating a record |
| `get_record_actions` | Get available workflow actions for an existing record |

## MCP Resources (6)

| URI | Description |
|-----|-------------|
| `odoo://models` | List all accessible models |
| `odoo://models/{name}` | Model detail with field definitions |
| `odoo://record/{model}/{id}` | Single record data with RBAC field filtering |
| `odoo://schema/{model}` | Field schema with type info and importance ranking |
| `odoo://categories` | Model categories with counts |
| `odoo://workflow/{model}` | Workflow definition with stages and actions for a model |

## MCP Prompts (12)

| Prompt | Description |
|--------|-------------|
| `analyze_model` | Comprehensive model structure analysis |
| `explore_data` | Natural language data exploration guide |
| `create_workflow` | Guide through model-specific workflows |
| `compare_records` | Side-by-side record comparison |
| `generate_report` | Analytical report generation |
| `discover_custom_modules` | Find and understand custom modules |
| `debug_access` | Troubleshoot access and permission issues |
| `workflow_guide` | Step-by-step workflow execution guide for a model |
| `record_creation_guide` | Guided record creation with field validation |
| `bulk_operations` | Guide for performing bulk operations safely |
| `field_mapping` | Map fields between Odoo versions (v17/v18/v19) |
| `data_migration` | Guide for migrating data between models or versions |

## Built-in Domain Plugins

### HR Plugin
| Tool | Description |
|------|-------------|
| `check_in` | Record attendance check-in |
| `check_out` | Record attendance check-out |
| `get_my_attendance` | View attendance records (with month filter) |
| `get_my_leaves` | View leave requests (with state filter) |
| `request_leave` | Submit a leave request |
| `get_my_profile` | View employee profile |

### Sales Plugin
| Tool | Description |
|------|-------------|
| `get_my_quotations` | List quotations/orders (with state filter) |
| `get_order_details` | Full order details with line items |
| `confirm_order` | Confirm a draft/sent quotation |
| `get_sales_summary` | Aggregated sales statistics (with period filter) |

### Project Plugin
| Tool | Description |
|------|-------------|
| `get_my_tasks` | List assigned tasks (with state/project filter) |
| `get_project_summary` | Project stats: task counts by stage, overdue |
| `update_task_stage` | Move a task to a different stage |

### Helpdesk Plugin
| Tool | Description |
|------|-------------|
| `get_my_tickets` | List assigned tickets (with state/priority filter) |
| `create_ticket` | Create a new helpdesk ticket |
| `update_ticket_stage` | Move a ticket to a different stage |

## Custom Module Support

Add custom Odoo modules without writing Python code. Edit `model_access.yaml`:

```yaml
custom_models:
  full_crud:
    - custom.delivery.route
    - custom.warehouse.zone
  read_only:
    - custom.delivery.log

allowed_methods:
  custom.delivery.route:
    - action_dispatch
    - action_complete
    - action_cancel
```

Then all CRUD tools (`search_read`, `create_record`, `update_record`, `delete_record`) and `execute_method` work on the custom models with full security enforcement.

## Plugin System

Extend the gateway with pip-installable plugins:

```python
from odoo_mcp_gateway.plugins.base import OdooPlugin

class ManufacturingPlugin(OdooPlugin):
    @property
    def name(self) -> str:
        return "manufacturing"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["mrp"]

    @property
    def required_models(self) -> list[str]:
        return ["mrp.production", "mrp.bom"]

    def register(self, server, context):
        @server.tool()
        async def get_production_orders(...):
            ...
```

Register via `pyproject.toml` entry points:

```toml
[project.entry-points."odoo_mcp_gateway.plugins"]
manufacturing = "my_package:ManufacturingPlugin"
```

## Configuration Files

| File | Purpose |
|------|---------|
| `config/restrictions.yaml` | Model/method/field block lists (3 tiers) |
| `config/model_access.yaml` | Per-model access policies, allowed methods, sensitive fields |
| `config/rbac.yaml` | Role-based tool access and field filtering by group |
| `config/gateway.yaml` | Server, connection, auth settings |

All files have `.example` templates with extensive inline documentation. Copy and customize:

```bash
cp config/restrictions.yaml.example config/restrictions.yaml
cp config/model_access.yaml.example config/model_access.yaml
cp config/rbac.yaml.example config/rbac.yaml
```

### Example: Restrict a Model

```yaml
# restrictions.yaml
restrictions:
  always_blocked:
    - my.secret.model
  admin_only:
    - hr.salary.rule
  admin_write_only:
    - res.company
  blocked_write_fields:
    - password_crypt
    - api_key
    - totp_secret
```

### Example: RBAC by Group

```yaml
# rbac.yaml
rbac:
  tool_group_requirements:
    delete_record:
      - base.group_system
    execute_method:
      - base.group_erp_manager
  sensitive_fields:
    hr.employee:
      salary:
        required_group: hr.group_hr_manager
```

## Docker

```bash
cp .env.example .env   # Edit with your Odoo settings
docker compose up
```

Services:
- **MCP Gateway** — port 8080 (streamable-http mode)
- **Odoo 18** — internal only (no host port exposed by default)
- **PostgreSQL** — internal only

The gateway runs as a non-root user in a minimal Python image.

## CLI Tools

```bash
# Test Odoo connectivity
odoo-mcp-tools test-connection --url http://localhost:8069

# Validate all YAML config files
odoo-mcp-tools validate-config --config-dir config

# List configured model access policies
odoo-mcp-tools list-models --config-dir config
```

## Development

```bash
git clone https://github.com/parth-unjiya/odoo-mcp-gateway.git
cd odoo-mcp-gateway
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=odoo_mcp_gateway --cov-report=term-missing

# Lint
ruff check src/ tests/

# Type check (strict mode)
mypy src/
```

### Source Layout

```
src/odoo_mcp_gateway/
├── __main__.py                  # Entry point (stdio + HTTP)
├── server.py                    # FastMCP server setup, tool registration
├── config.py                    # Pydantic settings (env + .env)
├── client/
│   ├── base.py                  # OdooClientBase ABC, AuthResult
│   ├── jsonrpc.py               # JSON-RPC client (session auth)
│   ├── xmlrpc.py                # XML-RPC client (API key auth, defusedxml)
│   └── exceptions.py            # OdooError hierarchy (7 types)
├── core/
│   ├── auth/manager.py          # 3 auth strategies
│   ├── connection/manager.py    # Circuit breaker + retry
│   ├── version/                 # Odoo 17/18/19 detection + adapters
│   ├── workflow/
│   │   ├── definitions.py      # WorkflowDef, StateDef, TransitionDef dataclasses
│   │   ├── registry.py         # Workflow registration and lookup
│   │   └── stock_workflows/    # Built-in workflows (sale, purchase, HR, etc.)
│   ├── security/
│   │   ├── restrictions.py      # 3-tier model/method restrictions + hardcoded guardrails
│   │   ├── rbac.py              # Tool access + field filtering
│   │   ├── middleware.py        # Security pipeline + security_gate()
│   │   ├── rate_limit.py        # Token bucket rate limiter
│   │   ├── audit.py             # Structured audit logging
│   │   ├── sanitizer.py         # Error message sanitization
│   │   └── config_loader.py     # YAML config → Pydantic models
│   └── discovery/
│       ├── model_registry.py    # ir.model auto-discovery
│       ├── field_inspector.py   # fields_get with TTL cache
│       └── suggestions.py       # Category search + related models
├── tools/
│   ├── auth.py                  # login tool
│   ├── schema.py                # list_models, get_model_fields
│   ├── crud.py                  # search_read, create/update/delete, execute_method
│   └── workflow.py              # get_create_requirements, get_record_actions
├── resources/handlers.py        # 6 MCP resources (odoo:// URIs)
├── prompts/handlers.py          # 12 MCP prompt templates
├── plugins/
│   ├── base.py, registry.py     # Plugin ABC + entry_point discovery
│   └── core/                    # Built-in plugins (HR, Sales, Project, Helpdesk)
├── cli/tools.py                 # CLI: test-connection, validate-config
└── utils/                       # Domain builder, formatting, token budget
```

## Testing

**1,476 tests passing, 93% code coverage**, mypy strict clean, ruff clean:

```
tests/unit/
├── client/          # JSON-RPC, XML-RPC, auth manager, XXE protection
├── security/        # Restrictions, RBAC, audit, rate limit, sanitizer, security_gate
├── discovery/       # Model registry, field inspector, suggestions
├── tools/           # All 13 MCP tools + input validation + dry_run
├── plugins/         # Plugin system + 4 domain plugins + IDOR protection
└── cli/             # CLI utility tools
```

```bash
# Run all tests
pytest tests/ -v

# Run specific area
pytest tests/unit/security/ -v
pytest tests/unit/tools/ -v
pytest tests/unit/plugins/ -v

# Coverage report
pytest tests/ --cov=odoo_mcp_gateway --cov-report=html
```

## Error Handling

All Odoo errors are classified into 7 types:

| Error | Cause |
|-------|-------|
| `OdooConnectionError` | Cannot reach Odoo server |
| `OdooAuthError` | Invalid credentials |
| `OdooAccessError` | ir.model.access denied |
| `OdooValidationError` | Field validation failure |
| `OdooUserError` | Business logic error |
| `OdooMissingError` | Record not found |
| `OdooVersionError` | Unsupported Odoo version |

All error messages are sanitized before reaching the MCP client — internal URLs, SQL fragments, file paths, and stack traces are automatically stripped.

## Known Limitations

- **XML-RPC credential handling**: When using API key authentication (XML-RPC), the credential is sent with every RPC call as required by the protocol. Use HTTPS in production. (Note: passwords are stored in a `Credential` wrapper that prevents `repr()`/traceback leakage.)
- **HTTP mode session isolation**: `streamable-http` transport currently has known session isolation limitations — the `_current_session_key` ContextVar is set inside the login tool but subsequent tool calls from different request contexts may fall back to the first available session. **Deploy HTTP mode as single-tenant only** (one user per server process) until per-request middleware lands in v0.3.0. stdio mode is single-session by design and unaffected.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes with tests
4. Ensure all checks pass: `pytest && ruff check src/ tests/ && mypy src/`
5. Submit a pull request

## License

[MIT](LICENSE)
