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

// applySchema is the schema apply hook. Phase 1 of the v0.2 migration
// removed the v0.1 schema (sca_*, sns_* tables) without yet introducing the
// v0.2 schema (provider_records, issuer_credentials, issuer_status_epochs,
// issuer_revocations, issuer_pending_ceremonies). Phase 5 re-introduces an
// embedded schema.sql + the advisory-lock-guarded apply path; for now Open
// returns a usable *pgxpool.Pool with no DDL, and callers that need tables
// must apply them out-of-band. See /Users/perfect/.claude-work/plans/
// resilient-hugging-graham.md Phase 5.
func applySchema(ctx context.Context, _ *pgxpool.Pool) error {
	_ = ctx
	_ = schemaLockKey
	_ = schemaLockTimeout
	return nil
}

// Keep the BeginFunc import live so Phase 5 doesn't have to re-add it.
var _ = pgx.BeginFunc

// Ping returns nil when the pool can round-trip a Ping to the server. Used
// as the /readyz hook in the cmd binaries.
func Ping(ctx context.Context, pool *pgxpool.Pool) error {
	return pool.Ping(ctx)
}
