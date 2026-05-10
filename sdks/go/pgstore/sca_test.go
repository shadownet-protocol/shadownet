// SPDX-License-Identifier: MIT

//go:build integration

package pgstore_test

import (
	"context"
	"os"
	"testing"

	"github.com/shadownet-protocol/shadownet-go/pgstore"
	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
	"github.com/shadownet-protocol/shadownet-go/pkg/sca/storetest"

	"github.com/jackc/pgx/v5/pgxpool"
)

// dsnEnv is the env var operators set to point integration tests at a
// running Postgres. CI sets it to the GitHub Actions service-container DSN;
// developers set it locally before running `go test -tags integration ./...`.
const dsnEnv = "SHADOWNET_TEST_PG_DSN"

// freshPool drops and re-applies the schema so every test gets a clean DB.
// The truncates target the tables pgstore touches; we deliberately leave
// other tables alone so this can run against a shared DB without nuking
// unrelated state.
func freshPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv(dsnEnv)
	if dsn == "" {
		t.Skipf("set %s to run pgstore integration tests", dsnEnv)
	}
	ctx := context.Background()
	pool, err := pgstore.Open(ctx, dsn)
	if err != nil {
		t.Fatalf("pgstore.Open: %v", err)
	}
	t.Cleanup(func() { pool.Close() })
	if _, err := pool.Exec(ctx, `
TRUNCATE sca_revoked, sca_status_lists, sca_credentials, sca_sessions, sns_records RESTART IDENTITY CASCADE`); err != nil {
		t.Fatalf("truncate: %v", err)
	}
	return pool
}

func TestSCASessionStore(t *testing.T) {
	storetest.RunSessionStore(t, func(t *testing.T) sca.SessionStore {
		return pgstore.NewSCASessionStore(freshPool(t))
	})
}

func TestSCAIssuanceStore(t *testing.T) {
	storetest.RunIssuanceStore(t, func(t *testing.T) sca.IssuanceStore {
		return pgstore.NewSCAIssuanceStore(freshPool(t))
	})
}

func TestSCARevocationStore(t *testing.T) {
	storetest.RunRevocationStore(t, func(t *testing.T) sca.RevocationStore {
		return pgstore.NewSCARevocationStore(freshPool(t), sca.DefaultListID, 1024)
	})
}

func TestSCARevocationStoreRotation(t *testing.T) {
	const capacity = 64
	storetest.RunRevocationStoreRotation(t, func(t *testing.T) sca.RevocationStore {
		return pgstore.NewSCARevocationStore(freshPool(t), sca.DefaultListID, capacity)
	}, capacity)
}
