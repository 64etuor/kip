# Answer formats

Use the exact `answer_format`, `choices`, and `example` returned by `kip setup inspect`. These examples clarify the non-scalar forms.

## Filesystem sources

Pass one JSON array. Each object requires `name`, `root`, `classification`, and `acl_scope`. Optional `include_extensions` and `exclude_globs` narrow collection further.

```json
[{"name":"company-docs","root":"/mnt/nas/team","classification":"internal","acl_scope":"workspace:acme-rnd","include_extensions":[".pdf",".hwpx"],"exclude_globs":["**/archive/**"]}]
```

Use named subdirectories only. `/`, a home directory, a project root, or a parent containing the project is intentionally rejected.

## Lists

Pass classifications and ontology reviewers as JSON arrays, including an empty array only when the CLI question permits it.

## Ontology profile and interaction memory

Choose `empty` for a new organization unless the bundled research-project
example is intentionally the starting meaning contract. Choose
`explicit_consent` only when users may opt in to durable preferences and
non-activating ontology discovery candidates. This does not enable automatic
facts, automatic YAML edits, or raw query/answer retention.

## Secrets

Pass only a reference such as `env:KIP_DATABASE_URL`, `keychain:kip/openai`, or `secret-manager:prod/kip/database`. Never pass a URL containing a password, an API key, or a token.

## Corrections

Re-answer a previously recorded question to revise it. Any existing plan then becomes stale; generate and approve a new plan.
