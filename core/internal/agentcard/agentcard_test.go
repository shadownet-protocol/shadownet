// SPDX-License-Identifier: MIT

package agentcard_test

import (
	"crypto/ed25519"
	"errors"
	"testing"

	"github.com/shadownet-protocol/shadownet/core/internal/agentcard"
	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
)

func newPair(t *testing.T) (ed25519.PrivateKey, ed25519.PublicKey, string) {
	t.Helper()
	kp, err := crypto.Generate()
	if err != nil {
		t.Fatal(err)
	}
	pubMB, err := identifiers.EncodePubKey(kp.Public)
	if err != nil {
		t.Fatal(err)
	}
	return kp.Private, kp.Public, pubMB
}

func TestBuildShadownameMode(t *testing.T) {
	t.Parallel()
	_, _, shadowPub := newPair(t)
	body, err := agentcard.Build(agentcard.Body{
		Name:            "Alice",
		Description:     "Alice's Shadow",
		Version:         "1.0.0",
		A2AURL:          "https://shadow.sh4dow.org/v1/a2a/alice",
		ShadowPublicKey: shadowPub,
	})
	if err != nil {
		t.Fatal(err)
	}
	if body["shadownet:v"] != "0.2" {
		t.Fatalf("shadownet:v = %v", body["shadownet:v"])
	}
	if body["shadownet:pk"] != shadowPub {
		t.Fatalf("shadownet:pk mismatch")
	}
	caps, _ := body["capabilities"].(map[string]any)
	if caps == nil {
		t.Fatal("capabilities missing")
	}
	exts, _ := caps["extensions"].([]any)
	if len(exts) != 1 {
		t.Fatalf("extensions count = %d", len(exts))
	}
}

func TestBuildAndSignShadownameMode(t *testing.T) {
	t.Parallel()
	providerPriv, providerPub, _ := newPair(t)
	_, _, shadowPub := newPair(t)
	body, err := agentcard.Build(agentcard.Body{
		Name:            "Alice",
		Description:     "Alice's Shadow",
		A2AURL:          "https://shadow.sh4dow.org/v1/a2a/alice",
		ShadowPublicKey: shadowPub,
	})
	if err != nil {
		t.Fatal(err)
	}
	signed, err := agentcard.Sign(body, providerPriv, agentcard.ModeShadowname, "sh4dow.org", shadowPub)
	if err != nil {
		t.Fatal(err)
	}
	if err := agentcard.Verify(signed, agentcard.VerifyOptions{
		ExpectedKID:   "shadownet@sh4dow.org",
		CandidateKeys: []ed25519.PublicKey{providerPub},
	}); err != nil {
		t.Fatalf("Verify: %v", err)
	}
}

func TestBuildAndSignDirectMode(t *testing.T) {
	t.Parallel()
	priv, pub, pubMB := newPair(t)
	b := agentcard.Body{
		Name:            "Self-Served",
		Description:     "Direct-addressed Shadow",
		A2AURL:          "https://192.0.2.10:8443/a2a",
		ShadowPublicKey: pubMB,
		IssueEndpoint:   "https://192.0.2.10:8443/issue",
		StatusListBase:  "https://192.0.2.10:8443/status",
	}
	b.AddPinnedSelfSigned()
	body, err := agentcard.Build(b)
	if err != nil {
		t.Fatal(err)
	}
	if body["shadownet:issueEndpoint"] != "https://192.0.2.10:8443/issue" {
		t.Fatalf("issueEndpoint = %v", body["shadownet:issueEndpoint"])
	}
	if body["shadownet:statusListBase"] != "https://192.0.2.10:8443/status" {
		t.Fatalf("statusListBase = %v", body["shadownet:statusListBase"])
	}
	if _, ok := body["securitySchemes"].(map[string]any)["shadownet:pinned-self-signed"]; !ok {
		t.Fatal("pinned-self-signed not declared")
	}
	signed, err := agentcard.Sign(body, priv, agentcard.ModeDirect, "", pubMB)
	if err != nil {
		t.Fatal(err)
	}
	if err := agentcard.Verify(signed, agentcard.VerifyOptions{
		ExpectedKID:   pubMB,
		CandidateKeys: []ed25519.PublicKey{pub},
	}); err != nil {
		t.Fatalf("Verify: %v", err)
	}
}

func TestVerifyRejectsWrongKID(t *testing.T) {
	t.Parallel()
	priv, pub, _ := newPair(t)
	_, _, shadowPub := newPair(t)
	body, _ := agentcard.Build(agentcard.Body{
		Name: "Alice", A2AURL: "https://x.example/a2a", ShadowPublicKey: shadowPub,
	})
	signed, err := agentcard.Sign(body, priv, agentcard.ModeShadowname, "sh4dow.org", shadowPub)
	if err != nil {
		t.Fatal(err)
	}
	err = agentcard.Verify(signed, agentcard.VerifyOptions{
		ExpectedKID:   "shadownet@other.example",
		CandidateKeys: []ed25519.PublicKey{pub},
	})
	if !errors.Is(err, agentcard.ErrInvalid) {
		t.Fatalf("expected ErrInvalid, got %v", err)
	}
}

func TestVerifyRejectsWrongKey(t *testing.T) {
	t.Parallel()
	priv, _, _ := newPair(t)
	_, other, _ := newPair(t)
	_, _, shadowPub := newPair(t)
	body, _ := agentcard.Build(agentcard.Body{
		Name: "Alice", A2AURL: "https://x.example/a2a", ShadowPublicKey: shadowPub,
	})
	signed, _ := agentcard.Sign(body, priv, agentcard.ModeShadowname, "sh4dow.org", shadowPub)
	err := agentcard.Verify(signed, agentcard.VerifyOptions{
		ExpectedKID:   "shadownet@sh4dow.org",
		CandidateKeys: []ed25519.PublicKey{other},
	})
	if !errors.Is(err, agentcard.ErrInvalid) {
		t.Fatalf("expected ErrInvalid, got %v", err)
	}
}

func TestVerifyMultipleCandidateKeysAcceptsAnyMatch(t *testing.T) {
	t.Parallel()
	priv, pub, _ := newPair(t)
	_, other, _ := newPair(t)
	_, _, shadowPub := newPair(t)
	body, _ := agentcard.Build(agentcard.Body{
		Name: "Alice", A2AURL: "https://x.example/a2a", ShadowPublicKey: shadowPub,
	})
	signed, _ := agentcard.Sign(body, priv, agentcard.ModeShadowname, "sh4dow.org", shadowPub)
	// pub2 first in the list — the second key (pub) should still validate.
	if err := agentcard.Verify(signed, agentcard.VerifyOptions{
		ExpectedKID:   "shadownet@sh4dow.org",
		CandidateKeys: []ed25519.PublicKey{other, pub},
	}); err != nil {
		t.Fatalf("Verify with split-key acceptance: %v", err)
	}
}

func TestSignRejectsExistingSignatures(t *testing.T) {
	t.Parallel()
	priv, _, _ := newPair(t)
	_, _, shadowPub := newPair(t)
	body, _ := agentcard.Build(agentcard.Body{
		Name: "Alice", A2AURL: "https://x.example/a2a", ShadowPublicKey: shadowPub,
	})
	body["signatures"] = []any{"already signed"}
	if _, err := agentcard.Sign(body, priv, agentcard.ModeShadowname, "sh4dow.org", shadowPub); !errors.Is(err, agentcard.ErrInvalid) {
		t.Fatalf("expected ErrInvalid, got %v", err)
	}
}

func TestBuildRejectsBadInputs(t *testing.T) {
	t.Parallel()
	cases := []agentcard.Body{
		{Name: "x", A2AURL: "https://x", ShadowPublicKey: ""},
		{Name: "x", A2AURL: "https://x", ShadowPublicKey: "not-a-pubkey"},
		{Name: "x", A2AURL: "", ShadowPublicKey: "z6Mk" + "z6Mkfake"},
	}
	for _, b := range cases {
		b := b
		t.Run(b.ShadowPublicKey, func(t *testing.T) {
			t.Parallel()
			if _, err := agentcard.Build(b); err == nil {
				t.Fatal("expected error")
			}
		})
	}
}
