// SPDX-License-Identifier: MIT

// Command issuer-server-pg is the Postgres-backed flavour of the
// Shadownet Issuer reference server (RFC 0001 §6). The command surface,
// YAML config, mode dispatch (domain ∣ keyed), hook drivers, and admin
// subcommands are identical to the SQLite-backed cmd/issuer-server in
// the parent core module — see that binary's package doc for details —
// except that storage.driver MUST be "postgres" and storage.dsn is a
// libpq URI.
//
// Storage block:
//
//	storage:
//	  driver: postgres
//	  dsn: postgres://user:pass@host:5432/db?sslmode=require&pool_max_conns=20
//	  maxIndicesPerEpoch: 131072
//
// Schema is applied at boot (idempotently) under a Postgres advisory
// lock; AllocateIndex uses SELECT ... FOR UPDATE inside a pgx.BeginFunc
// transaction so concurrent issuances can't be assigned the same
// revocation index.
package main

import (
	"context"
	"fmt"
	"os"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/cmdrun"
	"github.com/shadownet-protocol/shadownet/core/pgstore"
)

func main() {
	err := cmdrun.Main(os.Args[1:], cmdrun.Options{
		BinaryName:    "issuer-server-pg",
		StorageDriver: "postgres",
		OpenStore:     openPostgresStore,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "issuer-server-pg:", err)
		os.Exit(1)
	}
}

func openPostgresStore(ctx context.Context, dsn string, maxIndices uint64) (issuer.Store, error) {
	pool, err := pgstore.Open(ctx, dsn)
	if err != nil {
		return nil, err
	}
	store, err := pgstore.NewIssuerStore(ctx, pool, maxIndices)
	if err != nil {
		pool.Close()
		return nil, err
	}
	return &issuerStoreWithPool{IssuerStore: store, pool: pool}, nil
}

// issuerStoreWithPool wraps the pgstore.IssuerStore so that closing it
// also closes the underlying pool — cmdrun calls Close on the issuer.Store
// it gets back from OpenStore, and pgstore.IssuerStore.Close is a no-op
// because the pool ownership traditionally belonged to the caller.
type issuerStoreWithPool struct {
	*pgstore.IssuerStore
	pool interface{ Close() }
}

func (s *issuerStoreWithPool) Close() error {
	_ = s.IssuerStore.Close()
	s.pool.Close()
	return nil
}
