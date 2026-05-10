// SPDX-License-Identifier: MIT

package vc

import "testing"

func TestMemoryTrustStore(t *testing.T) {
	s := NewMemoryTrustStore([]TrustEntry{{
		Issuer:         "did:web:sca.sh4dow.org",
		AcceptedLevels: []string{LevelL1, LevelL2},
	}})

	e, ok := s.Lookup("did:web:sca.sh4dow.org")
	if !ok {
		t.Fatal("expected entry")
	}
	if !e.LevelAccepted(LevelL1) || !e.LevelAccepted(LevelL2) {
		t.Fatal("expected L1+L2 accepted")
	}
	if e.LevelAccepted(LevelL3) {
		t.Fatal("L3 should not be accepted")
	}

	if _, ok := s.Lookup("did:web:other.example"); ok {
		t.Fatal("unexpected entry")
	}

	s.Set(TrustEntry{Issuer: "did:web:other.example", AcceptedLevels: []string{LevelO1}})
	if _, ok := s.Lookup("did:web:other.example"); !ok {
		t.Fatal("expected new entry")
	}

	s.Remove("did:web:other.example")
	if _, ok := s.Lookup("did:web:other.example"); ok {
		t.Fatal("expected removal")
	}
}

func TestMemoryTrustStoreCopiesSlices(t *testing.T) {
	src := []string{LevelL1}
	s := NewMemoryTrustStore([]TrustEntry{{Issuer: "x", AcceptedLevels: src}})
	src[0] = LevelL3 // mutate caller's slice

	e, _ := s.Lookup("x")
	if e.LevelAccepted(LevelL3) {
		t.Fatal("trust store leaked a reference to caller's slice")
	}
}
