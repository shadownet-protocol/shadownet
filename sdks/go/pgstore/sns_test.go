// SPDX-License-Identifier: MIT

//go:build integration

package pgstore_test

import (
	"testing"

	"github.com/shadownet-protocol/shadownet/sdks/go/pgstore"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sns"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sns/storetest"
)

func TestSNSRecordStore(t *testing.T) {
	storetest.RunRecordStore(t, func(t *testing.T) sns.RecordStore {
		return pgstore.NewSNSRecordStore(freshPool(t))
	})
}
