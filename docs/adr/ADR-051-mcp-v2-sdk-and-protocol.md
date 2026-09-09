# ADR-051: Adopt the stable MCP 2 SDK without changing KIP tool contracts

- **Status:** Accepted
- **Date:** 2026-08-16

## Context

The MCP Python SDK 2.0 line is stable while 1.x is in maintenance. Version 2
renames the high-level server from `FastMCP` to `MCPServer`, changes direct tool
call results to `CallToolResult`, and implements protocol `2026-07-28` while
retaining negotiated support for legacy clients. KIP was pinned to MCP 1.x, so
its optional agent edge and client contract tests no longer exercised the
current supported SDK.

The KIP JSON envelope, application services, and environment-derived
`RequestContext` are product contracts. An SDK upgrade must not create a
second data contract or allow protocol metadata to become authorization data.

## Decision

1. Pin the optional dependency to `mcp>=2.0,<3` and lock 2.0.0.
2. Construct `MCPServer` with the KIP package version so clients can identify
   the running application build.
3. Keep stdio as the only shipped transport and keep every tool result inside
   `kip.envelope.v1`.
4. Continue deriving workspace, principal, ACL scopes, and roles only from the
   trusted launch environment. Protocol metadata and capabilities grant no
   authority.
5. Do not use sampling, elicitation, roots, protocol logging, or another
   server-initiated backchannel.
6. Protect the boundary with an MCP 2 in-process client contract test and a
   real stdio process QA covering initialization, discovery, version, and a
   tool call. A future Streamable HTTP edge needs its own security and
   deployment decision.

## Consequences

- Current clients negotiate protocol `2026-07-28`; SDK-supported legacy
  clients remain usable without changing KIP tool payloads.
- Synchronous tool handlers execute through the SDK's worker-thread boundary;
  KIP application services remain unchanged.
- The MCP lock introduces its `httpx2` transport dependency. It must not be
  confused with or substituted for KIP's existing `httpx` application client.
- Runtime requirements, SBOM inputs, tests, operations, security guidance, and
  starter-kit upgrade guidance must stay synchronized with the SDK major.

## References

- MCP Python SDK v2 migration guide
- MCP Python SDK v2 release notes
- `docs/DATA_CONTRACTS.md`
- `docs/SECURITY.md`
