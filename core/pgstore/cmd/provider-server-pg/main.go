// SPDX-License-Identifier: MIT

// Command provider-server-pg is the Postgres-backed flavour of the
// Shadownet Provider reference server (RFC 0001 §5.2). The command
// surface, YAML config, and admin subcommands are identical to the
// SQLite-backed cmd/provider-server in the parent core module — see
// that binary's package doc for details — except that storage.driver
// MUST be "postgres" and storage.dsn is a libpq URI.
//
// Storage block:
//
//	storage:
//	  driver: postgres
//	  dsn: postgres://user:pass@host:5432/db?sslmode=require&pool_max_conns=20
//
// Schema is applied at boot (idempotently) under a Postgres advisory lock
// so multiple replica starts against the same database serialize on DDL
// rather than racing on the system-catalog inserts.
package main

import (
	"context"
	"fmt"
	"io"
	"os"

	"github.com/shadownet-protocol/shadownet/core/internal/provider"
	"github.com/shadownet-protocol/shadownet/core/internal/provider/cmdrun"
	"github.com/shadownet-protocol/shadownet/core/pgstore"
)

func main() {
	err := cmdrun.Main(os.Args[1:], cmdrun.Options{
		BinaryName:    "provider-server-pg",
		StorageDriver: "postgres",
		OpenStore:     openPostgresStore,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "provider-server-pg:", err)
		os.Exit(1)
	}
}

func openPostgresStore(ctx context.Context, dsn string) (provider.Store, io.Closer, error) {
	pool, err := pgstore.Open(ctx, dsn)
	if err != nil {
		return nil, nil, err
	}
	store := pgstore.NewProviderStore(pool)
	return store, poolCloser{pool: pool}, nil
}

// poolCloser adapts *pgxpool.Pool to io.Closer so cmdrun's defer can fire it.
type poolCloser struct {
	pool interface{ Close() }
}

func (c poolCloser) Close() error {
	c.pool.Close()
	return nil
}
