// SPDX-License-Identifier: MIT

package storemem

import (
	"context"
	"sync"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
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

// SCARevocationStore is an in-memory sca.RevocationStore. It maintains a
// single bitstring of fixed size; AssignIndex returns the next sequential
// index and the configured listID.
type SCARevocationStore struct {
	mu     sync.Mutex
	listID string
	list   *vc.StatusList
	next   uint64
}

// SCARevocationStoreOption configures NewSCARevocationStore.
type SCARevocationStoreOption func(*SCARevocationStore)

// WithListSize overrides the default 131_072-bit list capacity.
func WithListSize(size uint64) SCARevocationStoreOption {
	return func(s *SCARevocationStore) {
		s.list = vc.NewStatusList(size)
	}
}

// NewSCARevocationStore returns a revocation store backed by a single status
// list with the supplied ID and a default 131072-bit capacity.
func NewSCARevocationStore(listID string, opts ...SCARevocationStoreOption) *SCARevocationStore {
	s := &SCARevocationStore{listID: listID, list: vc.NewStatusList(131_072)}
	for _, o := range opts {
		o(s)
	}
	return s
}

// AssignIndex implements sca.RevocationStore.
func (s *SCARevocationStore) AssignIndex(_ context.Context) (string, uint64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	idx := s.next
	if idx >= s.list.Size() {
		return "", 0, sca.New(500, sca.CodeRevoked, "status list capacity exhausted")
	}
	s.next++
	return s.listID, idx, nil
}

// Revoke implements sca.RevocationStore.
func (s *SCARevocationStore) Revoke(_ context.Context, listID string, index uint64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if listID != s.listID {
		return sca.New(404, sca.CodeRevoked, "unknown listID")
	}
	return s.list.Set(index, true)
}

// Snapshot implements sca.RevocationStore. The returned StatusList is a deep
// copy; callers may not mutate the store via it.
func (s *SCARevocationStore) Snapshot(_ context.Context, listID string) (*vc.StatusList, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if listID != s.listID {
		return nil, sca.New(404, sca.CodeRevoked, "unknown listID")
	}
	encoded, err := s.list.Encode()
	if err != nil {
		return nil, err
	}
	return vc.DecodeStatusList(encoded)
}
