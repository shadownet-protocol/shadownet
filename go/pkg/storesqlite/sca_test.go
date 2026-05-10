// SPDX-License-Identifier: MIT

package storesqlite_test

import (
	"database/sql"
	"testing"

	"github.com/shadownet-protocol/shadownet/go/pkg/sca"
	"github.com/shadownet-protocol/shadownet/go/pkg/sca/storetest"
	"github.com/shadownet-protocol/shadownet/go/pkg/storesqlite"
)

// Each test gets its own :memory: database; sqlite's :memory: form is
// per-connection so a fresh sql.DB == a fresh schema.

func TestSCASessionStore(t *testing.T) {
	storetest.RunSessionStore(t, func(t *testing.T) sca.SessionStore {
		return storesqlite.NewSCASessionStore(openTestDB(t))
	})
}

func TestSCAIssuanceStore(t *testing.T) {
	storetest.RunIssuanceStore(t, func(t *testing.T) sca.IssuanceStore {
		return storesqlite.NewSCAIssuanceStore(openTestDB(t))
	})
}

func TestSCARevocationStore(t *testing.T) {
	storetest.RunRevocationStore(t, func(t *testing.T) sca.RevocationStore {
		rev, err := storesqlite.NewSCARevocationStore(openTestDB(t), sca.DefaultListID, 1024)
		if err != nil {
			t.Fatalf("NewSCARevocationStore: %v", err)
		}
		return rev
	})
}

func TestSCARevocationStoreRotation(t *testing.T) {
	const capacity = 64
	storetest.RunRevocationStoreRotation(t, func(t *testing.T) sca.RevocationStore {
		rev, err := storesqlite.NewSCARevocationStore(openTestDB(t), sca.DefaultListID, capacity)
		if err != nil {
			t.Fatalf("NewSCARevocationStore: %v", err)
		}
		return rev
	}, capacity)
}

func openTestDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := storesqlite.Open(":memory:")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })
	return db
}
