// SPDX-License-Identifier: MIT

//go:build integration

package pgstore_test

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/provider"
	"github.com/shadownet-protocol/shadownet/core/pgstore"
)

func testPool(t *testing.T) {
	t.Helper()
	if os.Getenv("SHADOWNET_TEST_PG_DSN") == "" {
		t.Skip("SHADOWNET_TEST_PG_DSN not set — skipping pgstore integration test")
	}
}

func openProviderStore(t *testing.T) *pgstore.ProviderStore {
	t.Helper()
	testPool(t)
	pool, err := pgstore.Open(context.Background(), os.Getenv("SHADOWNET_TEST_PG_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		// Truncate so subsequent tests see a clean slate.
		_, _ = pool.Exec(context.Background(), "TRUNCATE provider_records")
		pool.Close()
	})
	return pgstore.NewProviderStore(pool)
}

func TestProviderPutGet(t *testing.T) {
	s := openProviderStore(t)
	ctx := context.Background()
	rec := provider.Record{
		Local:           "alice",
		ShadowPublicKey: "z6MkAlicePub",
		A2AURL:          "https://shadow.example/v1/a2a/alice",
		DisplayName:     "Alice",
	}
	if err := s.PutRecord(ctx, rec); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetRecord(ctx, "alice")
	if err != nil {
		t.Fatal(err)
	}
	if got.ShadowPublicKey != rec.ShadowPublicKey || got.A2AURL != rec.A2AURL {
		t.Fatalf("round-trip mismatch: %+v", got)
	}
	if got.CreatedAt.IsZero() {
		t.Fatal("CreatedAt should be set")
	}
}

func TestProviderGetMissing(t *testing.T) {
	s := openProviderStore(t)
	_, err := s.GetRecord(context.Background(), "no-such-local")
	if !errors.Is(err, provider.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}

func TestProviderUpsertPreservesCreatedAt(t *testing.T) {
	s := openProviderStore(t)
	ctx := context.Background()
	rec := provider.Record{
		Local:           "bob",
		ShadowPublicKey: "z6MkBobPub",
		A2AURL:          "https://shadow.example/v1/a2a/bob",
	}
	if err := s.PutRecord(ctx, rec); err != nil {
		t.Fatal(err)
	}
	first, _ := s.GetRecord(ctx, "bob")

	time.Sleep(10 * time.Millisecond)
	rec.A2AURL = "https://shadow.example/v1/a2a/bob-new"
	if err := s.PutRecord(ctx, rec); err != nil {
		t.Fatal(err)
	}
	second, _ := s.GetRecord(ctx, "bob")
	if !second.CreatedAt.Equal(first.CreatedAt) {
		t.Fatalf("CreatedAt drift across upsert: %v -> %v", first.CreatedAt, second.CreatedAt)
	}
	if !second.UpdatedAt.After(first.UpdatedAt) {
		t.Fatalf("UpdatedAt did not advance: %v -> %v", first.UpdatedAt, second.UpdatedAt)
	}
	if second.A2AURL != "https://shadow.example/v1/a2a/bob-new" {
		t.Fatalf("A2A URL not updated: %q", second.A2AURL)
	}
}

func TestProviderListAndDelete(t *testing.T) {
	s := openProviderStore(t)
	ctx := context.Background()
	for _, l := range []string{"alice", "bob", "charlie"} {
		_ = s.PutRecord(ctx, provider.Record{
			Local:           l,
			ShadowPublicKey: "z6Mk" + l,
			A2AURL:          "https://shadow.example/v1/a2a/" + l,
		})
	}
	rows, err := s.ListRecords(ctx)
	if err != nil || len(rows) != 3 {
		t.Fatalf("ListRecords got %d rows, err=%v", len(rows), err)
	}
	if err := s.DeleteRecord(ctx, "bob"); err != nil {
		t.Fatal(err)
	}
	rows, _ = s.ListRecords(ctx)
	if len(rows) != 2 {
		t.Fatalf("after delete: %d rows", len(rows))
	}
	// Deleting a missing record is a no-op.
	if err := s.DeleteRecord(ctx, "no-such"); err != nil {
		t.Fatalf("delete missing should be no-op: %v", err)
	}
}
