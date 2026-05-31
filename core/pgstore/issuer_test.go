// SPDX-License-Identifier: MIT

//go:build integration

package pgstore_test

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
	"github.com/shadownet-protocol/shadownet/core/pgstore"
)

func openIssuerStoreWithMax(t *testing.T, maxIndices uint64) *pgstore.IssuerStore {
	t.Helper()
	if os.Getenv("SHADOWNET_TEST_PG_DSN") == "" {
		t.Skip("SHADOWNET_TEST_PG_DSN not set — skipping pgstore integration test")
	}
	pool, err := pgstore.Open(context.Background(), os.Getenv("SHADOWNET_TEST_PG_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	// Wipe issuer state before each test so AllocateIndex sees a fresh epoch.
	if _, err := pool.Exec(context.Background(),
		`TRUNCATE issuer_credentials, issuer_pendings, issuer_revocations, issuer_epochs`); err != nil {
		t.Fatal(err)
	}
	s, err := pgstore.NewIssuerStore(context.Background(), pool, maxIndices)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		pool.Close()
	})
	return s
}

func openIssuerStore(t *testing.T) *pgstore.IssuerStore {
	return openIssuerStoreWithMax(t, 16)
}

func TestIssuerBootstrapEpoch(t *testing.T) {
	s := openIssuerStore(t)
	e, err := s.CurrentEpoch(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if e.Number != 1 || !e.IsOpen() || e.NextIdx != 0 {
		t.Fatalf("bootstrap epoch unexpected: %+v", e)
	}
}

func TestIssuerCredentialRoundtrip(t *testing.T) {
	s := openIssuerStore(t)
	ctx := context.Background()
	now := time.Now()
	c := issuer.Credential{
		IdempotencyKey: "abc123",
		JWS:            "h.p.s",
		Iss:            "acme.example",
		Sub:            "alice@sh4dow.org",
		Org:            "acme.example",
		Epoch:          1,
		Idx:            7,
		IssuedAt:       now,
		ExpiresAt:      now.Add(time.Hour),
	}
	if err := s.PutCredential(ctx, c); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetByIdempotencyKey(ctx, "abc123")
	if err != nil {
		t.Fatal(err)
	}
	if got.JWS != c.JWS || got.Idx != 7 {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}
}

func TestIssuerGetByIdempotencyMissing(t *testing.T) {
	s := openIssuerStore(t)
	_, err := s.GetByIdempotencyKey(context.Background(), "no-such-key")
	if !errors.Is(err, issuer.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}

func TestIssuerAllocateIndexSequential(t *testing.T) {
	s := openIssuerStore(t)
	ctx := context.Background()
	exp := time.Now().Add(time.Hour)
	seen := make(map[uint64]struct{})
	for i := 0; i < 16; i++ {
		ep, idx, err := s.AllocateIndex(ctx, exp)
		if err != nil {
			t.Fatalf("alloc %d: %v", i, err)
		}
		if ep != 1 {
			t.Fatalf("alloc %d: epoch=%d", i, ep)
		}
		if _, dup := seen[idx]; dup {
			t.Fatalf("duplicate idx %d", idx)
		}
		seen[idx] = struct{}{}
	}
	// Next allocation MUST roll the epoch.
	ep, idx, err := s.AllocateIndex(ctx, exp)
	if err != nil {
		t.Fatalf("alloc after full: %v", err)
	}
	if ep != 2 || idx != 0 {
		t.Fatalf("epoch did not roll: ep=%d idx=%d", ep, idx)
	}
}

func TestIssuerAllocateIndexConcurrent(t *testing.T) {
	s := openIssuerStoreWithMax(t, 1024)
	ctx := context.Background()
	exp := time.Now().Add(time.Hour)
	const N = 32
	var (
		mu   sync.Mutex
		seen = make(map[uint64]struct{})
	)
	var wg sync.WaitGroup
	errs := make(chan error, N)
	for i := 0; i < N; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ep, idx, err := s.AllocateIndex(ctx, exp)
			if err != nil {
				errs <- err
				return
			}
			if ep != 1 {
				errs <- fmt.Errorf("unexpected epoch %d", ep)
				return
			}
			mu.Lock()
			defer mu.Unlock()
			if _, dup := seen[idx]; dup {
				errs <- fmt.Errorf("duplicate idx %d", idx)
				return
			}
			seen[idx] = struct{}{}
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
	if len(seen) != N {
		t.Fatalf("expected %d unique allocations, got %d", N, len(seen))
	}
}

func TestIssuerPendingLifecycle(t *testing.T) {
	s := openIssuerStore(t)
	ctx := context.Background()
	now := time.Now()
	p := issuer.Pending{
		HandleID:       "h1",
		IdempotencyKey: "k1",
		Iss:            "alice@sh4dow.org",
		Aud:            "acme.example",
		Kind:           "org_affiliation",
		Org:            "acme.example",
		SubjectPubKey:  "z6MkAlice",
		Status:         issuer.PendingNew,
		NextURL:        "https://verify.acme.example/start",
		CreatedAt:      now,
		UpdatedAt:      now,
		CeremonyExpiry: now.Add(time.Hour),
	}
	if err := s.PutPending(ctx, p); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetPending(ctx, "h1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != issuer.PendingNew {
		t.Fatalf("status = %v", got.Status)
	}
	if err := s.UpdatePendingStatus(ctx, "h1", issuer.PendingApproved, "", time.Now()); err != nil {
		t.Fatal(err)
	}
	got, _ = s.GetPending(ctx, "h1")
	if got.Status != issuer.PendingApproved {
		t.Fatalf("status after update = %v", got.Status)
	}
}

func TestIssuerStatusBits(t *testing.T) {
	s := openIssuerStore(t)
	ctx := context.Background()
	if err := s.SetRevoked(ctx, 1, 5, time.Now()); err != nil {
		t.Fatal(err)
	}
	bits, _, err := s.LoadStatusBits(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if bits[0]&(1<<(7-5)) == 0 {
		t.Fatalf("idx 5 not set: %08b", bits[0])
	}
	// Idempotent re-revoke.
	if err := s.SetRevoked(ctx, 1, 5, time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
}

func TestIssuerRevokeOutOfRange(t *testing.T) {
	s := openIssuerStore(t)
	err := s.SetRevoked(context.Background(), 1, 999, time.Now())
	if !errors.Is(err, issuer.ErrInvalid) {
		t.Fatalf("expected ErrInvalid for OOR idx, got %v", err)
	}
}

func TestIssuerLoadStatusBitsMissingEpoch(t *testing.T) {
	s := openIssuerStore(t)
	_, _, err := s.LoadStatusBits(context.Background(), 999)
	if !errors.Is(err, issuer.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}

func TestIssuerRotateEpoch(t *testing.T) {
	s := openIssuerStore(t)
	ctx := context.Background()
	cur, _ := s.CurrentEpoch(ctx)
	next, err := s.RotateEpoch(ctx, 100, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if next.Number != cur.Number+1 || next.MaxIndices != 100 {
		t.Fatalf("rotate produced %+v", next)
	}
	old, _ := s.GetEpoch(ctx, cur.Number)
	if old.IsOpen() {
		t.Fatal("old epoch should be closed after rotate")
	}
}
