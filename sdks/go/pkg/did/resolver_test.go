// SPDX-License-Identifier: MIT

package did

import (
	"bytes"
	"context"
	"errors"
	"testing"

	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
)

func TestKeyResolverProducesEd25519Document(t *testing.T) {
	kp, _ := crypto.Generate()
	didStr, _ := EncodeKey(kp.Public)

	doc, err := NewKeyResolver().Resolve(context.Background(), didStr)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if doc.ID != didStr {
		t.Fatalf("doc.ID = %q, want %q", doc.ID, didStr)
	}
	if len(doc.VerificationMethod) != 1 {
		t.Fatalf("vm count = %d", len(doc.VerificationMethod))
	}
	if !bytes.Equal(doc.VerificationMethod[0].Public, kp.Public) {
		t.Fatalf("public key mismatch")
	}
}

func TestDispatcherUnknownMethod(t *testing.T) {
	r := NewResolver(nil)
	_, err := r.Resolve(context.Background(), "did:foo:bar")
	if !errors.Is(err, ErrUnknownMethod) {
		t.Fatalf("err = %v, want ErrUnknownMethod", err)
	}
}

func TestDispatcherWebUnconfigured(t *testing.T) {
	r := NewResolver(nil)
	_, err := r.Resolve(context.Background(), "did:web:example.com")
	if !errors.Is(err, ErrUnknownMethod) {
		t.Fatalf("err = %v, want ErrUnknownMethod when web is nil", err)
	}
}

func TestLookupKeyByFragment(t *testing.T) {
	kp, _ := crypto.Generate()
	didStr, _ := EncodeKey(kp.Public)

	// did:key documents have a single verification method with fragment equal
	// to the multibase body.
	frag := didStr[len("did:key:"):]
	pub, err := LookupKey(context.Background(), NewKeyResolver(), didStr+"#"+frag)
	if err != nil {
		t.Fatalf("LookupKey: %v", err)
	}
	if !bytes.Equal(pub, kp.Public) {
		t.Fatalf("LookupKey returned wrong key")
	}
}

func TestLookupKeyNoFragmentReturnsFirst(t *testing.T) {
	kp, _ := crypto.Generate()
	didStr, _ := EncodeKey(kp.Public)
	pub, err := LookupKey(context.Background(), NewKeyResolver(), didStr)
	if err != nil {
		t.Fatalf("LookupKey: %v", err)
	}
	if !bytes.Equal(pub, kp.Public) {
		t.Fatalf("wrong key")
	}
}

func TestLookupKeyMissingFragment(t *testing.T) {
	kp, _ := crypto.Generate()
	didStr, _ := EncodeKey(kp.Public)
	if _, err := LookupKey(context.Background(), NewKeyResolver(), didStr+"#nope"); err == nil {
		t.Fatalf("expected error for missing fragment")
	}
}
