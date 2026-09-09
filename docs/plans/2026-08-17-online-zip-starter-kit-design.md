# Online ZIP Starter Kit Design

- **Status:** Approved by direct user instruction
- **Date:** 2026-08-17

## Goal

Produce one internet-connected ZIP that contains only the source, contracts,
configuration examples, migrations, operational tooling, tests, and canonical
documents required to adopt KIP safely in a new environment.

## Reference findings

The historical `KIP_knowledge_fabric_starter_v3.1.zip` is a useful source-kit
reference, but it predates the current contracts and includes an empty
`secrets/` directory and obsolete optional topology. The current verified
release bundle has stronger manifest, checksum, private-data, and path-safety
controls, but its primary archive is a deployment-oriented TAR containing
wheel and immutable image evidence.

The new artifact is a third surface: a minimal, verified, online source ZIP.
It does not replace the signed production release bundle.

## Archive contract

The default artifact is `dist/kip-starter-kit-<VERSION>.zip`. It contains one
root directory named `kip-starter-kit-<VERSION>/` with:

- root agent, packaging, Compose, license, and example-environment files;
- all application source, migrations, ontology, contracts, tests, SDK, skills,
  deployment templates, operational scripts, examples, and sample data;
- canonical product, architecture, security, operations, integration,
  troubleshooting, adoption, evaluation, and readiness documents plus ADRs;
- only the evaluation fixtures needed for portable acceptance and starter
  review, never private organization questions or historical report output;
- `STARTER-KIT-MANIFEST.json` and `SHA256SUMS` covering every payload file.

It excludes local configuration, generated configuration, secrets, databases,
CAS/state, caches, worktrees, plans, dated audit reports, private golden data,
model caches, and release/build output.

## Build and verification

`kip-starter-kit build` resolves files through an explicit allowlist, rejects
symlinks and forbidden paths, scans small text files for private paths,
credentials, and private keys, and refuses a dirty source tree unless the
operator explicitly passes `--allow-dirty`. ZIP entries are sorted with a
normalized permission mode and a timestamp derived from `SOURCE_DATE_EPOCH` or
the source commit, making identical inputs reproducible.

`kip-starter-kit verify` treats the ZIP as untrusted input. It rejects unsafe
member paths, multiple roots, symlinks, unexpected files, missing canonical
files, checksum drift, manifest drift, private/local-state paths, and secret
patterns without extracting the archive.

The manifest uses `kip.starter-archive.v1` and records the KIP version, source
commit, tracked-change state, creation timestamp, root directory, and SHA-256
for each payload file. The external `<archive>.sha256` file covers the ZIP
itself.

## Adoption path

After verification and extraction, the recipient starts with `README.md` and
`docs/STARTER_KIT_GUIDE.md`, runs `./scripts/bootstrap.sh`, completes the
guided setup, then runs doctor, migration, sample sync/read, and
`./scripts/verify.sh`. Internet access supplies Python, OCR, container, and
model dependencies; none are vendored into the ZIP.

## Non-goals

- embedding Docker images, model weights, secrets, private corpora, or DB dumps;
- claiming broad-production acceptance without recipient-environment evidence;
- replacing signed image/wheel provenance or the production release bundle;
- shipping historical plans or measured reports as if they were current truth.
