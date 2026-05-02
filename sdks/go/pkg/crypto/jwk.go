// SPDX-License-Identifier: MIT

package crypto

import (
	"crypto/ed25519"
	"encoding/base64"
	"fmt"
)

// JWK is the JSON Web Key representation of an Ed25519 key per RFC 8037.
//
// Only the OKP key type with the Ed25519 curve is supported; any other
// kty/crv pair fails verification, matching RFC-0001 §Cryptography.
type JWK struct {
	Kty string `json:"kty"`
	Crv string `json:"crv"`
	X   string `json:"x"`
	D   string `json:"d,omitempty"`
	Kid string `json:"kid,omitempty"`
}

// PublicJWK returns the public-half JWK for pub. kid is optional.
func PublicJWK(pub ed25519.PublicKey, kid string) (JWK, error) {
	if len(pub) != ed25519.PublicKeySize {
		return JWK{}, fmt.Errorf("crypto: public key length = %d, want %d", len(pub), ed25519.PublicKeySize)
	}
	return JWK{
		Kty: "OKP",
		Crv: "Ed25519",
		X:   base64.RawURLEncoding.EncodeToString(pub),
		Kid: kid,
	}, nil
}

// Public returns the Ed25519 public key encoded by j.
func (j JWK) Public() (ed25519.PublicKey, error) {
	if j.Kty != "OKP" || j.Crv != "Ed25519" {
		return nil, fmt.Errorf("crypto: jwk kty/crv = %q/%q, want OKP/Ed25519", j.Kty, j.Crv)
	}
	raw, err := base64.RawURLEncoding.DecodeString(j.X)
	if err != nil {
		return nil, fmt.Errorf("crypto: decode jwk x: %w", err)
	}
	if len(raw) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("crypto: jwk public key length = %d, want %d", len(raw), ed25519.PublicKeySize)
	}
	return ed25519.PublicKey(raw), nil
}
