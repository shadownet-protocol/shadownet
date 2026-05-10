// SPDX-License-Identifier: MIT

package storetest

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sca"
)

// SessionStoreFactory returns a fresh, empty SessionStore per call.
//
// Implementations that need cleanup (e.g. a per-test schema) hook into the
// returned store's lifecycle via t.Cleanup inside the factory.
type SessionStoreFactory func(t *testing.T) sca.SessionStore

// RunSessionStore exercises the full sca.SessionStore contract against the
// store the factory produces.
func RunSessionStore(t *testing.T, factory SessionStoreFactory) {
	t.Helper()
	t.Run("PutGetRoundtrip", func(t *testing.T) { testSessionPutGet(t, factory(t)) })
	t.Run("MarkReadyTransitionsPendingToReady", func(t *testing.T) { testSessionMarkReady(t, factory(t)) })
	t.Run("MarkReadyOnReadyReturnsErrSessionState", func(t *testing.T) { testSessionMarkReadyTwice(t, factory(t)) })
	t.Run("ConsumeTransitionsReadyToConsumed", func(t *testing.T) { testSessionConsume(t, factory(t)) })
	t.Run("DoubleConsumeReturnsErrSessionState", func(t *testing.T) { testSessionDoubleConsume(t, factory(t)) })
	t.Run("FailFromPendingMarksExpired", func(t *testing.T) { testSessionFailPending(t, factory(t)) })
	t.Run("FailFromReadyMarksFailed", func(t *testing.T) { testSessionFailReady(t, factory(t)) })
	t.Run("GetMissingReturnsErrSessionNotFound", func(t *testing.T) { testSessionGetMissing(t, factory(t)) })
	t.Run("ConcurrentMarkReadyOnlyOneWins", func(t *testing.T) { testSessionConcurrentMarkReady(t, factory(t)) })
}

func newPendingSession(id string) sca.Session {
	now := time.Now().UTC().Truncate(time.Second)
	return sca.Session{
		ID:        id,
		Subject:   "did:key:zSubject",
		Level:     "urn:shadownet:level:L1",
		Method:    "instant-approval",
		State:     sca.StatePending,
		Next:      sca.NextStep{Kind: sca.StepInPerson, TTL: 60},
		CreatedAt: now,
		ExpiresAt: now.Add(sca.PendingTTL),
	}
}

func testSessionPutGet(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	in := newPendingSession("sess-roundtrip")
	if err := s.Put(ctx, in); err != nil {
		t.Fatalf("Put: %v", err)
	}
	got, err := s.Get(ctx, in.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.ID != in.ID || got.Subject != in.Subject || got.State != sca.StatePending {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}
	if got.Next.Kind != sca.StepInPerson || got.Next.TTL != 60 {
		t.Fatalf("Next not preserved: %+v", got.Next)
	}
}

func testSessionMarkReady(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	in := newPendingSession("sess-mark-ready")
	if err := s.Put(ctx, in); err != nil {
		t.Fatalf("Put: %v", err)
	}
	at := time.Now().UTC().Truncate(time.Second)
	if err := s.MarkReady(ctx, in.ID, at); err != nil {
		t.Fatalf("MarkReady: %v", err)
	}
	got, _ := s.Get(ctx, in.ID)
	if got.State != sca.StateReady {
		t.Fatalf("state = %q, want ready", got.State)
	}
	if !got.ReadyAt.Equal(at) {
		t.Fatalf("ReadyAt = %v, want %v", got.ReadyAt, at)
	}
}

func testSessionMarkReadyTwice(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	in := newPendingSession("sess-mark-ready-twice")
	_ = s.Put(ctx, in)
	if err := s.MarkReady(ctx, in.ID, time.Now()); err != nil {
		t.Fatalf("first MarkReady: %v", err)
	}
	if err := s.MarkReady(ctx, in.ID, time.Now()); !errors.Is(err, sca.ErrSessionState) {
		t.Fatalf("second MarkReady: got %v, want ErrSessionState", err)
	}
}

func testSessionConsume(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	in := newPendingSession("sess-consume")
	_ = s.Put(ctx, in)
	if err := s.MarkReady(ctx, in.ID, time.Now()); err != nil {
		t.Fatalf("MarkReady: %v", err)
	}
	if err := s.Consume(ctx, in.ID); err != nil {
		t.Fatalf("Consume: %v", err)
	}
	got, _ := s.Get(ctx, in.ID)
	if got.State != sca.StateConsumed {
		t.Fatalf("state = %q, want consumed", got.State)
	}
}

func testSessionDoubleConsume(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	in := newPendingSession("sess-double-consume")
	_ = s.Put(ctx, in)
	_ = s.MarkReady(ctx, in.ID, time.Now())
	if err := s.Consume(ctx, in.ID); err != nil {
		t.Fatalf("first Consume: %v", err)
	}
	if err := s.Consume(ctx, in.ID); !errors.Is(err, sca.ErrSessionState) {
		t.Fatalf("second Consume: got %v, want ErrSessionState", err)
	}
}

func testSessionFailPending(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	in := newPendingSession("sess-fail-pending")
	_ = s.Put(ctx, in)
	if err := s.Fail(ctx, in.ID); err != nil {
		t.Fatalf("Fail: %v", err)
	}
	got, _ := s.Get(ctx, in.ID)
	if got.State != sca.StateExpired {
		t.Fatalf("Fail-from-pending → state = %q, want expired", got.State)
	}
}

func testSessionFailReady(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	in := newPendingSession("sess-fail-ready")
	_ = s.Put(ctx, in)
	_ = s.MarkReady(ctx, in.ID, time.Now())
	if err := s.Fail(ctx, in.ID); err != nil {
		t.Fatalf("Fail: %v", err)
	}
	got, _ := s.Get(ctx, in.ID)
	if got.State != sca.StateFailed {
		t.Fatalf("Fail-from-ready → state = %q, want failed", got.State)
	}
}

func testSessionGetMissing(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	if _, err := s.Get(ctx, "no-such-session"); !errors.Is(err, sca.ErrSessionNotFound) {
		t.Fatalf("got %v, want ErrSessionNotFound", err)
	}
}

func testSessionConcurrentMarkReady(t *testing.T, s sca.SessionStore) {
	ctx := context.Background()
	in := newPendingSession("sess-concurrent")
	if err := s.Put(ctx, in); err != nil {
		t.Fatalf("Put: %v", err)
	}
	const N = 16
	var (
		wg       sync.WaitGroup
		wins     atomic.Int32
		stateErr atomic.Int32
	)
	wg.Add(N)
	start := make(chan struct{})
	for i := 0; i < N; i++ {
		go func() {
			defer wg.Done()
			<-start
			err := s.MarkReady(ctx, in.ID, time.Now())
			switch {
			case err == nil:
				wins.Add(1)
			case errors.Is(err, sca.ErrSessionState):
				stateErr.Add(1)
			default:
				t.Errorf("unexpected MarkReady err: %v", err)
			}
		}()
	}
	close(start)
	wg.Wait()
	if wins.Load() != 1 {
		t.Fatalf("wins = %d, want exactly 1", wins.Load())
	}
	if int(wins.Load()+stateErr.Load()) != N {
		t.Fatalf("wins=%d, stateErr=%d, total != N=%d", wins.Load(), stateErr.Load(), N)
	}
}
