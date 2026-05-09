// SPDX-License-Identifier: MIT

package storetest

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
)

// IssuanceStoreFactory returns a fresh, empty IssuanceStore per call.
type IssuanceStoreFactory func(t *testing.T) sca.IssuanceStore

// RunIssuanceStore exercises the full sca.IssuanceStore contract.
func RunIssuanceStore(t *testing.T, factory IssuanceStoreFactory) {
	t.Helper()
	t.Run("PutGetRoundtrip", func(t *testing.T) { testIssuancePutGet(t, factory(t)) })
	t.Run("GetMissingReturnsErrJTINotFound", func(t *testing.T) { testIssuanceGetMissing(t, factory(t)) })
	t.Run("PutMultipleAndGetEach", func(t *testing.T) { testIssuanceMany(t, factory(t)) })
}

func sampleIssued(jti string) sca.IssuedCredential {
	now := time.Now().UTC().Truncate(time.Second)
	return sca.IssuedCredential{
		JTI:             jti,
		Issuer:          "did:web:sca.example",
		Subject:         "did:key:zSubject",
		Level:           vc.LevelL1,
		SubjectType:     vc.SubjectPerson,
		JWT:             "eyJ.example",
		StatusListID:    "main",
		StatusListIndex: 7,
		IssuedAt:        now,
		Expires:         now.Add(48 * time.Hour),
	}
}

func testIssuancePutGet(t *testing.T, s sca.IssuanceStore) {
	ctx := context.Background()
	in := sampleIssued("urn:uuid:put-get")
	if err := s.Put(ctx, in); err != nil {
		t.Fatalf("Put: %v", err)
	}
	got, err := s.Get(ctx, in.JTI)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.JTI != in.JTI || got.JWT != in.JWT || got.StatusListIndex != in.StatusListIndex {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}
	if got.SubjectType != in.SubjectType {
		t.Fatalf("SubjectType = %q, want %q", got.SubjectType, in.SubjectType)
	}
	if !got.IssuedAt.Equal(in.IssuedAt) || !got.Expires.Equal(in.Expires) {
		t.Fatalf("timestamps not preserved: got %v / %v, want %v / %v", got.IssuedAt, got.Expires, in.IssuedAt, in.Expires)
	}
}

func testIssuanceGetMissing(t *testing.T, s sca.IssuanceStore) {
	ctx := context.Background()
	if _, err := s.Get(ctx, "urn:uuid:nope"); !errors.Is(err, sca.ErrJTINotFound) {
		t.Fatalf("got %v, want ErrJTINotFound", err)
	}
}

func testIssuanceMany(t *testing.T, s sca.IssuanceStore) {
	ctx := context.Background()
	for i := 0; i < 10; i++ {
		c := sampleIssued("urn:uuid:many-")
		c.JTI = c.JTI + string(rune('a'+i))
		c.StatusListIndex = uint64(i)
		if err := s.Put(ctx, c); err != nil {
			t.Fatalf("Put[%d]: %v", i, err)
		}
	}
	for i := 0; i < 10; i++ {
		jti := "urn:uuid:many-" + string(rune('a'+i))
		got, err := s.Get(ctx, jti)
		if err != nil {
			t.Fatalf("Get[%d]: %v", i, err)
		}
		if got.StatusListIndex != uint64(i) {
			t.Fatalf("Get[%d].StatusListIndex = %d, want %d", i, got.StatusListIndex, i)
		}
	}
}
