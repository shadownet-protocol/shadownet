// SPDX-License-Identifier: MIT

package pgstore

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Open connects to dsn, applies the schema, and returns a configured pool.
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
	if _, err := pool.Exec(ctx, schemaSQL); err != nil {
		pool.Close()
		return nil, fmt.Errorf("pgstore: apply schema: %w", err)
	}
	return pool, nil
}

// Ping returns nil when the pool can round-trip a Ping to the server. Used
// as the /readyz hook in the cmd binaries.
func Ping(ctx context.Context, pool *pgxpool.Pool) error {
	return pool.Ping(ctx)
}
