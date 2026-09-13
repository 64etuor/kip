# Reporting a security issue

This file is the reporting policy. The system's actual security design — trust
boundaries, database roles, ACL enforcement, egress rules, the threat model —
is [`docs/SECURITY.md`](../docs/SECURITY.md), and it is the document to read
before deciding whether what you found is a real boundary failure.

## How to report

Use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/64etuor/kip/security/advisories/new)**.
That form is private to you and the maintainer, and it is the only private
channel this project publishes — there is no security mailing address.

Please do not open a public issue, a pull request with a fix, or a discussion
thread for a suspected vulnerability. A pull request that quietly fixes an
unreported issue is itself a disclosure.

Include, as far as you can:

- The version, from the `data.version` field of `kip version`, and whether it is
  a release package, a git checkout or the container image.
- Which boundary you believe is crossed, in the vocabulary of
  [`docs/SECURITY.md`](../docs/SECURITY.md) — retrieval authorization, the
  filesystem access boundary, database roles, model egress, the package archive
  boundary, and so on.
- A reproduction that uses the bundled `sample-data/` corpus or synthetic files.
  Never send documents, mail, workbook values or credentials from a real corpus;
  ids, hashes, error codes and `meta.request_id` are enough to trace a request.
- Whether the deployment still had the documented defaults, or had changed them
  (API bound off loopback, remote model egress approved, a non-default database
  role, source roots writable).

## What is in scope

The boundaries this system claims to enforce, and therefore the ones worth
reporting:

- **Retrieval authorization.** A hit, snippet, citation, graph edge, count or
  error message reaching a principal whose ACL scopes do not permit it — or ACL
  filtering that happens after ranking and limits rather than before them.
- **Source and filesystem scope.** Reading bytes outside the configured enabled
  roots, escaping a root through a symlink or a path in an index record, or a
  removed root still serving content without an explicit re-sync.
- **Database roles and row-level security.** A code path that reaches canonical
  state with more privilege than its role is meant to have, or that bypasses
  row-level security.
- **Model egress.** Source text, query text or metadata leaving the host when
  remote egress has not been approved, or the local model sidecar becoming
  reachable off loopback.
- **Parser and extraction isolation.** A crafted HWP/HWPX, PDF, PPTX or XLSX
  file that achieves code execution, escapes the privileges the parser is meant
  to run with, exhausts the host through a decompression or recursion bomb, or
  causes a failed shadow extraction to destroy the active one.
- **The instruction/data boundary.** Content inside an indexed document being
  acted on as an instruction by KIP itself rather than returned as evidence.
  (Returning injected text as data with its locator is the design, not the bug —
  see below.)
- **The packaged archive and release integrity.** A path in a package that
  escapes the extraction root, a manifest, SHA-256 or attestation check that can
  be bypassed or downgraded, an upgrade that overwrites files it does not own,
  or secrets present in a published archive.
- **Secret handling.** Credentials, tokens or connection strings appearing in
  logs, query traces, evaluation reports, envelopes or error details.
- **Authentication on the optional REST and MCP adapters**, including token
  validation and identity/ACL snapshot handling.

## What is out of scope

Not because these do not matter, but because they are not vulnerabilities in
this codebase and a report will be closed as such:

- A finding that requires the deployment to have already disabled a documented
  default — binding the API to a public interface, approving remote egress,
  running as a database superuser, mounting sources writable.
- Prompt injection *content* that KIP surfaces as evidence with its locator.
  Source bodies are untrusted data by design and callers are told so. An actual
  failure of that boundary is in scope; the presence of injected text is not.
- A dependency CVE with no exploit path through KIP. Dependabot and
  `pip-audit` in CI already track those; open a normal issue if a bump is stuck.
- Resource exhaustion from operator-chosen work, such as indexing a very large
  corpus or building an embedding projection.
- The in-memory repository (`memory://`). It is documented as non-durable and
  intended for tests and demos.
- Anything that assumes host or root access to a machine already running KIP.
- Reports from automated scanners with no demonstrated impact on the boundaries
  listed above.

## What you can expect

KIP is maintained by one person, with no security team and no bug bounty. The
commitments here are deliberately ones a single maintainer can keep:

- An acknowledgement within **7 days**. If you have heard nothing after 14 days,
  add a comment to the advisory — it means the notification was missed.
- An assessment of whether it is in scope, and of severity, within **30 days** of
  acknowledgement.
- A fix timeline that depends on severity and is agreed with you in the advisory
  rather than promised in advance.
- Coordinated disclosure, defaulting to publishing the advisory once a fixed
  release is out, or **90 days** after the report, whichever comes first. Say so
  in the report if you need a different timeline.
- Credit in the advisory and in `CHANGELOG.md` unless you ask to stay anonymous.

## Supported versions

Only the latest release receives fixes. There are no maintained release
branches, and no backports to earlier minor versions. The latest version is the
one on the [releases page](https://github.com/64etuor/kip/releases/latest);
compare it with `kip version`, and upgrade in place with `kip update` or
`./scripts/upgrade.sh` (see
[`docs/DEPLOYMENT_GUIDE.md`](../docs/DEPLOYMENT_GUIDE.md)).

Release artifacts are built by the tagged CI run, carry SHA-256 sums and build
provenance attestations, and the wheel carries an SPDX SBOM. Verify a download
before running it; the installer verifies the archive against its `.sha256`
before extracting.

## Third-party components

KIP is MIT, but it ships components under other licences, two of them copyleft
(AGPL-3.0 PyMuPDF, LGPL-3.0 psycopg). That is a distribution obligation rather
than a vulnerability; the list is in
[`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md). Vulnerabilities in those
components belong to their own projects, though a KIP-specific exploit path
through one is in scope here.
