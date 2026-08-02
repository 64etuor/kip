# Connectors

## Connector contract

A connector discovers immutable source revisions and emits canonical source events. It must provide stable external IDs, cursors, ACL scope, and deletion semantics. It must not parse business meaning.

## Filesystem/NAS

- Mount read-only.
- Use path relative to configured source root as the external ID.
- Wait for file size and mtime to settle before hashing.
- Do not treat an unavailable mount as mass deletion.
- Hash content for revision identity.

## HWP/HWPX parser broker

Default command templates are configuration, not Core code.

```toml
[parsers.hwp.kordoc]
enabled = true
argv = ["npx", "-y", "kordoc@4.2.7", "{input}", "--format", "json"]

[parsers.hwp.unhwp]
enabled = false
argv = ["unhwp", "convert", "{input}", "-o", "{output_dir}", "--all"]
```

Validate these commands against the actual installed parser version. The broker records parser/version/output hash and never replaces a successful active extraction with a failed run.

Keep production parser versions pinned. Upgrade a pin only after the parser conformance corpus and shadow-extraction comparison pass; do not use `@latest` in scheduled indexing.

## Slack

- Prefer conversations history/replies and event revisions over Slack search.
- Record workspace, conversation, timestamp, thread root, edit timestamp, and deletion tombstone.
- Respect channel membership and token scopes.
- Download files only when explicitly enabled; otherwise retain metadata and a protected source URI.

## Apple Mail

- Use the macOS JXA adapter or a server protocol connector.
- Configure account and mailbox allowlists.
- Grant Mail Automation permission to the host process.
- Do not read `~/Library/Mail` databases directly in the normal path.

## IMAP

- Use UID-based cursors, not sequence numbers.
- Store Message-ID, In-Reply-To, References, mailbox placement, and RFC822 hash.
- Separate a message from its mailbox placements.

## Push connector API

External applications may send canonical `ConnectorEvent` objects to `POST /v1/connectors/events`.

- Use a stable `event_id` and stable `external_id` for every source object.
- Replaying the same operation and payload is idempotent because the canonical revision hash is unchanged.
- Authenticate with both the API key and admin key in the starter profile.
- Propagate the source ACL in `acl_scopes`; never grant broader access than the originating system.
- Treat `delete` as an immutable tombstone revision rather than physically deleting prior evidence.

Example:

```bash
curl -sS http://127.0.0.1:8080/v1/connectors/events \
  -H 'Content-Type: application/json' \
  -H "X-KIP-API-Key: $KIP_API_KEY" \
  -H "X-KIP-Admin-Key: $KIP_ADMIN_KEY" \
  -H 'X-KIP-Workspace: default' \
  -H 'X-KIP-Principal: connector-custom-crm' \
  -H 'X-KIP-ACL-Scopes: workspace:default,project:A' \
  --data-binary @examples/connector/event.json
```
