// SPDX-License-Identifier: MIT

package vc

import "sync"

// InstitutionalPolicy describes how a verifier treats AffiliationCredentials
// from a particular organization. See RFC-0004 §Institutional trust.
//
// SubstituteForPersonhood is the personhood level (e.g. "L1") that an
// otherwise-unsolicited affiliation claim substitutes for at the
// stranger-handshake gate; the empty string means "do not substitute."
type InstitutionalPolicy struct {
	AcceptDomainControlled  bool
	SubstituteForPersonhood string
	DenyListed              bool
}

// InstitutionalStore is the institutional-trust surface of a verifier's
// trust store. It is conceptually distinct from the issuer-trust TrustStore;
// the two never share entries (one indexes by SCA DID, the other by org DID).
//
// Default returns the policy applied to any org not in the allowlist or
// denylist — typically AcceptDomainControlled=true, SubstituteForPersonhood="L1"
// — and is what makes "any resolving did:web is acceptable" the default.
type InstitutionalStore interface {
	Lookup(org string) (InstitutionalPolicy, bool)
	Default() InstitutionalPolicy
}

// MemoryInstitutionalStore is a goroutine-safe in-memory InstitutionalStore.
type MemoryInstitutionalStore struct {
	mu       sync.RWMutex
	dflt     InstitutionalPolicy
	policies map[string]InstitutionalPolicy
}

// DefaultInstitutionalPolicy is the RFC-0004 reference default: accept any
// did:web org whose document resolves, substitute for L1 at the
// stranger-handshake gate.
func DefaultInstitutionalPolicy() InstitutionalPolicy {
	return InstitutionalPolicy{
		AcceptDomainControlled:  true,
		SubstituteForPersonhood: LevelL1,
	}
}

// NewMemoryInstitutionalStore builds a store with the given default policy
// and per-org overrides.
func NewMemoryInstitutionalStore(dflt InstitutionalPolicy, overrides map[string]InstitutionalPolicy) *MemoryInstitutionalStore {
	s := &MemoryInstitutionalStore{dflt: dflt, policies: make(map[string]InstitutionalPolicy, len(overrides))}
	for k, v := range overrides {
		s.policies[k] = v
	}
	return s
}

// Lookup implements InstitutionalStore.
func (s *MemoryInstitutionalStore) Lookup(org string) (InstitutionalPolicy, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	p, ok := s.policies[org]
	return p, ok
}

// Default implements InstitutionalStore.
func (s *MemoryInstitutionalStore) Default() InstitutionalPolicy {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.dflt
}

// SetPolicy replaces the policy for an org.
func (s *MemoryInstitutionalStore) SetPolicy(org string, p InstitutionalPolicy) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.policies[org] = p
}

// Remove deletes the per-org policy; subsequent Lookup falls back to Default.
func (s *MemoryInstitutionalStore) Remove(org string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.policies, org)
}

// Accepts returns true when the verifier's institutional policy will accept
// an AffiliationCredential from this org. A per-org entry takes precedence
// over the default; a denylisted entry rejects regardless.
func (s *MemoryInstitutionalStore) Accepts(org string) bool {
	if p, ok := s.Lookup(org); ok {
		return !p.DenyListed
	}
	return s.Default().AcceptDomainControlled
}
