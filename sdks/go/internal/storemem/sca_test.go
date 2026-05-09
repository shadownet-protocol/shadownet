// SPDX-License-Identifier: MIT

package storemem_test

import (
	"testing"

	"github.com/shadownet-protocol/shadownet-go/internal/storemem"
	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
	"github.com/shadownet-protocol/shadownet-go/pkg/sca/storetest"
)

func TestSCASessionStore(t *testing.T) {
	storetest.RunSessionStore(t, func(*testing.T) sca.SessionStore {
		return storemem.NewSCASessionStore()
	})
}

func TestSCAIssuanceStore(t *testing.T) {
	storetest.RunIssuanceStore(t, func(*testing.T) sca.IssuanceStore {
		return storemem.NewSCAIssuanceStore()
	})
}

func TestSCARevocationStore(t *testing.T) {
	storetest.RunRevocationStore(t, func(*testing.T) sca.RevocationStore {
		return storemem.NewSCARevocationStore(sca.DefaultListID, storemem.WithListSize(1024))
	})
}

func TestSCARevocationStoreRotation(t *testing.T) {
	const capacity = 64
	storetest.RunRevocationStoreRotation(t, func(*testing.T) sca.RevocationStore {
		return storemem.NewSCARevocationStore(sca.DefaultListID, storemem.WithListSize(capacity))
	}, capacity)
}
