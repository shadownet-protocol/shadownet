# Shadownet — Go SDK / pgstore

Postgres-backed `Store` implementations for the Shadownet reference SCA and SNS servers, plus the `-pg` binary variants that ship them.

This is a **separate Go module** from the parent SDK at [`../`](../). Importing `pkg/sca`, `pkg/sns`, or anything else from the parent does **not** pull `github.com/jackc/pgx/v5` into your dependency graph; only deployments that need Postgres add this submodule explicitly.

## Use cases

- The canonical hosted Shadownet cloud, where Postgres is already part of the stack.
- Self-hosters who outgrow SQLite and want managed Postgres (RDS, Cloud SQL, Aurora, etc.).
- Any deployment that needs HA via streaming replication or read replicas.

For everything else, the default `cmd/{sca,sns}-server` binaries with their built-in memory + sqlite drivers are simpler.

## Install

As a library (operators wiring their own binary):

```sh
go get github.com/shadownet-protocol/shadownet/core/pgstore
```

As container images (operators using the reference binaries):

```sh
docker pull ghcr.io/shadownet-protocol/sca-server-pg:latest
docker pull ghcr.io/shadownet-protocol/sns-server-pg:latest
```

The `-pg` images accept the same YAML config + `SHADOWNET_*` env vars as the default `:sca-server` / `:sns-server` images, plus `storage.driver: postgres` with a libpq DSN in `storage.dsn`.

## Schema

Applied automatically on `pgstore.Open` via `CREATE TABLE IF NOT EXISTS`. The full DDL is in [`schema.sql`](./schema.sql); reproduced here for reference:

- `sca_sessions` — proof-session state machine.
- `sca_credentials` — per-jti issuance log + status-list pointer.
- `sca_status_lists` — one row per status-list shard. Rotation appends a row when the active shard fills.
- `sca_revoked` — sparse `(list_id, idx)` pairs; `Snapshot` reconstructs the bitstring.
- `sns_records` — per-local SNS record + tombstone column.

The schema is intentionally shared between the SCA and SNS table sets; both servers can run against the same Postgres instance and database.

## Concurrency model

- **`AssignIndex`** uses an atomic `UPDATE … RETURNING` with `FOR UPDATE SKIP LOCKED` to claim the next index in the active shard. When the shard fills, the same transaction inserts a fresh shard. Two concurrent issuance flows never share an index and never block each other on the active shard.
- **`Revoke`** is `INSERT … ON CONFLICT DO NOTHING` — idempotent, no fetch-modify-write race.
- **`Snapshot`** reconstructs the bitstring from `sca_revoked` rows. No BLOB to clobber.
- **Session state transitions** (`MarkReady`, `Consume`, `Fail`) are `UPDATE … WHERE id = $1 AND state = $2`; the rows-affected count distinguishes "not found" from "wrong state."

## Running locally

```sh
docker run --rm -d --name shadownet-pg \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=shadownet -p 5432:5432 \
  postgres:16-alpine

export SHADOWNET_TEST_PG_DSN='postgres://postgres:postgres@localhost:5432/shadownet?sslmode=disable'
go test -tags integration -race -count=1 ./...
```

The integration tests use the same `pkg/sca/storetest` and `pkg/sns/storetest` contract suites that exercise `pkg/storemem` and `pkg/storesqlite` — three implementations validated by one source of truth.

## What pgstore does NOT include

- **Schema migration tooling.** v0.1.x has one schema; future versions add a migrations table.
- **Connection pool tuning beyond pgx defaults.** Pass tuning knobs in the DSN (`pool_max_conns=N`, etc.) — pgx parses them.
- **Sharding across multiple Postgres clusters.** Operators that need this implement it at the application layer or by deploying multiple SCA instances.
- **MySQL, CockroachDB, etc.** Other backends follow the same pattern (separate module implementing `pkg/sca` and `pkg/sns` Store interfaces, validated by the storetest suite).

## License

MIT. See [`../LICENSE`](../LICENSE).
