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

The reference profile uses the in-process `hwp-hwpx-parser` adapter first.
Command parsers are optional, explicitly installed fallbacks; their templates
remain configuration, not Core code. Runtime indexing must never download or
execute an unpinned package installer.

```toml
[parsers.hwp]
order = ["hwp-hwpx-parser", "kordoc", "unhwp", "paired_pdf"]

[parsers.hwp.hwp-hwpx-parser]
enabled = true
max_chars_per_unit = 4000

[parsers.hwp.kordoc]
enabled = false
argv = ["kordoc", "{input}", "--format", "json"]

[parsers.hwp.unhwp]
enabled = false
argv = ["unhwp", "convert", "{input}", "-o", "{output_dir}", "--all"]
```

Validate command fallbacks against the exact installed version before enabling
them. The broker records parser/version/output hash. A failed or below-threshold
candidate never replaces the successful active extraction.

When an existing index must adopt a new HWP parser, prepare the entire source in
non-mutating shadow mode first. Activation is a separate operator action:

```bash
./scripts/kip parser reextract --source company-nas
./scripts/kip parser reextract --source company-nas --activate
```

The operation hashes only configured HWP/HWPX candidates. Activation inherits
and rechecks the current canonical source ACL snapshot and classification,
retains extraction history, replaces only the rebuildable active lexical
projection, and commits each document atomically. It never writes to the source
file or changes source access policy.

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
  --data-binary @examples/connector/event.json
```

The authenticated server identity authorizes the write. Source ACLs belong in
the versioned connector event payload and are validated by the connector
contract; they are not caller identity headers.
