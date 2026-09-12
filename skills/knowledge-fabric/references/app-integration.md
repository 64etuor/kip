# Application and connector integration

## Choose an edge

- REST/OpenAPI: backend, web, mobile, workflow automation, and language-neutral clients.
- MCP: AI applications that support tool discovery.
- CLI JSON: local scripts, Claude Code, Codex, cron, and debugging.
- Connector event endpoint: external source adapters that normalize changes into versioned events.

## Required behavior

All edges must use the same application service. Do not place search ranking, ACL decisions, review promotion, or parser logic in controllers.

Pass workspace, principal, and ACL scopes on every request. Use separate API and admin keys in the starter profile; put production deployments behind organization-approved identity and network controls.

The REST API accepts no idempotency key: it has no such header or parameter, and KIP derives the keys it needs for durable jobs server-side. Make retries safe at your own edge instead. Do not expose PostgreSQL directly to applications; a graph database is not part of the deployment today and would be a rebuildable read projection if one were ever adopted. Generate clients from `contracts/openapi.yaml` or use `sdk/python/kip_client.py` as a minimal example.
