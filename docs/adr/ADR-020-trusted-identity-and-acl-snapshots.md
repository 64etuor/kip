# ADR-020: Trusted identity and expiring ACL snapshots

- Status: Accepted
- Date: 2026-08-09

## Context

The original starter API accepted workspace, principal, and ACL scopes from
ordinary request headers. That boundary is suitable only for isolated fixtures:
any caller able to reach the API could select another identity. Source ACLs also
had no provider version or expiry, so revoked directory membership could remain
searchable indefinitely.

## Decision

KIP resolves every request through an identity port. API-key mode maps one
constant-time verified credential to a configuration-owned principal and fixed
scopes. Production multi-user mode verifies JWT signature, issuer, audience,
algorithm allow-list, timestamps, identity claims, and an expiring principal ACL
snapshot against a bounded JWKS client. Legacy caller identity headers are
rejected outside development and test.

Every source object owns an ACL snapshot. Dynamic connector snapshots record a
provider, version, capture time, and expiry; configuration-owned filesystem
snapshots are explicitly non-expiring. Content units reference the active
snapshot, and approved assertions retain the snapshot IDs of their exact
evidence. PostgreSQL RLS and repository queries exclude stale snapshots before
lexical, vector, evidence, assertion, or graph rows can leave storage. The
memory reference adapter implements the same semantics.

## Consequences

- Revocation exposure is bounded by the identity/source snapshot TTL and token
  lifetime. Connector refresh cadence must be shorter than that TTL.
- An identity-provider or ACL-refresh outage can reduce availability, but it
  cannot silently preserve stale access.
- API-key mode remains useful for bootstrap and single-principal integrations;
  it is not a substitute for production multi-user identity.
- The PyJWT crypto stack is an optional package extra so non-HTTP and local
  minimal installs do not import or require it. Configuring JWT mode without the
  extra fails during container construction.
- Migration `0007_identity_acl_snapshots.sql` is append-only. Future changes to
  the snapshot schema require a new migration.
