// SPDX-License-Identifier: MIT

//go:build integration

package pgstore_test

import (
	"context"
	"os"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/shadownet-protocol/shadownet-go/pgstore"
)

// TestOpenConcurrentSchemaApply exercises the same-database co-tenant race:
// two pgstore.Open calls firing the same DDL block at the same time used to
// fail with `duplicate key value violates unique constraint
// "pg_type_typname_nsp_index"` because Postgres CREATE TABLE IF NOT EXISTS
// is not a synchronization primitive. Open now wraps the schema apply in
// a pg_advisory_xact_lock-guarded transaction; this test asserts that N
// concurrent Open calls all succeed against the same DSN.
func TestOpenConcurrentSchemaApply(t *testing.T) {
	dsn := os.Getenv(dsnEnv)
	if dsn == "" {
		t.Skipf("set %s to run pgstore integration tests", dsnEnv)
	}

	// Drop any pre-existing pgstore tables so every goroutine actually
	// races on CREATE, not just the IF-NOT-EXISTS no-op fast path.
	bootstrap, err := pgstore.Open(context.Background(), dsn)
	if err != nil {
		t.Fatalf("bootstrap Open: %v", err)
	}
	for _, table := range []string{
		"sca_revoked", "sca_status_lists", "sca_credentials",
		"sca_sessions", "sns_records",
	} {
		if _, err := bootstrap.Exec(context.Background(),
			"DROP TABLE IF EXISTS "+table+" CASCADE"); err != nil {
			t.Fatalf("drop %s: %v", table, err)
		}
	}
	bootstrap.Close()

	const N = 8
	var (
		wg    sync.WaitGroup
		errs  atomic.Int32
		start = make(chan struct{})
	)
	wg.Add(N)
	for i := 0; i < N; i++ {
		go func() {
			defer wg.Done()
			<-start
			pool, err := pgstore.Open(context.Background(), dsn)
			if err != nil {
				errs.Add(1)
				t.Errorf("concurrent Open: %v", err)
				return
			}
			pool.Close()
		}()
	}
	close(start)
	wg.Wait()
	if errs.Load() > 0 {
		t.Fatalf("%d/%d concurrent Open calls failed", errs.Load(), N)
	}

	// Sanity-check the schema is intact and usable after the race.
	pool, err := pgstore.Open(context.Background(), dsn)
	if err != nil {
		t.Fatalf("post-race Open: %v", err)
	}
	defer pool.Close()
	for _, table := range []string{
		"sca_sessions", "sca_credentials", "sca_status_lists",
		"sca_revoked", "sns_records",
	} {
		var n int
		if err := pool.QueryRow(context.Background(),
			"SELECT COUNT(*) FROM "+table).Scan(&n); err != nil {
			t.Errorf("%s not queryable after race: %v", table, err)
		}
	}
}
