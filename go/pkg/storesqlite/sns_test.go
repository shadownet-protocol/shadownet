// SPDX-License-Identifier: MIT

package storesqlite_test

import (
	"database/sql"
	"testing"

	"github.com/shadownet-protocol/shadownet/go/pkg/sns"
	"github.com/shadownet-protocol/shadownet/go/pkg/sns/storetest"
	"github.com/shadownet-protocol/shadownet/go/pkg/storesqlite"
)

func TestSNSRecordStore(t *testing.T) {
	storetest.RunRecordStore(t, func(t *testing.T) sns.RecordStore {
		return storesqlite.NewSNSRecordStore(openSNSTestDB(t))
	})
}

func openSNSTestDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := storesqlite.OpenSNS(":memory:")
	if err != nil {
		t.Fatalf("OpenSNS: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })
	return db
}
