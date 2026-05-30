// SPDX-License-Identifier: MIT

package csr_test

import (
	"crypto/ed25519"
	"errors"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/csr"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
)

func newKey(t *testing.T) crypto.KeyPair {
	t.Helper()
	kp, err := crypto.Generate()
	if err != nil {
		t.Fatal(err)
	}
	return kp
}

func basePayload(now int64) csr.Payload {
	return csr.Payload{
		Iss: "alice@sh4dow.org",
		Aud: "acme.example",
		Iat: now,
		Exp: now + 300,
		Req: csr.Request{Kind: "org_affiliation", Org: "acme.example"},
	}
}

func TestMintAndVerifyHappyPath(t *testing.T) {
	t.Parallel()
	subject := newKey(t)
	now := time.Now().Unix()
	token, err := csr.Mint(basePayload(now), subject)
	if err != nil {
		t.Fatal(err)
	}
	got, err := csr.Verify(token, csr.VerifyOptions{
		Now:               time.Now,
		ExpectedAudience:  "acme.example",
		ResolveSubjectKey: func(string) (ed25519.PublicKey, error) { return subject.Public, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	if got.Iss != "alice@sh4dow.org" {
		t.Fatalf("Iss = %q", got.Iss)
	}
	if got.Req.Kind != "org_affiliation" {
		t.Fatalf("Req.Kind = %q", got.Req.Kind)
	}
}

func TestMintRejectsLifetimeOverMax(t *testing.T) {
	t.Parallel()
	subject := newKey(t)
	now := time.Now().Unix()
	p := basePayload(now)
	p.Exp = now + int64((csr.MaxLifetime + time.Second).Seconds())
	if _, err := csr.Mint(p, subject); !errors.Is(err, csr.ErrLifetimeExceeded) {
		t.Fatalf("expected ErrLifetimeExceeded, got %v", err)
	}
}

func TestVerifyWrongAudienceRejected(t *testing.T) {
	t.Parallel()
	subject := newKey(t)
	now := time.Now().Unix()
	token, _ := csr.Mint(basePayload(now), subject)
	_, err := csr.Verify(token, csr.VerifyOptions{
		Now:               time.Now,
		ExpectedAudience:  "other.example",
		ResolveSubjectKey: func(string) (ed25519.PublicKey, error) { return subject.Public, nil },
	})
	if !errors.Is(err, csr.ErrAudienceMismatch) {
		t.Fatalf("expected ErrAudienceMismatch, got %v", err)
	}
}

func TestVerifyExpiredRejected(t *testing.T) {
	t.Parallel()
	subject := newKey(t)
	// Mint with the current time, then advance the test clock past exp.
	now := time.Now().Unix()
	token, err := csr.Mint(basePayload(now), subject)
	if err != nil {
		t.Fatal(err)
	}
	future := time.Now().Add(2 * time.Hour)
	_, err = csr.Verify(token, csr.VerifyOptions{
		Now:               func() time.Time { return future },
		ExpectedAudience:  "acme.example",
		ResolveSubjectKey: func(string) (ed25519.PublicKey, error) { return subject.Public, nil },
	})
	if !errors.Is(err, csr.ErrExpired) {
		t.Fatalf("expected ErrExpired, got %v", err)
	}
}

func TestVerifySignatureMismatchRejected(t *testing.T) {
	t.Parallel()
	subject := newKey(t)
	other := newKey(t)
	now := time.Now().Unix()
	token, _ := csr.Mint(basePayload(now), subject)
	_, err := csr.Verify(token, csr.VerifyOptions{
		Now:               time.Now,
		ExpectedAudience:  "acme.example",
		ResolveSubjectKey: func(string) (ed25519.PublicKey, error) { return other.Public, nil },
	})
	if !errors.Is(err, csr.ErrSignature) {
		t.Fatalf("expected ErrSignature, got %v", err)
	}
}

func TestKeyedSubjectRoundtrip(t *testing.T) {
	t.Parallel()
	// Direct-mode Subject: iss is the multibase pubkey.
	subject := newKey(t)
	pub, err := identifiers.EncodePubKey(subject.Public)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().Unix()
	p := basePayload(now)
	p.Iss = pub
	token, err := csr.Mint(p, subject)
	if err != nil {
		t.Fatal(err)
	}
	got, err := csr.Verify(token, csr.VerifyOptions{
		Now:               time.Now,
		ExpectedAudience:  "acme.example",
		ResolveSubjectKey: func(string) (ed25519.PublicKey, error) { return subject.Public, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	if got.Iss != pub {
		t.Fatalf("Iss = %q want %q", got.Iss, pub)
	}
}
