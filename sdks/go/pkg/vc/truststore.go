// SPDX-License-Identifier: MIT

package vc

import "sync"

// TrustEntry records the levels a verifier accepts from a single SCA.
type TrustEntry struct {
	Issuer         string
	AcceptedLevels []string
}

// TrustStore is the read interface verifiers consult per RFC-0004 §Trust evaluation.
type TrustStore interface {
	// Lookup returns the entry for issuer, or (TrustEntry{}, false) if absent.
	Lookup(issuer string) (TrustEntry, bool)
}

// MemoryTrustStore is a goroutine-safe in-memory TrustStore.
type MemoryTrustStore struct {
	mu      sync.RWMutex
	entries map[string]TrustEntry
}

// NewMemoryTrustStore builds a MemoryTrustStore from a slice of entries.
func NewMemoryTrustStore(entries []TrustEntry) *MemoryTrustStore {
	s := &MemoryTrustStore{entries: make(map[string]TrustEntry, len(entries))}
	for _, e := range entries {
		s.entries[e.Issuer] = TrustEntry{
			Issuer:         e.Issuer,
			AcceptedLevels: append([]string(nil), e.AcceptedLevels...),
		}
	}
	return s
}

// Lookup implements TrustStore.
func (s *MemoryTrustStore) Lookup(issuer string) (TrustEntry, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	e, ok := s.entries[issuer]
	if !ok {
		return TrustEntry{}, false
	}
	return TrustEntry{
		Issuer:         e.Issuer,
		AcceptedLevels: append([]string(nil), e.AcceptedLevels...),
	}, true
}

// Set replaces the trust entry for an issuer.
func (s *MemoryTrustStore) Set(e TrustEntry) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.entries[e.Issuer] = TrustEntry{
		Issuer:         e.Issuer,
		AcceptedLevels: append([]string(nil), e.AcceptedLevels...),
	}
}

// Remove deletes the entry for an issuer.
func (s *MemoryTrustStore) Remove(issuer string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.entries, issuer)
}

// LevelAccepted returns true when entry.AcceptedLevels contains level.
func (e TrustEntry) LevelAccepted(level string) bool {
	for _, l := range e.AcceptedLevels {
		if l == level {
			return true
		}
	}
	return false
}
