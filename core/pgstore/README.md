# Shadownet — core/pgstore

Postgres-backed `Store` implementations for the Shadownet reference Provider and Issuer servers, plus the `-pg` cmd binary variants that ship them.

This is a **separate Go module** from the parent core module at [`../`](../). Importing `internal/provider`, `internal/issuer`, or anything else from the parent does **not** pull `github.com/jackc/pgx/v5` into your dependency graph; only deployments that need Postgres add this submodule explicitly.

## Use cases

- The canonical hosted Shadownet cloud, where Postgres is already part of the stack.
- Self-hosters who outgrow SQLite and want managed Postgres (RDS, Cloud SQL, Aurora, etc.).
- Any deployment that needs HA via streaming replication or read replicas.

For everything else, the default `cmd/provider-server` and `cmd/issuer-server` binaries with the built-in SQLite driver are simpler.

## Install

As container images (operators using the reference binaries):

```sh
docker pull ghcr.io/shadownet-protocol/provider-server-pg:latest
docker pull ghcr.io/shadownet-protocol/issuer-server-pg:latest
```

The `-pg` images accept the same YAML config as the default `provider-server` / `issuer-server` images, except `storage.driver` MUST be `postgres` and `storage.dsn` is a libpq URI (e.g. `postgres://user:pass@host:5432/db?sslmode=require`).

## Schema

Applied automatically on `pgstore.Open` under a `pg_advisory_xact_lock` so concurrent boots against the same database serialize on DDL rather than racing on the system catalog. The full DDL is in [`schema.sql`](./schema.sql) and covers:

- `provider_records` — per-Shadowname registration (local, shadow public key, A2A URL, display name).
- `issuer_credentials` — issued `shadownet-cred+jwt` log keyed by idempotency key.
- `issuer_pendings` — paused CSR ceremonies awaiting operator decision.
- `issuer_epochs` — open + closed status epochs, with `next_idx` and `max_indices` for allocation.
- `issuer_revocations` — sparse `(epoch, idx)` pairs; `LoadStatusBits` reconstructs the bitstring on read.

Provider and Issuer can share one Postgres database; the table sets do not overlap.

## Concurrency model

- **`AllocateIndex`** does `SELECT ... FOR UPDATE` on the active epoch row inside a `pgx.BeginFunc` transaction, then `UPDATE ... SET next_idx = next_idx + 1`. Two concurrent CSRs are serialized at the row level and never collide on an index; when the active epoch hits `max_indices`, the same transaction closes it and opens the next.
- **`SetRevoked`** is `INSERT ... ON CONFLICT DO NOTHING` — idempotent, no fetch-modify-write race.
- **`LoadStatusBits`** reconstructs the bitstring from `issuer_revocations` rows. No BLOB to clobber.
- **Pending lifecycle transitions** (`PutPending`, `UpdatePendingStatus`) are constrained-update statements; the rows-affected count distinguishes "not found" from "no-op".

## Running locally

```sh
docker run --rm -d --name shadownet-pg \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=shadownet -p 5432:5432 \
  postgres:16-alpine

export SHADOWNET_TEST_PG_DSN='postgres://postgres:postgres@localhost:5432/shadownet?sslmode=disable'
go test -tags integration -race -count=1 ./...
```

The integration tests skip silently when `SHADOWNET_TEST_PG_DSN` is unset, so the same `go test ./...` invocation that runs as part of CI on PRs (without a Postgres) and as part of release verification (with one) is the right thing in both contexts.

## What pgstore does NOT include

- **Schema migration tooling.** v0.3.x has one schema; future versions add a migrations table.
- **Connection pool tuning beyond pgx defaults.** Pass tuning knobs in the DSN (`pool_max_conns=N`, etc.) — pgx parses them.
- **Sharding across multiple Postgres clusters.** Operators that need this implement it at the application layer or by deploying multiple Issuer instances.
- **Backends other than Postgres.** Implementations for other RDBMSes follow the same pattern (separate module satisfying `internal/provider.Store` and `internal/issuer.Store`).

## License

MIT. See [`../LICENSE`](../LICENSE).
