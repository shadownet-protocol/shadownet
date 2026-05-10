// SPDX-License-Identifier: MIT

// Package storetest provides a reusable contract test suite for the three
// SCA Store interfaces (sca.SessionStore, sca.IssuanceStore,
// sca.RevocationStore).
//
// It is the canonical compliance gate for any operator-supplied backend.
// Callers wire their store factory in:
//
//	func TestMyStore(t *testing.T) {
//	    storetest.RunSessionStore(t, func() sca.SessionStore {
//	        return mystore.NewSessions(setupDB(t))
//	    })
//	}
//
// This is the same pattern as `database/sql/driver`'s test suite. Each Run*
// helper exercises every state transition, error code, and concurrency edge
// the protocol requires; passing the suite is the compliance bar.
package storetest
