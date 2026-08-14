# ADR-040: Make guided setup end in a runnable deployment

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

Guided setup produced approved configuration artifacts but stopped there. A
fresh clone failed confusingly before bootstrap on old system Pythons, the
generated Compose override and config were written but nothing consumed them
without manual `docker compose -f` composition, `.mcp.json` pointed the host
MCP adapter at the container-path config so MCP was broken on the host, and
setup accepted `keychain:`/`secret-manager:` secret references that no runtime
could resolve — deferring the failure to runtime instead of the answer.

## Decision

1. `bootstrap.sh` fails fast on Python < 3.12 with actionable guidance, and
   the `kip-setup` skill starts with an inspect-then-bootstrap step 0;
   `STARTER_KIT_GUIDE` makes bootstrap step 1.
2. Setup accepts only resolvable secret references: `env:` everywhere,
   `file:` only where the answer format allows it (the model credential); the
   database URL and bootstrap identity keys are `env:`-only. `keychain:` and
   `secret-manager:` are rejected at answer time with guidance, and the
   egress secret-reference check is aligned to the same `{env, file}` set.
3. Setup writes two generated configs: container-path
   `config/kip.generated.toml` (bind-mounted by the generated Compose
   override) and host-path `config/kip.host.generated.toml`, which
   `.mcp.json` selects so the stdio MCP adapter works on the host. Both are
   git-ignored along with `.mcp.json.previous`.
4. `scripts/app-up.sh` layers `compose.generated.yaml` over the base
   `compose.yaml` when present (falling back to the plain app profile
   otherwise), so the approved read-only source mounts and generated config
   reach the containers without manual Compose editing.
5. The receipt records `next_steps` (migrate, app-up, sync, search smoke
   test) and a configuration-only limitation entry; `setup verify` adds
   `runtime_readiness` checks (python, docker CLI/daemon, database secret
   resolvable, source readability) whose environmental failures never flip
   `verified`.
6. The generated retrieval default is `mode = "reranked"`, matching the
   promoted BM25 default of ADR-034.

## Consequences

- The 60-minute acceptance path is bootstrap -> guided setup -> `next_steps`,
  with no hand-edited TOML, Compose, or `.mcp.json`; MCP works on the host
  again.
- Unresolvable secret schemes are impossible to record, so a plan can no
  longer pass verification while being undeployable; external secret managers
  must inject environment variables or files.
- Setup remains configuration-only: `apply`/`verify` index and serve nothing,
  and the recorded `sync_schedule` stays declarative metadata — periodic
  execution comes from the launchd installer or an external scheduler.

## References

- `docs/QUICKSTART.md`
- `docs/STARTER_KIT_GUIDE.md`
- `docs/DATA_CONTRACTS.md`
- `skills/kip-setup/SKILL.md`
- `contracts/setup-answers.schema.json`, `contracts/setup-receipt.schema.json`
- `scripts/app-up.sh`, `scripts/bootstrap.sh`
- ADR-034
