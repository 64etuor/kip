# Operations

## Daily

```bash
./scripts/doctor.sh
./scripts/kip status
./scripts/kip jobs list --status failed
```

## Incremental sync

Use source-specific cursors. A source outage must not be interpreted as deletion. Files are considered deleted only after a successful complete scan confirms absence according to the configured grace policy.

## Backup

```bash
./scripts/backup.sh
```

The backup set contains:

- PostgreSQL custom-format dump;
- ontology and configuration snapshot without secrets;
- CAS manifest and optional CAS archive;
- schema/version metadata.

## Restore drill

Run quarterly. Restore to a new database and CAS path, run migrations, verify row counts, rebuild disposable projections, then run golden queries.

## Projection rebuild

Rebuild lexical, vector, and graph projections independently. Never delete approved assertions to rebuild a projection.

### Local semantic shadow

```bash
./scripts/bootstrap-semantic.sh
./scripts/semantic-server.sh run
./scripts/semantic-smoke.sh
./scripts/kip projection rebuild --name semantic
./scripts/kip projection verify --name semantic
```

The isolated semantic environment pins Infinity 0.0.77 and Click 8.1.8.
Click 8.4.x is incompatible with Infinity's Typer 0.12.5 dual boolean flags.
Apple MPS also runs with BetterTransformer disabled because Infinity's optional
Optimum precheck is invalid on that path. Defaults of four embedding inputs and
two reranking pairs per server batch fit the validated 24 GB Apple Silicon
profile; adjust the `KIP_*_SERVER_BATCH_SIZE` variables only after measuring.

`projection verify` accepts a complete `shadow` or `active` space and checks
that its vector count equals current active content units. `projection
activate` refuses an incomplete space. Activation is still a separate operator
decision after `evaluate compare`; the current public pilot says to keep it
disabled.

### Periodic public scorecard

Enable the `public-government` source and both model adapters in the local
`config/kip.toml`, then run:

```bash
make fetch-corpus
./scripts/kip sync run --source public-government
make evaluate
```

Timestamped JSON and Markdown scorecards are written below
`evaluation/reports/public-government/`. `latest.*` is a convenience pointer;
`evaluation/reports/evolution.jsonl` is the append-only metrics trail. Do not
use `python -m kip.cli` directly for this workflow because it does not load
`.env`. The default scorecard performs one untimed full-dataset warmup pass per
variant, suitable for the persistent model sidecar. Pass `--warmup-passes 0`
to `evaluate run` only for a deliberate cold-start measurement.

## Parser upgrade

1. Run the new parser in shadow mode.
2. Compare quality and golden corpus output.
3. Keep the previous active extraction until the new extraction passes.
4. Activate atomically.
5. Preserve parser name, version, run ID, warnings, and output hash.
