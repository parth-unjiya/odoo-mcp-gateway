# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-03-17

### Added
- Intelligent Workflow Engine (v2 core feature)
  - Workflow definitions with state machines for common Odoo models
  - `odoo://workflow/{model}` resource for workflow discovery
  - `get_create_requirements` tool — guides AI through record creation
  - `get_record_actions` tool — shows valid next actions for a record
  - 5 new workflow prompts: quote_to_invoice, employee_onboarding, ticket_lifecycle, purchase_to_receipt, lead_to_opportunity
  - Stock workflows for sale.order, purchase.order, hr.leave, helpdesk.ticket, crm.lead
- Session isolation for HTTP/streamable-http mode (per-MCP-session state)
- Session timeout enforcement with lazy eviction
- Admin detection via `has_group()` XML-RPC call (works in non-English Odoo)
- ConnectionManager integration with circuit breaker and retry for all RPC clients
- Plugin operation type declaration (plugins can register tool operation types for correct rate limiting)
- Built-in plugin entry points registered in pyproject.toml
- Version adapter extensions: `get_removed_fields()`, `get_renamed_fields()`, `get_state_field_overrides()`
- CHANGELOG.md
- SECURITY.md with vulnerability disclosure policy
- .env.example for Docker and native setup

### Fixed
- Version mismatch between `__init__.py` (0.1.0) and pyproject.toml (0.1.1)
- MCP_PORT default now 8080 (was 8000), matching README and Docker documentation
- Credentials (password, login, db) scrubbed from memory on client close
- Duplicate transport resolution in `__main__.py` removed (was bypassing Pydantic validation)
- Missing `hr.attendance`, `helpdesk.ticket`, `helpdesk.team` added to model_access.yaml.example
- gateway.yaml.example now clearly marked as reference-only (not loaded by application)
- restrictions.yaml.example notes hardcoded guardrail overlap

### Improved
- Test coverage for `__main__.py`, ErrorSanitizer, HR plugin, and resource handlers
- Plugin entry points registered for built-in plugins (HR, Sales, Project, Helpdesk)

## [0.1.1] - 2026-03-16

### Added
- Initial public release
- 27 MCP tools (11 core + 16 plugin)
- 5 MCP resources
- 7 MCP prompts
- Two-layer security: MCP restrictions (YAML) + Odoo ACLs
- 4 built-in domain plugins: HR, Sales, Project, Helpdesk
- Version-agnostic: Odoo 17, 18, 19
- 1,043 tests, 93% coverage
- CI/CD with GitHub Actions
- Docker support
- PyPI publishing
