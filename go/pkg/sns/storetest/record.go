// SPDX-License-Identifier: MIT

package storetest

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/go/pkg/sns"
	"github.com/shadownet-protocol/shadownet/go/pkg/vc"
)

// RecordStoreFactory returns a fresh, empty RecordStore per call.
type RecordStoreFactory func(t *testing.T) sns.RecordStore

// RunRecordStore exercises the full sns.RecordStore contract.
func RunRecordStore(t *testing.T, factory RecordStoreFactory) {
	t.Helper()
	t.Run("PutGetRoundtrip", func(t *testing.T) { testRecordPutGet(t, factory(t)) })
	t.Run("GetMissingReturnsErrRecordNotFound", func(t *testing.T) { testRecordMissing(t, factory(t)) })
	t.Run("DeleteCreatesTombstone", func(t *testing.T) { testRecordTombstone(t, factory(t)) })
	t.Run("PutAfterDeleteClearsTombstone", func(t *testing.T) { testRecordResurrect(t, factory(t)) })
	t.Run("LookupCaseInsensitiveOnLocal", func(t *testing.T) { testRecordCaseInsensitive(t, factory(t)) })
	t.Run("PutOverwritesExisting", func(t *testing.T) { testRecordOverwrite(t, factory(t)) })
}

func sampleRecord(local string) sns.Record {
	jwk := crypto.JWK{Kty: "OKP", Crv: "Ed25519", X: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}
	return sns.Record{
		Shadowname:  local + "@example.org",
		DID:         "did:key:zSubject",
		Endpoint:    "https://shadow.example.org/u/" + local + "/a2a",
		PublicKey:   jwk,
		SubjectType: vc.SubjectPerson,
		TTL:         300,
		IssuedAt:    time.Now().UTC().Truncate(time.Second),
	}
}

func testRecordPutGet(t *testing.T, s sns.RecordStore) {
	ctx := context.Background()
	in := sampleRecord("alice")
	if err := s.Put(ctx, in); err != nil {
		t.Fatalf("Put: %v", err)
	}
	got, err := s.Get(ctx, "alice")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.DID != in.DID || got.Endpoint != in.Endpoint || got.TTL != in.TTL {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}
}

func testRecordMissing(t *testing.T, s sns.RecordStore) {
	ctx := context.Background()
	if _, err := s.Get(ctx, "ghost"); !errors.Is(err, sns.ErrRecordNotFound) {
		t.Fatalf("got %v, want ErrRecordNotFound", err)
	}
}

func testRecordTombstone(t *testing.T, s sns.RecordStore) {
	ctx := context.Background()
	in := sampleRecord("bob")
	_ = s.Put(ctx, in)
	if err := s.Delete(ctx, "bob"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, err := s.Get(ctx, "bob"); !errors.Is(err, sns.ErrRecordTombstoned) {
		t.Fatalf("got %v, want ErrRecordTombstoned", err)
	}
}

func testRecordResurrect(t *testing.T, s sns.RecordStore) {
	ctx := context.Background()
	in := sampleRecord("carol")
	_ = s.Put(ctx, in)
	_ = s.Delete(ctx, "carol")
	// After tombstone, Put with the same local should reactivate the record.
	if err := s.Put(ctx, in); err != nil {
		t.Fatalf("re-Put after delete: %v", err)
	}
	got, err := s.Get(ctx, "carol")
	if err != nil {
		t.Fatalf("Get post-resurrect: %v", err)
	}
	if got.DID != in.DID {
		t.Fatalf("resurrected record DID = %q, want %q", got.DID, in.DID)
	}
}

func testRecordCaseInsensitive(t *testing.T, s sns.RecordStore) {
	ctx := context.Background()
	in := sampleRecord("dave")
	_ = s.Put(ctx, in)
	for _, q := range []string{"dave", "DAVE", "DaVe", "dAvE"} {
		got, err := s.Get(ctx, q)
		if err != nil {
			t.Fatalf("Get(%q): %v", q, err)
		}
		if got.DID != in.DID {
			t.Fatalf("Get(%q) DID = %q, want %q", q, got.DID, in.DID)
		}
	}
}

func testRecordOverwrite(t *testing.T, s sns.RecordStore) {
	ctx := context.Background()
	first := sampleRecord("eve")
	_ = s.Put(ctx, first)
	second := first
	second.Endpoint = "https://shadow.example.org/u/eve/v2/a2a"
	if err := s.Put(ctx, second); err != nil {
		t.Fatalf("Put (overwrite): %v", err)
	}
	got, _ := s.Get(ctx, "eve")
	if got.Endpoint != second.Endpoint {
		t.Fatalf("overwrite didn't take: endpoint = %q, want %q", got.Endpoint, second.Endpoint)
	}
}
