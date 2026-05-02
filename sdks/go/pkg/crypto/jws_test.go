// SPDX-License-Identifier: MIT

package crypto

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"strings"
	"testing"
)

func TestSignVerifyJWS(t *testing.T) {
	kp, err := Generate()
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}

	payload := []byte(`{"hello":"shadownet"}`)
	compact, err := SignJWS(kp.Private, payload, SignerOptions{KeyID: "did:key:z6MkExample#1", Type: "JWT"})
	if err != nil {
		t.Fatalf("SignJWS: %v", err)
	}
	if strings.Count(compact, ".") != 2 {
		t.Fatalf("expected compact JWS form (two dots), got %q", compact)
	}

	hdr, body, err := VerifyJWS(kp.Public, compact)
	if err != nil {
		t.Fatalf("VerifyJWS: %v", err)
	}
	if hdr.Alg != "EdDSA" {
		t.Fatalf("alg = %q, want EdDSA", hdr.Alg)
	}
	if hdr.Kid != "did:key:z6MkExample#1" {
		t.Fatalf("kid = %q", hdr.Kid)
	}
	if hdr.Typ != "JWT" {
		t.Fatalf("typ = %q, want JWT", hdr.Typ)
	}
	if string(body) != string(payload) {
		t.Fatalf("payload roundtrip mismatch")
	}
}

func TestSignJWSRequiresKeyID(t *testing.T) {
	kp, _ := Generate()
	if _, err := SignJWS(kp.Private, []byte("x"), SignerOptions{}); err == nil {
		t.Fatalf("expected error when KeyID is empty")
	}
}

func TestVerifyJWSWrongKey(t *testing.T) {
	kp, _ := Generate()
	other, _ := Generate()
	compact, err := SignJWS(kp.Private, []byte(`{"a":1}`), SignerOptions{KeyID: "k1"})
	if err != nil {
		t.Fatalf("SignJWS: %v", err)
	}
	if _, _, err := VerifyJWS(other.Public, compact); err == nil {
		t.Fatalf("expected verification failure under wrong key")
	}
}

func TestVerifyJWSRejectsNonEdDSA(t *testing.T) {
	// Forge an HS256 JWS by hand: header.payload.signature, where signature is
	// HMAC-SHA256 of header.payload with a known key. We don't actually need a
	// valid HMAC — VerifyJWS must reject the alg before computing the signature.
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`))
	payload := base64.RawURLEncoding.EncodeToString([]byte(`{"x":1}`))
	sig := base64.RawURLEncoding.EncodeToString([]byte("not-a-real-signature"))
	bogus := header + "." + payload + "." + sig

	pub, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}
	_, _, err = VerifyJWS(pub, bogus)
	if err == nil {
		t.Fatalf("expected error for non-EdDSA alg")
	}
	// The error chain may originate from go-jose's allowed-alg check or from our
	// post-parse check; both are acceptable. We assert it surfaces somewhere.
	if !errors.Is(err, ErrUnsupportedAlgorithm) && !strings.Contains(err.Error(), "HS256") && !strings.Contains(err.Error(), "alg") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestSignVerifyJWT(t *testing.T) {
	kp, _ := Generate()
	in := struct {
		Sub string `json:"sub"`
		Iat int64  `json:"iat"`
	}{Sub: "did:key:z6MkExample", Iat: 1759200000}

	compact, err := SignJWT(kp.Private, in, SignerOptions{KeyID: "did:key:z6MkExample#0"})
	if err != nil {
		t.Fatalf("SignJWT: %v", err)
	}

	var out struct {
		Sub string `json:"sub"`
		Iat int64  `json:"iat"`
	}
	hdr, err := VerifyJWT(kp.Public, compact, &out)
	if err != nil {
		t.Fatalf("VerifyJWT: %v", err)
	}
	if hdr.Typ != "JWT" {
		t.Fatalf("default typ = %q, want JWT", hdr.Typ)
	}
	if out != in {
		t.Fatalf("claims roundtrip mismatch: got %+v want %+v", out, in)
	}
}

func TestPeekHeader(t *testing.T) {
	kp, _ := Generate()
	compact, err := SignJWS(kp.Private, []byte(`{"x":1}`), SignerOptions{KeyID: "kid-1", Type: "vc+jwt"})
	if err != nil {
		t.Fatalf("SignJWS: %v", err)
	}
	hdr, err := PeekHeader(compact)
	if err != nil {
		t.Fatalf("PeekHeader: %v", err)
	}
	if hdr.Kid != "kid-1" || hdr.Typ != "vc+jwt" || hdr.Alg != "EdDSA" {
		t.Fatalf("unexpected header: %+v", hdr)
	}
}
