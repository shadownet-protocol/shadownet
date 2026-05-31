// SPDX-License-Identifier: MIT

package sqlitestore_test

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/sqlitestore"
)

func newStoreWithMax(t *testing.T, maxIndices uint64) *sqlitestore.Store {
	t.Helper()
	// Each test runs against its own temp-file SQLite database so the
	// bootstrap-epoch insert doesn't collide with sibling parallel tests
	// (a shared in-memory `cache=shared` URI would expose all tests to
	// the same row, which is the wrong isolation model for parallel runs).
	path := filepath.Join(t.TempDir(), "issuer.db")
	s, err := sqlitestore.Open("file:"+path+"?_pragma=busy_timeout=5000", maxIndices)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func newStore(t *testing.T) *sqlitestore.Store {
	return newStoreWithMax(t, 16)
}

func TestBootstrapEpoch(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	e, err := s.CurrentEpoch(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if e.Number != 1 {
		t.Fatalf("first epoch = %d, want 1", e.Number)
	}
	if e.NextIdx != 0 || !e.IsOpen() {
		t.Fatalf("first epoch should be open at idx 0: %+v", e)
	}
}

func TestAllocateIndexSequential(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	ctx := context.Background()
	exp := time.Now().Add(24 * time.Hour)
	seen := make(map[uint64]struct{})
	for i := 0; i < 16; i++ {
		ep, idx, err := s.AllocateIndex(ctx, exp)
		if err != nil {
			t.Fatalf("allocate %d: %v", i, err)
		}
		if ep != 1 {
			t.Fatalf("allocation %d: epoch = %d, want 1", i, ep)
		}
		if _, dup := seen[idx]; dup {
			t.Fatalf("duplicate idx %d on iteration %d", idx, i)
		}
		seen[idx] = struct{}{}
	}
	// Next allocation MUST roll the epoch.
	ep, idx, err := s.AllocateIndex(ctx, exp)
	if err != nil {
		t.Fatalf("allocate after exhaustion: %v", err)
	}
	if ep != 2 || idx != 0 {
		t.Fatalf("epoch did not roll: ep=%d idx=%d", ep, idx)
	}
}

func TestAllocateIndexConcurrent(t *testing.T) {
	t.Parallel()
	// Cap epoch at a large number so concurrent allocations don't roll.
	s := newStoreWithMax(t, 1024)

	ctx := context.Background()
	exp := time.Now().Add(24 * time.Hour)
	const N = 64
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
				errs <- fmt.Errorf("alloc: %w", err)
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
	if got := len(seen); got != N {
		t.Fatalf("expected %d unique idx allocations, got %d", N, got)
	}
}

func TestPutAndGetCredential(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	ctx := context.Background()
	now := time.Now()
	c := issuer.Credential{
		IdempotencyKey: "abc123",
		JWS:            "header.payload.signature",
		Iss:            "acme.example",
		Sub:            "alice@sh4dow.org",
		Org:            "acme.example",
		Epoch:          1,
		Idx:            42,
		IssuedAt:       now,
		ExpiresAt:      now.Add(24 * time.Hour),
	}
	if err := s.PutCredential(ctx, c); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetByIdempotencyKey(ctx, "abc123")
	if err != nil {
		t.Fatal(err)
	}
	if got.JWS != c.JWS {
		t.Fatalf("jws round-trip: got %q want %q", got.JWS, c.JWS)
	}
	if got.Sub != c.Sub || got.Idx != 42 {
		t.Fatalf("unexpected scan: %+v", got)
	}
}

func TestGetByIdempotencyKeyMissing(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	_, err := s.GetByIdempotencyKey(context.Background(), "no-such-key")
	if !errors.Is(err, issuer.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}

func TestPendingLifecycle(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	ctx := context.Background()
	now := time.Now()
	p := issuer.Pending{
		HandleID:       "h1",
		IdempotencyKey: "k1",
		Iss:            "alice@sh4dow.org",
		Aud:            "acme.example",
		Kind:           "org_affiliation",
		Org:            "acme.example",
		SubjectPubKey:  "z6MkAlice...",
		Status:         issuer.PendingNew,
		NextURL:        "https://verify.acme.example/start",
		CreatedAt:      now,
		UpdatedAt:      now,
		CeremonyExpiry: now.Add(24 * time.Hour),
	}
	if err := s.PutPending(ctx, p); err != nil {
		t.Fatal(err)
	}

	got, err := s.GetPending(ctx, "h1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != issuer.PendingNew {
		t.Fatalf("status: %v", got.Status)
	}

	if err := s.UpdatePendingStatus(ctx, "h1", issuer.PendingApproved, "", now.Add(time.Hour)); err != nil {
		t.Fatal(err)
	}
	updated, err := s.GetPending(ctx, "h1")
	if err != nil {
		t.Fatal(err)
	}
	if updated.Status != issuer.PendingApproved {
		t.Fatalf("status after update: %v", updated.Status)
	}
	if updated.UpdatedAt.Unix() != now.Add(time.Hour).Unix() {
		t.Fatalf("UpdatedAt not advanced: %v", updated.UpdatedAt)
	}
}

func TestListPendingFilters(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	ctx := context.Background()
	now := time.Now()
	for i := 0; i < 5; i++ {
		status := issuer.PendingNew
		if i%2 == 1 {
			status = issuer.PendingApproved
		}
		exp := now.Add(time.Hour)
		if i == 4 {
			exp = now.Add(-time.Hour) // expired
		}
		_ = s.PutPending(ctx, issuer.Pending{
			HandleID:       fmt.Sprintf("h%d", i),
			IdempotencyKey: fmt.Sprintf("k%d", i),
			Iss:            "x", Aud: "y", Kind: "org_affiliation", Org: "y",
			SubjectPubKey: "z", Status: status,
			CreatedAt:      now.Add(time.Duration(i) * time.Second),
			UpdatedAt:      now.Add(time.Duration(i) * time.Second),
			CeremonyExpiry: exp,
		})
	}
	all, err := s.ListPending(ctx, issuer.PendingFilter{})
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 4 {
		t.Fatalf("default filter should drop expired; got %d", len(all))
	}

	approved := issuer.PendingApproved
	approvedOnly, err := s.ListPending(ctx, issuer.PendingFilter{Status: &approved})
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range approvedOnly {
		if p.Status != issuer.PendingApproved {
			t.Fatalf("filter leaked status: %v", p.Status)
		}
	}

	withExpired, err := s.ListPending(ctx, issuer.PendingFilter{IncludeExpired: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(withExpired) != 5 {
		t.Fatalf("IncludeExpired should surface all 5, got %d", len(withExpired))
	}
}

func TestRotateEpoch(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	ctx := context.Background()
	current, _ := s.CurrentEpoch(ctx)
	next, err := s.RotateEpoch(ctx, 100, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if next.Number != current.Number+1 {
		t.Fatalf("new epoch number = %d, want %d", next.Number, current.Number+1)
	}
	if next.MaxIndices != 100 {
		t.Fatalf("max indices not honored: %d", next.MaxIndices)
	}
	// Old epoch is now closed.
	old, err := s.GetEpoch(ctx, current.Number)
	if err != nil {
		t.Fatal(err)
	}
	if old.IsOpen() {
		t.Fatal("old epoch should be closed after rotate")
	}
}

func TestSetRevokedAndStatusBits(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	ctx := context.Background()
	if err := s.SetRevoked(ctx, 1, 5, time.Now()); err != nil {
		t.Fatal(err)
	}
	bits, _, err := s.LoadStatusBits(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	// idx 5 = MSB-of-byte ordering: bit 7-5 = 2 of byte 0.
	if bits[0]&(1<<(7-5)) == 0 {
		t.Fatalf("idx 5 not set: %08b", bits[0])
	}
	// Idempotent: re-revoking is fine.
	if err := s.SetRevoked(ctx, 1, 5, time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
}

func TestSetRevokedOutOfRange(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	// First epoch has max_indices = 16 (the test default).
	if err := s.SetRevoked(context.Background(), 1, 999, time.Now()); !errors.Is(err, issuer.ErrInvalid) {
		t.Fatalf("expected ErrInvalid for out-of-range idx, got %v", err)
	}
}

func TestLoadStatusBitsUnknownEpoch(t *testing.T) {
	t.Parallel()
	s := newStore(t)
	_, _, err := s.LoadStatusBits(context.Background(), 999)
	if !errors.Is(err, issuer.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}
