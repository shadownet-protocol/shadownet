// SPDX-License-Identifier: MIT

package vc

import (
	"errors"
	"testing"
)

func TestPredicateLeafs(t *testing.T) {
	creds := []*Credential{
		{Issuer: "did:web:sca.shadownet.example", Level: LevelL2, SubjectType: SubjectPerson},
		{Issuer: "did:web:other.example", Level: LevelL1, SubjectType: SubjectPerson},
	}

	cases := []struct {
		name string
		json string
		want bool
	}{
		{"level matches", `{"level":"urn:shadownet:level:L2"}`, true},
		{"level absent", `{"level":"urn:shadownet:level:L3"}`, false},
		{"issuer matches", `{"issuer":"did:web:sca.shadownet.example"}`, true},
		{"subjectType matches", `{"subjectType":"person"}`, true},
		{"subjectType absent", `{"subjectType":"organization"}`, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p, err := ParsePredicate([]byte(tc.json))
			if err != nil {
				t.Fatalf("ParsePredicate: %v", err)
			}
			if got := p.Match(creds); got != tc.want {
				t.Fatalf("Match = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestPredicateAllAnyNot(t *testing.T) {
	creds := []*Credential{
		{Issuer: "did:web:sca.example", Level: LevelL2, SubjectType: SubjectPerson},
	}

	all := mustParsePredicate(t, `{"all":[
		{"level":"urn:shadownet:level:L2"},
		{"issuer":"did:web:sca.example"}
	]}`)
	if !all.Match(creds) {
		t.Fatal("all should match")
	}

	any := mustParsePredicate(t, `{"any":[
		{"level":"urn:shadownet:level:L1"},
		{"level":"urn:shadownet:level:L2"}
	]}`)
	if !any.Match(creds) {
		t.Fatal("any should match")
	}

	not := mustParsePredicate(t, `{"not":{"level":"urn:shadownet:level:L3"}}`)
	if !not.Match(creds) {
		t.Fatal("not should match")
	}
}

func TestPredicateDepthCap(t *testing.T) {
	// 5 levels of nesting (root + 5 children) must trip the cap.
	deep := `{"all":[{"all":[{"all":[{"all":[{"all":[{"level":"x"}]}]}]}]}]}`
	if _, err := ParsePredicate([]byte(deep)); !errors.Is(err, ErrPredicateTooDeep) {
		t.Fatalf("expected ErrPredicateTooDeep, got %v", err)
	}
}

func TestPredicateRejectsMultipleSetFields(t *testing.T) {
	bad := `{"level":"x","issuer":"y"}`
	if _, err := ParsePredicate([]byte(bad)); err == nil {
		t.Fatalf("expected error for ambiguous predicate")
	}
}

func TestPredicateRejectsEmptyAll(t *testing.T) {
	if _, err := ParsePredicate([]byte(`{"all":[]}`)); err == nil {
		t.Fatalf("expected error for empty all")
	}
}

func mustParsePredicate(t *testing.T, s string) *Predicate {
	t.Helper()
	p, err := ParsePredicate([]byte(s))
	if err != nil {
		t.Fatalf("ParsePredicate(%q): %v", s, err)
	}
	return p
}
