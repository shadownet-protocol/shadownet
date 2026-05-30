// SPDX-License-Identifier: MIT

package crypto

import (
	"bytes"
	"crypto/ed25519"
	"encoding/hex"
	"testing"
)

// vectors from RFC 8032 §7.1.
var rfc8032Vectors = []struct {
	name    string
	seedHex string
	pubHex  string
	msgHex  string
	sigHex  string
}{
	{
		name:    "TEST 1 (empty message)",
		seedHex: "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
		pubHex:  "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
		msgHex:  "",
		sigHex:  "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
	},
	{
		name:    "TEST 2 (single byte)",
		seedHex: "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
		pubHex:  "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
		msgHex:  "72",
		sigHex:  "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
	},
	{
		name:    "TEST 3 (two bytes)",
		seedHex: "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
		pubHex:  "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
		msgHex:  "af82",
		sigHex:  "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
	},
}

func TestRFC8032KAT(t *testing.T) {
	for _, tc := range rfc8032Vectors {
		t.Run(tc.name, func(t *testing.T) {
			seed := mustHex(t, tc.seedHex)
			wantPub := mustHex(t, tc.pubHex)
			msg := mustHex(t, tc.msgHex)
			wantSig := mustHex(t, tc.sigHex)

			kp, err := NewKeyPair(seed)
			if err != nil {
				t.Fatalf("NewKeyPair: %v", err)
			}
			if !bytes.Equal(kp.Public, wantPub) {
				t.Fatalf("public = %x, want %x", kp.Public, wantPub)
			}
			gotSig := kp.Sign(msg)
			if !bytes.Equal(gotSig, wantSig) {
				t.Fatalf("signature = %x, want %x", gotSig, wantSig)
			}
			if !Verify(kp.Public, msg, wantSig) {
				t.Fatalf("Verify rejected the RFC 8032 signature")
			}
		})
	}
}

func TestGenerateRoundtrip(t *testing.T) {
	kp, err := Generate()
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if len(kp.Public) != ed25519.PublicKeySize {
		t.Fatalf("public size = %d, want %d", len(kp.Public), ed25519.PublicKeySize)
	}
	if len(kp.Private) != ed25519.PrivateKeySize {
		t.Fatalf("private size = %d, want %d", len(kp.Private), ed25519.PrivateKeySize)
	}
	msg := []byte("shadownet")
	sig := kp.Sign(msg)
	if !Verify(kp.Public, msg, sig) {
		t.Fatalf("Verify rejected freshly generated signature")
	}

	// Mutating the message must invalidate the signature.
	bad := append([]byte{}, msg...)
	bad[0] ^= 1
	if Verify(kp.Public, bad, sig) {
		t.Fatalf("Verify accepted modified message")
	}
}

func TestNewKeyPairBadSeedSize(t *testing.T) {
	if _, err := NewKeyPair(make([]byte, 31)); err == nil {
		t.Fatalf("expected error for 31-byte seed")
	}
}

func TestVerifyShortKey(t *testing.T) {
	if Verify(make([]byte, 16), []byte("x"), make([]byte, 64)) {
		t.Fatalf("Verify accepted undersized public key")
	}
}

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("hex.DecodeString(%q): %v", s, err)
	}
	return b
}
