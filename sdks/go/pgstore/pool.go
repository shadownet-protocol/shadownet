// SPDX-License-Identifier: MIT

package pgstore

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// schemaLockKey is the fixed int64 used as the pg_advisory_xact_lock key
// during schema apply. Two backends opening the same database concurrently
// serialize on this lock so their CREATE TABLE IF NOT EXISTS blocks don't
// race on pg_type/pg_class system-catalog inserts. The value is the ASCII
// bytes "SNPTRGST" packed big-endian.
const schemaLockKey int64 = 0x534E_5054_5247_5354

// schemaLockTimeout bounds how long Open waits for a peer's schema apply
// to finish. 30s comfortably covers cold-start DDL on a healthy Postgres;
// longer points at an upstream wedge worth surfacing as an error.
const schemaLockTimeout = 30 * time.Second

// Open connects to dsn, applies the schema (under an advisory lock so
// concurrent boots against the same database don't race), and returns a
// configured pool.
//
// The DSN is the standard libpq form, e.g.
//
//	postgres://user:pass@host:5432/db?sslmode=require&pool_max_conns=20
//
// Caller is responsible for Close() on the returned *pgxpool.Pool. A typical
// binary defers it after handing the pool to the SCA/SNS store constructors.
func Open(ctx context.Context, dsn string) (*pgxpool.Pool, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("pgstore: parse DSN: %w", err)
	}
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("pgstore: pool: %w", err)
	}
	if err := applySchema(ctx, pool); err != nil {
		pool.Close()
		return nil, err
	}
	return pool, nil
}

// applySchema runs schemaSQL inside a transaction guarded by a session-level
// advisory lock. Postgres CREATE TABLE IF NOT EXISTS is not a synchronization
// primitive — concurrent callers can both pass the existence check and one
// will fail on pg_type_typname_nsp_index. The advisory lock serializes the
// DDL across backends; pg_advisory_xact_lock releases automatically at
// COMMIT/ROLLBACK so there is no leak risk.
func applySchema(ctx context.Context, pool *pgxpool.Pool) error {
	ctx, cancel := context.WithTimeout(ctx, schemaLockTimeout)
	defer cancel()
	return pgx.BeginFunc(ctx, pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", schemaLockKey); err != nil {
			return fmt.Errorf("pgstore: acquire schema lock: %w", err)
		}
		if _, err := tx.Exec(ctx, schemaSQL); err != nil {
			return fmt.Errorf("pgstore: apply schema: %w", err)
		}
		return nil
	})
}

// Ping returns nil when the pool can round-trip a Ping to the server. Used
// as the /readyz hook in the cmd binaries.
func Ping(ctx context.Context, pool *pgxpool.Pool) error {
	return pool.Ping(ctx)
}
