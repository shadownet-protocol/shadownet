// SPDX-License-Identifier: MIT

package storemem

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/sca"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

// SCASessionStore is an in-memory sca.SessionStore.
type SCASessionStore struct {
	mu       sync.Mutex
	sessions map[string]sca.Session
}

// NewSCASessionStore returns an empty session store.
func NewSCASessionStore() *SCASessionStore {
	return &SCASessionStore{sessions: make(map[string]sca.Session)}
}

// Put implements sca.SessionStore.
func (s *SCASessionStore) Put(_ context.Context, sess sca.Session) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sessions[sess.ID] = sess
	return nil
}

// Get implements sca.SessionStore.
func (s *SCASessionStore) Get(_ context.Context, id string) (sca.Session, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[id]
	if !ok {
		return sca.Session{}, sca.ErrSessionNotFound
	}
	return sess, nil
}

// MarkReady implements sca.SessionStore.
func (s *SCASessionStore) MarkReady(_ context.Context, id string, at time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[id]
	if !ok {
		return sca.ErrSessionNotFound
	}
	if sess.State != sca.StatePending {
		return sca.ErrSessionState
	}
	sess.State = sca.StateReady
	sess.ReadyAt = at
	sess.ExpiresAt = at.Add(sca.ReadyTTL)
	s.sessions[id] = sess
	return nil
}

// Consume implements sca.SessionStore.
func (s *SCASessionStore) Consume(_ context.Context, id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[id]
	if !ok {
		return sca.ErrSessionNotFound
	}
	if sess.State != sca.StateReady {
		return sca.ErrSessionState
	}
	sess.State = sca.StateConsumed
	s.sessions[id] = sess
	return nil
}

// Fail implements sca.SessionStore.
func (s *SCASessionStore) Fail(_ context.Context, id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[id]
	if !ok {
		return sca.ErrSessionNotFound
	}
	if sess.State == sca.StatePending {
		sess.State = sca.StateExpired
	} else {
		sess.State = sca.StateFailed
	}
	s.sessions[id] = sess
	return nil
}

// SCAIssuanceStore is an in-memory sca.IssuanceStore.
type SCAIssuanceStore struct {
	mu sync.Mutex
	by map[string]sca.IssuedCredential
}

// NewSCAIssuanceStore returns an empty issuance store.
func NewSCAIssuanceStore() *SCAIssuanceStore {
	return &SCAIssuanceStore{by: make(map[string]sca.IssuedCredential)}
}

// Put implements sca.IssuanceStore.
func (s *SCAIssuanceStore) Put(_ context.Context, c sca.IssuedCredential) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.by[c.JTI] = c
	return nil
}

// Get implements sca.IssuanceStore.
func (s *SCAIssuanceStore) Get(_ context.Context, jti string) (sca.IssuedCredential, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.by[jti]
	if !ok {
		return sca.IssuedCredential{}, sca.ErrJTINotFound
	}
	return c, nil
}

// SCARevocationStore is an in-memory sca.RevocationStore. It maintains
// rolling status lists: AssignIndex assigns into the active list until that
// list fills, then allocates a fresh list (`<baseID>-2`, `<baseID>-3`, …)
// and continues there. Old lists remain readable via Snapshot — that's how
// previously-issued credentials' status URLs stay resolvable.
type SCARevocationStore struct {
	mu       sync.Mutex
	baseID   string
	capacity uint64
	lists    map[string]*vc.StatusList // every list ever allocated
	seq      []string                  // creation order for naming
	active   string                    // empty until first AssignIndex
	next     uint64                    // next index in active
}

// SCARevocationStoreOption configures NewSCARevocationStore.
type SCARevocationStoreOption func(*SCARevocationStore)

// WithListSize overrides the default 131_072-bit per-list capacity.
func WithListSize(size uint64) SCARevocationStoreOption {
	return func(s *SCARevocationStore) {
		s.capacity = size
	}
}

// NewSCARevocationStore returns a rotating revocation store. baseID is the
// first list's ID; subsequent lists are named `<baseID>-2`, `<baseID>-3`, …
// Default per-list capacity is 131072 bits; override via WithListSize.
func NewSCARevocationStore(baseID string, opts ...SCARevocationStoreOption) *SCARevocationStore {
	s := &SCARevocationStore{
		baseID:   baseID,
		capacity: 131_072,
		lists:    make(map[string]*vc.StatusList),
	}
	for _, o := range opts {
		o(s)
	}
	return s
}

// listOrdinalName returns the name of the i-th allocated list (0-indexed):
// 0 → baseID, 1 → baseID-2, 2 → baseID-3, …
func (s *SCARevocationStore) listOrdinalName(i int) string {
	if i == 0 {
		return s.baseID
	}
	return fmt.Sprintf("%s-%d", s.baseID, i+1)
}

// AssignIndex implements sca.RevocationStore. Allocates a new list when the
// active one is full; never returns "capacity exhausted" under normal use.
func (s *SCARevocationStore) AssignIndex(_ context.Context) (string, uint64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.active == "" || s.next >= s.capacity {
		name := s.listOrdinalName(len(s.seq))
		s.lists[name] = vc.NewStatusList(s.capacity)
		s.seq = append(s.seq, name)
		s.active = name
		s.next = 0
	}
	idx := s.next
	s.next++
	return s.active, idx, nil
}

// Revoke implements sca.RevocationStore.
func (s *SCARevocationStore) Revoke(_ context.Context, listID string, index uint64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	list, ok := s.lists[listID]
	if !ok {
		return sca.New(404, sca.CodeRevoked, "unknown listID")
	}
	return list.Set(index, true)
}

// Snapshot implements sca.RevocationStore. The returned StatusList is a deep
// copy; callers may not mutate the store via it.
func (s *SCARevocationStore) Snapshot(_ context.Context, listID string) (*vc.StatusList, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	list, ok := s.lists[listID]
	if !ok {
		return nil, sca.New(404, sca.CodeRevoked, "unknown listID")
	}
	encoded, err := list.Encode()
	if err != nil {
		return nil, err
	}
	return vc.DecodeStatusList(encoded)
}
