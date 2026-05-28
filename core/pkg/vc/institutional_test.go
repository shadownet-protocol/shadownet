// SPDX-License-Identifier: MIT

package vc

import "testing"

func TestDefaultInstitutionalPolicy(t *testing.T) {
	p := DefaultInstitutionalPolicy()
	if !p.AcceptDomainControlled {
		t.Errorf("default must AcceptDomainControlled")
	}
	if p.SubstituteForPersonhood != LevelL1 {
		t.Errorf("default substitution = %q, want L1", p.SubstituteForPersonhood)
	}
}

func TestMemoryInstitutionalStoreLookup(t *testing.T) {
	allowedAt := InstitutionalPolicy{AcceptDomainControlled: true, SubstituteForPersonhood: LevelL2}
	deny := InstitutionalPolicy{DenyListed: true}
	s := NewMemoryInstitutionalStore(DefaultInstitutionalPolicy(), map[string]InstitutionalPolicy{
		"did:web:acme.example":       allowedAt,
		"did:web:bad-actor.example":  deny,
	})

	if p, ok := s.Lookup("did:web:acme.example"); !ok || p.SubstituteForPersonhood != LevelL2 {
		t.Errorf("acme lookup = %+v, ok=%v", p, ok)
	}
	if p, ok := s.Lookup("did:web:bad-actor.example"); !ok || !p.DenyListed {
		t.Errorf("bad-actor lookup = %+v, ok=%v", p, ok)
	}
	if _, ok := s.Lookup("did:web:unknown.example"); ok {
		t.Errorf("unknown org should not be in store")
	}

	if !s.Accepts("did:web:acme.example") {
		t.Errorf("acme should be accepted")
	}
	if s.Accepts("did:web:bad-actor.example") {
		t.Errorf("bad-actor must not be accepted")
	}
	if !s.Accepts("did:web:unknown.example") {
		t.Errorf("unknown should be accepted by default AcceptDomainControlled")
	}
}

func TestPredicateAffiliationLeaf(t *testing.T) {
	subjects := []*SubjectCredential{
		{Issuer: "did:web:sca.example", Level: LevelL2, SubjectType: SubjectPerson},
	}
	affs := []*AffiliationCredential{
		{Issuer: "did:web:acme.example", Affiliation: "did:web:acme.example"},
	}

	p, err := ParsePredicate([]byte(`{"affiliation":"did:web:acme.example"}`))
	if err != nil {
		t.Fatalf("ParsePredicate: %v", err)
	}
	if !p.Match(subjects, affs) {
		t.Errorf("acme affiliation should match")
	}
	if !p.Match(nil, affs) {
		t.Errorf("acme affiliation should match with no subjects too")
	}
	if p.Match(subjects, nil) {
		t.Errorf("affiliation leaf must not match with no affiliation creds")
	}

	other, _ := ParsePredicate([]byte(`{"affiliation":"did:web:globex.example"}`))
	if other.Match(subjects, affs) {
		t.Errorf("globex affiliation must not match")
	}
}

func TestPredicateAffiliationCompositesWithLevel(t *testing.T) {
	subjects := []*SubjectCredential{
		{Issuer: "did:web:sca.example", Level: LevelL1, SubjectType: SubjectPerson},
	}
	affs := []*AffiliationCredential{
		{Issuer: "did:web:acme.example", Affiliation: "did:web:acme.example"},
	}
	all := mustParsePredicate(t, `{"all":[{"level":"urn:shadownet:level:L1"},{"affiliation":"did:web:acme.example"}]}`)
	if !all.Match(subjects, affs) {
		t.Fatalf("all{level+affiliation} should match")
	}
}
