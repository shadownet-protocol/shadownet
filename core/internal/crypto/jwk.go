// SPDX-License-Identifier: MIT

package crypto

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
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

// PrivateJWK encodes a full keypair as a private JWK (with the d field).
// Use SaveKeyFile to persist it.
func PrivateJWK(kp KeyPair, kid string) (JWK, error) {
	if len(kp.Public) != ed25519.PublicKeySize || len(kp.Private) != ed25519.PrivateKeySize {
		return JWK{}, fmt.Errorf("crypto: keypair invalid sizes pub=%d priv=%d", len(kp.Public), len(kp.Private))
	}
	return JWK{
		Kty: "OKP",
		Crv: "Ed25519",
		X:   base64.RawURLEncoding.EncodeToString(kp.Public),
		D:   base64.RawURLEncoding.EncodeToString(kp.Seed()),
		Kid: kid,
	}, nil
}

// KeyPair returns the full keypair encoded by a private JWK (one with the d
// field set).
func (j JWK) KeyPair() (KeyPair, error) {
	if j.Kty != "OKP" || j.Crv != "Ed25519" {
		return KeyPair{}, fmt.Errorf("crypto: jwk kty/crv = %q/%q, want OKP/Ed25519", j.Kty, j.Crv)
	}
	if j.D == "" {
		return KeyPair{}, fmt.Errorf("crypto: jwk has no d (private) field")
	}
	seed, err := base64.RawURLEncoding.DecodeString(j.D)
	if err != nil {
		return KeyPair{}, fmt.Errorf("crypto: decode jwk d: %w", err)
	}
	return NewKeyPair(seed)
}

// SaveKeyFile writes a private JWK to path with mode 0600. Refuses to
// overwrite an existing file — operators must delete or rename first.
func SaveKeyFile(path string, kp KeyPair, kid string) error {
	if _, err := os.Stat(path); err == nil {
		return fmt.Errorf("crypto: refusing to overwrite existing key file %q", path)
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("crypto: stat %s: %w", path, err)
	}
	jwk, err := PrivateJWK(kp, kid)
	if err != nil {
		return err
	}
	body, err := json.MarshalIndent(jwk, "", "  ")
	if err != nil {
		return fmt.Errorf("crypto: marshal jwk: %w", err)
	}
	if err := os.WriteFile(path, body, 0o600); err != nil {
		return fmt.Errorf("crypto: write %s: %w", path, err)
	}
	return nil
}

// LoadKeyFile reads a private JWK file written by SaveKeyFile. The file must
// be a regular file with mode 0600 or stricter; otherwise LoadKeyFile errors
// out (we refuse to load keys readable by other users).
func LoadKeyFile(path string) (KeyPair, error) {
	info, err := os.Stat(path)
	if err != nil {
		return KeyPair{}, fmt.Errorf("crypto: stat %s: %w", path, err)
	}
	if !info.Mode().IsRegular() {
		return KeyPair{}, fmt.Errorf("crypto: key file %q is not a regular file", path)
	}
	if info.Mode().Perm()&0o077 != 0 {
		return KeyPair{}, fmt.Errorf("crypto: key file %q is group/world-readable (mode %o); chmod 600", path, info.Mode().Perm())
	}
	body, err := os.ReadFile(path)
	if err != nil {
		return KeyPair{}, fmt.Errorf("crypto: read %s: %w", path, err)
	}
	var jwk JWK
	if err := json.Unmarshal(body, &jwk); err != nil {
		return KeyPair{}, fmt.Errorf("crypto: parse %s: %w", path, err)
	}
	return jwk.KeyPair()
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
