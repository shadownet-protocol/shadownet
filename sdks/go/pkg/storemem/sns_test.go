// SPDX-License-Identifier: MIT

package storemem_test

import (
	"testing"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sns"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sns/storetest"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/storemem"
)

func TestSNSRecordStore(t *testing.T) {
	storetest.RunRecordStore(t, func(*testing.T) sns.RecordStore {
		return storemem.NewSNSRecordStore()
	})
}
