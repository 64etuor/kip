# ADR-060: Bind answer file scope before retrieval

Status: Accepted — 2026-09-10

## Problem

The 3.7.0 answer flow inferred named-file scope from its limited, fresh hits.
When a requested file ranked lower or its source had changed, the constraint
disappeared and the answer could quote an unrelated approval. Opening quotes
and backticks also prevented filename recognition.

## Decision

Resolve candidate basenames from the ACL/current-root/request-filtered index
before ranking or live reads. One parser recognizes literal names, balanced
quotes/backticks, particles and exclusions; longest overlapping references win.
Unresolved references to supported document filename extensions refuse without
disclosing whether the file exists outside the caller's scope.

The answer service passes an internal `FilenameSearchRequest` to the existing
retrieval pipeline. Positive and negative filename predicates apply before
lexical and vector limits. They cannot grant access: existing ACL, active
revision, source-root and request filters remain mandatory. Public request
schemas/tool names and envelopes remain unchanged; ordinary search is still
discovery and does not impose answer-specific file binding.

Named files with differing allowed contents are checked for ambiguity before
limits. Exact Unicode caseless matching uses NFC plus Python casefold and
PostgreSQL 18's `pg_catalog.pg_unicode_fast` collation. The default libc
collation cannot be assumed to perform Unicode case folding.

Live reads then admit only fresh evidence from the bound scope. Approved
ontology evidence must be among the eligible retrieved units and respect the
same explicit source choice. All positively named files must have usable fresh
evidence; missing one cannot silently reduce a multi-file request. This qualifies
ADR-059's earlier statement that ontology evidence is always retained. Missing
or stale scoped evidence must never authorize substitution from another file.

## Consequences and checks

The filename resolver is a metadata lookup, not a corpus sync or source-byte
read. Quoted names are preferred for filenames with spaces or punctuation;
unknown standard document references fail closed. Natural-language filename
inference remains bounded, not a general query language or proof of absence.

Regression coverage includes the original wrong-source reproductions on Memory
and PostgreSQL, denied/removed sources, exclusion with limit=1, vector filtering
before nearest-neighbor limit, Unicode names and CLI/REST/MCP parity. Question
words are normalized before noun-particle stripping; this is an answerability
fix, not a claim of general semantic retrieval quality.
