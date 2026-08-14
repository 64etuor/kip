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
example is intentionally the starting meaning contract. `explicit_consent`
is the recommended default: it enables durable confirmed preferences and the
ontology discovery loop, in which admin-approved discovery candidates
materialize an additive, shadow-validated ontology release automatically.
Choose `disabled` only when the deployment must not retain any interaction
state. Neither mode enables automatic facts or raw query/answer retention;
auto-released predicates default to review-required, so assertions using
them still need exact evidence and human review.

## Secrets

Pass only a reference, never a value. The runtime supports two schemes:

- `env:NAME` — resolved from the environment variable `NAME`, or from a
  single-line secret file named by `NAME_FILE`.
- `file:/absolute/path` — resolved from a single-line secret file. Accepted
  only where the question's answer format says `env: or file:` (the model
  credential).

The database URL (`database_secret_ref`) and the bootstrap identity keys
(`identity_api_key_secret_ref`, `identity_admin_key_secret_ref`) accept `env:`
only, because the runtime resolves them exclusively through environment
variables. `keychain:` and `secret-manager:` references are rejected: no
runtime resolver exists, so recording one would produce a deployment that
cannot start. Never pass a URL containing a password, an API key, or a token.

## Sync schedule

`sync_schedule` (`manual` or a 5-field cron) is recorded in the generated
configuration as declarative operations metadata. Nothing schedules syncs
automatically from this answer: run `kip sync run --source SOURCE` manually or
install the launchd job (`scripts/install-launchd.sh`), which configures its
own interval.

## Corrections

Re-answer a previously recorded question to revise it. Any existing plan then becomes stale; generate and approve a new plan.
