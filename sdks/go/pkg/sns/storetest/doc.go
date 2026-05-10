// SPDX-License-Identifier: MIT

// Package storetest provides a reusable contract test suite for
// sns.RecordStore.
//
// Operators wiring a custom backend point a Run* helper at their factory:
//
//	func TestMyStore(t *testing.T) {
//	    storetest.RunRecordStore(t, func(t *testing.T) sns.RecordStore {
//	        return mystore.New(setupDB(t))
//	    })
//	}
//
// Passing the suite is the protocol-conformance bar.
package storetest
