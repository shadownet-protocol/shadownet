// SPDX-License-Identifier: MIT

package credential_test

import (
	"crypto/ed25519"
	"errors"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/credential"
	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
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

func basePayload(now int64) credential.Payload {
	return credential.Payload{
		Iss:  "acme.example",
		Sub:  "alice@sh4dow.org",
		Kind: credential.KindOrgAffiliation,
		Org:  "acme.example",
		Iat:  now,
		Exp:  now + 86400,
		Rev:  credential.Revocation{Epoch: "2026q2", Idx: 42},
	}
}

func TestMintAndVerifyHappyPath(t *testing.T) {
	t.Parallel()
	issuer := newKey(t)
	now := time.Now().Unix()
	token, err := credential.Mint(basePayload(now), issuer)
	if err != nil {
		t.Fatal(err)
	}
	got, err := credential.Verify(token, credential.VerifyOptions{
		Now: time.Now,
		ResolveIssuerKey: func(string) (ed25519.PublicKey, error) {
			return issuer.Public, nil
		},
		AuthorizeIssuerForOrg: func(iss, org string) error {
			if iss != "acme.example" || org != "acme.example" {
				t.Fatalf("authorize called with iss=%q org=%q", iss, org)
			}
			return nil
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if got.Sub != "alice@sh4dow.org" {
		t.Fatalf("sub = %q", got.Sub)
	}
	if got.Rev.Epoch != "2026q2" || got.Rev.Idx != 42 {
		t.Fatalf("rev = %+v", got.Rev)
	}
}

func TestMintRejectsUnknownKind(t *testing.T) {
	t.Parallel()
	issuer := newKey(t)
	now := time.Now().Unix()
	p := basePayload(now)
	p.Kind = "personhood"
	if _, err := credential.Mint(p, issuer); !errors.Is(err, credential.ErrUnknownKind) {
		t.Fatalf("expected ErrUnknownKind, got %v", err)
	}
}

func TestMintRejectsLifetimeOver30Days(t *testing.T) {
	t.Parallel()
	issuer := newKey(t)
	now := time.Now().Unix()
	p := basePayload(now)
	p.Exp = now + int64((credential.MaxOrgAffiliationLifetime + time.Second).Seconds())
	if _, err := credential.Mint(p, issuer); !errors.Is(err, credential.ErrLifetimeExceeded) {
		t.Fatalf("expected ErrLifetimeExceeded, got %v", err)
	}
}

func TestVerifyExpiredRejected(t *testing.T) {
	t.Parallel()
	issuer := newKey(t)
	now := time.Now().Unix()
	p := basePayload(now)
	p.Iat = now - 7200
	p.Exp = now - 3600
	token, err := credential.Mint(p, issuer)
	if err != nil {
		t.Fatal(err)
	}
	_, err = credential.Verify(token, credential.VerifyOptions{
		Now:              time.Now,
		ResolveIssuerKey: func(string) (ed25519.PublicKey, error) { return issuer.Public, nil },
	})
	if !errors.Is(err, credential.ErrExpired) {
		t.Fatalf("expected ErrExpired, got %v", err)
	}
}

func TestVerifySignatureMismatchRejected(t *testing.T) {
	t.Parallel()
	issuer := newKey(t)
	other := newKey(t)
	now := time.Now().Unix()
	token, err := credential.Mint(basePayload(now), issuer)
	if err != nil {
		t.Fatal(err)
	}
	_, err = credential.Verify(token, credential.VerifyOptions{
		Now:              time.Now,
		ResolveIssuerKey: func(string) (ed25519.PublicKey, error) { return other.Public, nil },
	})
	if !errors.Is(err, credential.ErrSignature) {
		t.Fatalf("expected ErrSignature, got %v", err)
	}
}

func TestVerifyAuthorizeRejectionPropagates(t *testing.T) {
	t.Parallel()
	issuer := newKey(t)
	now := time.Now().Unix()
	token, _ := credential.Mint(basePayload(now), issuer)
	_, err := credential.Verify(token, credential.VerifyOptions{
		Now:              time.Now,
		ResolveIssuerKey: func(string) (ed25519.PublicKey, error) { return issuer.Public, nil },
		AuthorizeIssuerForOrg: func(iss, org string) error {
			return errors.New("policy: nope")
		},
	})
	if !errors.Is(err, credential.ErrIssuerUnauthd) {
		t.Fatalf("expected ErrIssuerUnauthd, got %v", err)
	}
}

func TestKeyedIssuerRoundtrip(t *testing.T) {
	t.Parallel()
	// Keyed Hub: iss is the multibase pubkey itself.
	issuer := newKey(t)
	hubPub, err := identifiers.EncodePubKey(issuer.Public)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().Unix()
	p := basePayload(now)
	p.Iss = hubPub
	p.Org = hubPub // §6.6: keyed issuer => iss == org
	token, err := credential.Mint(p, issuer)
	if err != nil {
		t.Fatal(err)
	}
	got, err := credential.Verify(token, credential.VerifyOptions{
		Now:              time.Now,
		ResolveIssuerKey: func(string) (ed25519.PublicKey, error) { return issuer.Public, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	if got.Iss != hubPub {
		t.Fatalf("Iss = %q, want %q", got.Iss, hubPub)
	}
}

func TestVerifyRejectsWrongTyp(t *testing.T) {
	t.Parallel()
	issuer := newKey(t)
	// Sign claims under a different typ → shouldn't be accepted by Verify.
	now := time.Now().Unix()
	p := basePayload(now)
	token, err := crypto.SignJWT(issuer.Private, p, crypto.SignerOptions{
		Type:  "JWT",
		KeyID: "irrelevant",
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = credential.Verify(token, credential.VerifyOptions{
		Now:              time.Now,
		ResolveIssuerKey: func(string) (ed25519.PublicKey, error) { return issuer.Public, nil },
	})
	if !errors.Is(err, credential.ErrInvalid) {
		t.Fatalf("expected ErrInvalid, got %v", err)
	}
}
