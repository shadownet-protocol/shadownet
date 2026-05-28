// SPDX-License-Identifier: MIT

package did

import (
	"testing"
)

func TestParseDocumentDelegatedIssuersOnWeb(t *testing.T) {
	raw := []byte(`{
		"id": "did:web:acme.example",
		"shadownet:delegatedIssuers": [
			"did:web:sca.acme.example",
			"did:web:hr.acme.example"
		]
	}`)
	doc, err := parseDocument(raw)
	if err != nil {
		t.Fatalf("parseDocument: %v", err)
	}
	if got, want := len(doc.DelegatedIssuers), 2; got != want {
		t.Fatalf("DelegatedIssuers count = %d, want %d", got, want)
	}
	if !doc.IsDelegatedIssuer("did:web:sca.acme.example") {
		t.Errorf("expected sca.acme.example to be delegated")
	}
	if doc.IsDelegatedIssuer("did:web:other.example") {
		t.Errorf("did not expect other.example to be delegated")
	}
}

func TestParseDocumentDelegatedIssuersDroppedOnKey(t *testing.T) {
	raw := []byte(`{
		"id": "did:key:z6MkrJVnaZkeFzdQyMZu1cgjg7k1pZZ6pvBQ7XJPt4swbTQ2",
		"shadownet:delegatedIssuers": ["did:web:should.not.appear"]
	}`)
	doc, err := parseDocument(raw)
	if err != nil {
		t.Fatalf("parseDocument: %v", err)
	}
	if len(doc.DelegatedIssuers) != 0 {
		t.Errorf("DelegatedIssuers on did:key must be empty, got %v", doc.DelegatedIssuers)
	}
}

func TestIsDelegatedIssuerEmpty(t *testing.T) {
	doc := &Document{ID: "did:web:example"}
	if doc.IsDelegatedIssuer("did:web:any") {
		t.Errorf("empty document must not delegate any issuer")
	}
}
