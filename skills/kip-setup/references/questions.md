# Answer formats

Use the exact `answer_format`, `choices`, and `example` returned by `kip setup inspect`. These examples clarify the non-scalar forms.

## Filesystem sources

Pass one JSON array. Each object requires `name`, `root`, `classification`, and `acl_scope`. Optional `include_extensions` and `exclude_globs` narrow collection further.

```json
[{"name":"company-docs","root":"/mnt/nas/team","classification":"internal","acl_scope":"workspace:acme-rnd","include_extensions":[".pdf",".hwpx"],"exclude_globs":["**/archive/**"]}]
```

Explain each field to the user in plain language before asking — most users
have never seen these terms:

| Field | Plain meaning | Guidance |
|---|---|---|
| `name` | 폴더 별명 (수집 명령에서 쓰는 이름) | 영문 소문자·하이픈 |
| `root` | 실제 절대경로 | 예: `/mnt/nas/영업팀` |
| `classification` | 민감도 등급 | 아래 표 |
| `acl_scope` | 이 자료를 볼 수 있는 그룹 이름표 | 형식 `workspace:이름`; 보통 조직 workspace와 동일 |
| `include_extensions` | 색인할 확장자 | 생략하면 기본 목록 |
| `exclude_globs` | 제외 패턴 | 예: `**/backup/**` |

Classification levels (also used by the model-egress question — only the
levels listed there are ever sent to a remote model):

| Level | Meaning |
|---|---|
| `public` | 외부 공개 가능 |
| `internal` | 사내 전체 공유 가능 |
| `confidential` | 관련 부서만 |
| `restricted` | 지정된 소수만 (계약서·인사 등) |
| `personal` | 개인정보 포함 |

When the user is unsure, choose the stricter level; it can be relaxed later.
Start with one folder, verify the result, then add more.

Use named subdirectories only. `/`, a home directory, a project root, or a parent containing the project is intentionally rejected. Source folders are mounted read-only and are never modified.

## Identity mode

`api_key` is the right answer for a single user or a small team piloting the
system: it works immediately with a generated key. Choose `proxy_jwt` only when
the deployment must recognize individual employees through company SSO — that
path additionally needs the issuer, audience, and JWKS URL from whoever runs
the identity provider. Never accept caller-supplied identity headers in either
mode.

## Lists

Pass classifications and ontology reviewers as JSON arrays, including an empty array only when the CLI question permits it.

## Ontology profile and interaction memory

First explain what an ontology is, in one sentence the user can act on:
"KIP이 문서에서 인식할 개념(사람·과제·계약 등)과 관계(무엇이 무엇을 승인했는지)를
정의한 사전입니다." Then: choose `empty` for a new organization unless the
bundled research-project example is intentionally the starting meaning contract
— a mismatched example vocabulary confuses reviewers more than an empty one. `explicit_consent`
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
