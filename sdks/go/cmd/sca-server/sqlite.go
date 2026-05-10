// SPDX-License-Identifier: MIT

package main

import (
	"database/sql"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sca"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/storesqlite"
)

func openSQLiteStores(dsn string) (sca.SessionStore, sca.IssuanceStore, sca.RevocationStore, *sql.DB, error) {
	db, err := storesqlite.Open(dsn)
	if err != nil {
		return nil, nil, nil, nil, err
	}
	rev, err := storesqlite.NewSCARevocationStore(db, sca.DefaultListID, 0)
	if err != nil {
		_ = db.Close()
		return nil, nil, nil, nil, err
	}
	return storesqlite.NewSCASessionStore(db),
		storesqlite.NewSCAIssuanceStore(db),
		rev,
		db,
		nil
}
