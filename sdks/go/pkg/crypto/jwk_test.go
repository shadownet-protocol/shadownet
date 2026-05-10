// SPDX-License-Identifier: MIT

package crypto

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestPublicJWKRoundtrip(t *testing.T) {
	kp, err := Generate()
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	jwk, err := PublicJWK(kp.Public, "did:key:z6MkExample#1")
	if err != nil {
		t.Fatalf("PublicJWK: %v", err)
	}
	if jwk.Kty != "OKP" || jwk.Crv != "Ed25519" {
		t.Fatalf("unexpected kty/crv: %q/%q", jwk.Kty, jwk.Crv)
	}

	// Roundtrip via JSON to mirror real over-the-wire usage.
	raw, err := json.Marshal(jwk)
	if err != nil {
		t.Fatalf("json.Marshal: %v", err)
	}
	var back JWK
	if err := json.Unmarshal(raw, &back); err != nil {
		t.Fatalf("json.Unmarshal: %v", err)
	}
	got, err := back.Public()
	if err != nil {
		t.Fatalf("Public: %v", err)
	}
	if !bytes.Equal(got, kp.Public) {
		t.Fatalf("public key roundtrip mismatch")
	}
}

func TestJWKRejectsWrongKeyType(t *testing.T) {
	bad := JWK{Kty: "EC", Crv: "P-256", X: "AAAA"}
	if _, err := bad.Public(); err == nil {
		t.Fatalf("expected error for non-OKP/Ed25519 JWK")
	}
}

func TestJWKRejectsBadX(t *testing.T) {
	bad := JWK{Kty: "OKP", Crv: "Ed25519", X: "***not-base64url***"}
	if _, err := bad.Public(); err == nil {
		t.Fatalf("expected decode error")
	}
}

func TestJWKRejectsShortKey(t *testing.T) {
	short := JWK{Kty: "OKP", Crv: "Ed25519", X: "AAAA"} // 3 bytes, not 32
	if _, err := short.Public(); err == nil {
		t.Fatalf("expected length error")
	}
}
