# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.2.x   | Yes                |
| 0.1.x   | Security fixes only|

## Reporting a Vulnerability

If you discover a security vulnerability in odoo-mcp-gateway, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email: **parth.unjiya@spaceo.in**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 1 week
- **Fix release**: Within 2 weeks for critical issues

### Scope

The following are in scope:
- Authentication bypass
- Authorization bypass (accessing restricted models/methods/fields)
- Credential leakage
- Injection vulnerabilities (domain injection, method injection)
- Information disclosure via error messages
- Rate limiting bypass
- Audit logging bypass

### Out of Scope

- Odoo server-side vulnerabilities (report to Odoo SA)
- Denial of service via legitimate API usage
- Issues requiring physical access to the server

## Security Architecture

See the [README](README.md#security) for details on:
- Two-layer security model
- Hardcoded safety guardrails
- YAML-driven restrictions and RBAC
- Input validation and error sanitization
