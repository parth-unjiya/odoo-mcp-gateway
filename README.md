# odoo-mcp-gateway

Security-first, version-agnostic MCP gateway for Odoo 17/18/19. Works with stock and custom modules via YAML configuration. Zero Odoo-side code required.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Odoo](https://img.shields.io/badge/Odoo-17%20%7C%2018%20%7C%2019-714B67.svg)](https://www.odoo.com/)

<!-- mcp-name: io.github.parth-unjiya/odoo-mcp-gateway -->

## Why This Exists

Existing Odoo MCP servers share common problems: hardcoded model lists that miss custom modules, security as an afterthought, mandatory custom Odoo addons, and single-version targets. This gateway solves all of them:

- **Two-layer security** — MCP restrictions (YAML) + Odoo's built-in ACLs (ir.model.access + ir.rule)
- **YAML-driven configuration** — model restrictions, RBAC, field-level access, rate limiting, audit logging
- **Custom module support** — auto-discovers models via `ir.model`, add YAML config and it works
- **Version-agnostic** — Odoo 17, 18, 19 with version-specific adapters
- **Zero Odoo-side code** — `pip install` + YAML config = done. No custom addon required
- **Full MCP primitives** — 27 Tools + 5 Resources + 7 Prompts (most servers only implement Tools)
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
    |-- tools/             --> 27 MCP tools (auth + schema + CRUD + plugins)
    |-- resources/         --> 5 MCP resources (odoo:// URIs)
    |-- prompts/           --> 7 reusable prompt templates
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
- **18 always-blocked models** (ir.config_parameter, ir.cron, ir.module.module, ir.rule, ir.mail_server, etc.)
- **18 always-blocked methods** (sudo, with_user, with_env, _sql, _write, _create, etc.)
- **28 ORM methods blocked in execute_method** (prevents bypassing field-level checks)

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

**Blocked models** (17): `ir.config_parameter`, `res.users.apikeys`, `ir.cron`, `ir.module.module`, `ir.model.access`, `ir.rule`, `ir.mail_server`, `ir.ui.view`, `ir.actions.server`, `res.config.settings`, `change.password.wizard`, `change.password.user`, `base.module.update`, `base.module.upgrade`, `base.module.uninstall`, `fetchmail.server`, `bus.bus`

**Blocked methods** (18): `sudo`, `with_user`, `with_company`, `with_context`, `with_env`, `with_prefetch`, `_auto_init`, `_sql`, `_register_hook`, `_write`, `_create`, `_read`, `_setup_base`, `_setup_fields`, `_setup_complete`, `init`, `_table_query`, `_read_group_raw`

### Additional Security Features

- **Rate limiting** — per-session token bucket with separate global and write budgets
- **RBAC** — tool-level access control by user group, field-level response filtering
- **Input validation** — model names, method names, field names, domain filters, ORDER BY clauses, write values (size/depth/type)
- **IDOR protection** — plugin tools scope data access to the authenticated user
- **Audit logging** — structured JSON logs for all allowed and denied operations
- **Error sanitization** — strips internal URLs, SQL fragments, file paths, stack traces from error messages
- **XXE protection** — XML-RPC responses parsed with `defusedxml`
- **Domain validation** — Odoo domain filters validated for operators, field names, value types, nesting depth, and list sizes

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

## MCP Tools (11)

| Tool | Description |
|------|-------------|
| `login` | Authenticate with Odoo (api_key / password / session) |
| `list_models` | List accessible models with metadata and keyword filter |
| `get_model_fields` | Get field definitions for a model with optional filter |
| `search_read` | Search records with domain filters, field selection, ordering |
| `get_record` | Get a single record by ID |
| `search_count` | Count matching records |
| `create_record` | Create a new record (validates field names and values) |
| `update_record` | Update existing record (validates field names and values) |
| `delete_record` | Delete a single record by ID |
| `read_group` | Aggregated grouped reads with aggregate functions |
| `execute_method` | Call allowed model methods (validates method name) |

## MCP Resources (5)

| URI | Description |
|-----|-------------|
| `odoo://models` | List all accessible models |
| `odoo://models/{name}` | Model detail with field definitions |
| `odoo://record/{model}/{id}` | Single record data with RBAC field filtering |
| `odoo://schema/{model}` | Field schema with type info and importance ranking |
| `odoo://categories` | Model categories with counts |

## MCP Prompts (7)

| Prompt | Description |
|--------|-------------|
| `analyze_model` | Comprehensive model structure analysis |
| `explore_data` | Natural language data exploration guide |
| `create_workflow` | Guide through model-specific workflows |
| `compare_records` | Side-by-side record comparison |
| `generate_report` | Analytical report generation |
| `discover_custom_modules` | Find and understand custom modules |
| `debug_access` | Troubleshoot access and permission issues |

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
│   └── crud.py                  # search_read, create/update/delete, execute_method
├── resources/handlers.py        # 5 MCP resources (odoo:// URIs)
├── prompts/handlers.py          # 7 MCP prompt templates
├── plugins/
│   ├── base.py, registry.py     # Plugin ABC + entry_point discovery
│   └── core/                    # Built-in plugins (HR, Sales, Project, Helpdesk)
├── cli/tools.py                 # CLI: test-connection, validate-config
└── utils/                       # Domain builder, formatting, token budget
```

## Testing

1,043 tests across all layers with 93% code coverage:

```
tests/unit/
├── client/          # JSON-RPC, XML-RPC, auth manager, XXE protection
├── security/        # Restrictions, RBAC, audit, rate limit, sanitizer, security_gate
├── discovery/       # Model registry, field inspector, suggestions
├── tools/           # All 11 MCP tools + input validation
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

- **Single-user stdio mode**: The gateway is designed for single-user `stdio` transport (Claude Desktop, Claude Code). Multi-user `streamable-http` mode works but sessions are not fully isolated between concurrent users.
- **XML-RPC credential handling**: When using API key authentication (XML-RPC), the credential is sent with every RPC call as required by the protocol. Use HTTPS in production.
- **Admin detection**: Admin status is derived from Odoo group membership. Non-English Odoo instances may need configuration adjustments for group name resolution.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes with tests
4. Ensure all checks pass: `pytest && ruff check src/ tests/ && mypy src/`
5. Submit a pull request

## License

[MIT](LICENSE)
