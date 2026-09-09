# ADR-056: Current filesystem roots authorize existing evidence

- Status: Accepted
- Date: 2026-09-10

## Context

Filesystem roots previously bounded scanning while existing indexed records
remained retrievable after a source was removed, disabled, or moved. Reusing a
same-name source with equal file stats could also skip the parse needed to
establish evidence under the new root. Cloud placeholders were opened for
hashing before parser isolation, and excluded trees flooded sync warnings.

## Decision

Compose a deployment-owned filesystem policy from enabled source entries:
source name, resolved root, and the configuration ACL snapshot identity.
An empty configured list denies filesystem evidence. Combine it with request
ACLs before repository ranking, limits, vocabulary, and graph traversal.
Memory and PostgreSQL share this meaning; migrations 0024/0025 install the
private transaction policy helpers and require every assertion evidence unit's
current ACL, snapshot freshness, and source access. Non-filesystem connector
authorization is unchanged.

Exact reads recheck live path containment and symlink policy before opening
source bytes. OS cloud-residency metadata defers placeholders before hashing or
parsing. Cached text can remain readable with stale/hash-unavailable evidence;
live workbook reads cannot proceed without local source bytes. Scan diagnostics
aggregate skip reasons and preserve present-but-deferred paths for deletion
accounting. Symlink escapes and walk cycles are rejected.

Scope changes invalidate stat/hash reuse across roots. A successful parse and
URI-qualified revision establish evidence under the changed scope, even for
identical bytes. Stable source object IDs and the current unchanged-path/hash
revision survive; later changed-byte/location revisions bind the URI so a byte
reversion cannot reactivate an incompatible historical revision. Same-root
ACL/classification changes refresh unit metadata on explicit sync. Derived
assertion grants refresh only from complete authorized evidence; partial
visibility can retain a conservatively stricter cached grant.

## Consequences

No new search parameter or caller-supplied root can expand access. Reloading a
service applies changed configuration to old rows without deleting them or
starting a sync. An explicit sync reauthorizes the retained scope. A known ID
does not bypass the boundary; a path escape returns `not_found`.

Read-only mounts and OS permissions remain necessary. Metadata detection is
provider/OS-dependent; it is not a OneDrive API integration, deliberate bulk
download, or a descriptor-pinned sandbox against concurrent path replacement.
Real-corpus coverage and model quality require separate measured acceptance.
