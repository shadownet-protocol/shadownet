// SPDX-License-Identifier: MIT

package storetest

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
)

// RevocationStoreFactory returns a fresh, empty RevocationStore per call.
type RevocationStoreFactory func(t *testing.T) sca.RevocationStore

// RunRevocationStore exercises the basic sca.RevocationStore contract:
// monotonic AssignIndex, Revoke flips one bit, Snapshot reflects state,
// concurrent assignments hand out unique indices.
//
// Rotation behaviour (allocating a new listID when one fills) is exercised
// separately via RunRevocationStoreRotation, which is only meaningful for
// stores that implement rotation. See storetest.RunRevocationStoreRotation.
func RunRevocationStore(t *testing.T, factory RevocationStoreFactory) {
	t.Helper()
	t.Run("AssignIndexMonotonic", func(t *testing.T) { testRevAssignMonotonic(t, factory(t)) })
	t.Run("RevokeFlipsExactlyOneBit", func(t *testing.T) { testRevSingleBit(t, factory(t)) })
	t.Run("SnapshotReflectsRevocations", func(t *testing.T) { testRevSnapshot(t, factory(t)) })
	t.Run("ConcurrentAssignAreUnique", func(t *testing.T) { testRevConcurrent(t, factory(t)) })
	t.Run("RevokeIsIdempotent", func(t *testing.T) { testRevIdempotent(t, factory(t)) })
}

// RunRevocationStoreRotation exercises rotation behaviour: when AssignIndex
// would exceed the active list's capacity, the store allocates a fresh
// listID and assigns there. capacity is the per-list size the store was
// configured with; the test issues capacity+5 indices and asserts the last
// 5 land on a new listID.
//
// Stores that don't support rotation MUST NOT call this helper.
func RunRevocationStoreRotation(t *testing.T, factory RevocationStoreFactory, capacity uint64) {
	t.Helper()
	if capacity == 0 || capacity > 4096 {
		t.Fatalf("storetest: rotation test requires 1 ≤ capacity ≤ 4096, got %d", capacity)
	}
	s := factory(t)
	ctx := context.Background()

	firstID, _, err := s.AssignIndex(ctx)
	if err != nil {
		t.Fatalf("first AssignIndex: %v", err)
	}
	for i := uint64(1); i < capacity; i++ {
		if _, _, err := s.AssignIndex(ctx); err != nil {
			t.Fatalf("AssignIndex[%d]: %v", i, err)
		}
	}
	// At this point the active list is full. The next call MUST roll over.
	rotID, rotIdx, err := s.AssignIndex(ctx)
	if err != nil {
		t.Fatalf("post-capacity AssignIndex: %v (want rotation, not error)", err)
	}
	if rotID == firstID {
		t.Fatalf("rotation expected: still on listID %q after %d assignments", rotID, capacity)
	}
	if rotIdx != 0 {
		t.Fatalf("first index in rotated list = %d, want 0", rotIdx)
	}
	// A few more assignments to confirm the new list is also monotonic.
	for i := uint64(1); i < 5; i++ {
		gotID, gotIdx, err := s.AssignIndex(ctx)
		if err != nil {
			t.Fatalf("post-rotation AssignIndex[%d]: %v", i, err)
		}
		if gotID != rotID {
			t.Fatalf("post-rotation list flipped again at i=%d: %q → %q", i, rotID, gotID)
		}
		if gotIdx != i {
			t.Fatalf("post-rotation index = %d, want %d", gotIdx, i)
		}
	}
	// Both old and new lists must be snapshot-able.
	if _, err := s.Snapshot(ctx, firstID); err != nil {
		t.Fatalf("Snapshot of original list %q after rotation: %v", firstID, err)
	}
	if _, err := s.Snapshot(ctx, rotID); err != nil {
		t.Fatalf("Snapshot of rotated list %q: %v", rotID, err)
	}
}

func testRevAssignMonotonic(t *testing.T, s sca.RevocationStore) {
	ctx := context.Background()
	const N = 32
	seen := make(map[uint64]struct{}, N)
	var listID string
	for i := 0; i < N; i++ {
		gotID, idx, err := s.AssignIndex(ctx)
		if err != nil {
			t.Fatalf("AssignIndex[%d]: %v", i, err)
		}
		if listID == "" {
			listID = gotID
		} else if gotID != listID {
			// Rotation is permitted; this test only requires uniqueness.
			listID = gotID
			seen = make(map[uint64]struct{})
		}
		if _, dup := seen[idx]; dup {
			t.Fatalf("duplicate index %d in list %q at i=%d", idx, gotID, i)
		}
		seen[idx] = struct{}{}
	}
}

func testRevSingleBit(t *testing.T, s sca.RevocationStore) {
	ctx := context.Background()
	listID, idx, err := s.AssignIndex(ctx)
	if err != nil {
		t.Fatalf("AssignIndex: %v", err)
	}
	if err := s.Revoke(ctx, listID, idx); err != nil {
		t.Fatalf("Revoke: %v", err)
	}
	list, err := s.Snapshot(ctx, listID)
	if err != nil {
		t.Fatalf("Snapshot: %v", err)
	}
	revoked, err := list.Get(idx)
	if err != nil || !revoked {
		t.Fatalf("revoked bit not set at idx %d: %v", idx, err)
	}
	// Other bits must remain clear.
	for i := uint64(0); i < list.Size(); i++ {
		if i == idx {
			continue
		}
		v, _ := list.Get(i)
		if v {
			t.Fatalf("Revoke flipped bit %d in addition to %d", i, idx)
		}
	}
}

func testRevSnapshot(t *testing.T, s sca.RevocationStore) {
	ctx := context.Background()
	listID, _, _ := s.AssignIndex(ctx)
	// Allocate a few more so the indices we revoke are real.
	indices := []uint64{0}
	for i := 0; i < 5; i++ {
		_, idx, err := s.AssignIndex(ctx)
		if err != nil {
			t.Fatalf("AssignIndex: %v", err)
		}
		indices = append(indices, idx)
	}
	// Revoke a couple, leave the rest.
	for _, idx := range []uint64{indices[1], indices[3]} {
		if err := s.Revoke(ctx, listID, idx); err != nil {
			t.Fatalf("Revoke(%d): %v", idx, err)
		}
	}
	list, err := s.Snapshot(ctx, listID)
	if err != nil {
		t.Fatalf("Snapshot: %v", err)
	}
	for _, idx := range []uint64{indices[1], indices[3]} {
		v, _ := list.Get(idx)
		if !v {
			t.Fatalf("expected revoked at %d", idx)
		}
	}
	for _, idx := range []uint64{indices[0], indices[2], indices[4], indices[5]} {
		v, _ := list.Get(idx)
		if v {
			t.Fatalf("expected NOT revoked at %d", idx)
		}
	}
}

func testRevConcurrent(t *testing.T, s sca.RevocationStore) {
	ctx := context.Background()
	const N = 64
	type pair struct {
		listID string
		idx    uint64
	}
	out := make([]pair, N)
	var wg sync.WaitGroup
	var errs atomic.Int32
	wg.Add(N)
	start := make(chan struct{})
	for i := 0; i < N; i++ {
		i := i
		go func() {
			defer wg.Done()
			<-start
			id, idx, err := s.AssignIndex(ctx)
			if err != nil {
				errs.Add(1)
				return
			}
			out[i] = pair{id, idx}
		}()
	}
	close(start)
	wg.Wait()
	if errs.Load() > 0 {
		t.Fatalf("%d AssignIndex calls failed", errs.Load())
	}
	seen := make(map[pair]struct{}, N)
	for _, p := range out {
		if _, dup := seen[p]; dup {
			t.Fatalf("duplicate (listID=%q, idx=%d) under concurrent AssignIndex", p.listID, p.idx)
		}
		seen[p] = struct{}{}
	}
}

func testRevIdempotent(t *testing.T, s sca.RevocationStore) {
	ctx := context.Background()
	listID, idx, _ := s.AssignIndex(ctx)
	if err := s.Revoke(ctx, listID, idx); err != nil {
		t.Fatalf("first Revoke: %v", err)
	}
	if err := s.Revoke(ctx, listID, idx); err != nil {
		t.Fatalf("second Revoke (idempotency): %v", err)
	}
	list, _ := s.Snapshot(ctx, listID)
	v, _ := list.Get(idx)
	if !v {
		t.Fatal("idempotent revoke should leave bit set")
	}
}
