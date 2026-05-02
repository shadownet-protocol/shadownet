// SPDX-License-Identifier: MIT

package main

import (
	"github.com/shadownet-protocol/shadownet-go/internal/storesqlite"
	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
)

func openSQLiteStores(dsn string) (sca.SessionStore, sca.IssuanceStore, sca.RevocationStore, error) {
	db, err := storesqlite.Open(dsn)
	if err != nil {
		return nil, nil, nil, err
	}
	rev, err := storesqlite.NewSCARevocationStore(db, sca.DefaultListID, 0)
	if err != nil {
		return nil, nil, nil, err
	}
	return storesqlite.NewSCASessionStore(db),
		storesqlite.NewSCAIssuanceStore(db),
		rev,
		nil
}
