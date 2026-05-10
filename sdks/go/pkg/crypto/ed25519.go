// SPDX-License-Identifier: MIT

package crypto

import (
	"crypto/ed25519"
	"crypto/rand"
	"fmt"
)

// KeyPair holds an Ed25519 signing keypair.
//
// The zero value is invalid; use Generate or NewKeyPair to construct one.
type KeyPair struct {
	Public  ed25519.PublicKey
	Private ed25519.PrivateKey
}

// Generate returns a new Ed25519 keypair drawn from crypto/rand.
func Generate() (KeyPair, error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return KeyPair{}, fmt.Errorf("crypto: generate ed25519: %w", err)
	}
	return KeyPair{Public: pub, Private: priv}, nil
}

// NewKeyPair builds a KeyPair from a 32-byte seed (the "secret key" of
// RFC 8032 §5.1). Useful for deterministic test vectors and for loading a
// previously-saved seed from disk.
func NewKeyPair(seed []byte) (KeyPair, error) {
	if len(seed) != ed25519.SeedSize {
		return KeyPair{}, fmt.Errorf("crypto: seed length = %d, want %d", len(seed), ed25519.SeedSize)
	}
	priv := ed25519.NewKeyFromSeed(seed)
	pub, ok := priv.Public().(ed25519.PublicKey)
	if !ok {
		return KeyPair{}, fmt.Errorf("crypto: unexpected public key type %T", priv.Public())
	}
	return KeyPair{Public: pub, Private: priv}, nil
}

// Sign returns a detached Ed25519 signature over msg.
func (k KeyPair) Sign(msg []byte) []byte {
	return ed25519.Sign(k.Private, msg)
}

// Seed returns the 32-byte seed of the keypair.
func (k KeyPair) Seed() []byte {
	return k.Private.Seed()
}

// Verify reports whether sig is a valid Ed25519 signature of msg under pub.
func Verify(pub ed25519.PublicKey, msg, sig []byte) bool {
	if len(pub) != ed25519.PublicKeySize {
		return false
	}
	return ed25519.Verify(pub, msg, sig)
}
