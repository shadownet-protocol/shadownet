// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
)

// stubResolver returns pre-baked DID documents from a map. did:key resolution
// still flows through the real keyResolver so we don't have to hand-build
// individual employee documents.
type stubResolver struct {
	docs map[string]*did.Document
}

func (s *stubResolver) Resolve(ctx context.Context, d string) (*did.Document, error) {
	if doc, ok := s.docs[d]; ok {
		return doc, nil
	}
	return did.NewKeyResolver().Resolve(ctx, d)
}

// orgFixture sets up an Acme org DID document, an Acme-controlled SCA, and an
// employee did:key — enough to exercise both the same-DID and delegated-issuer
// verification paths.
type orgFixture struct {
	orgKP    crypto.KeyPair
	orgDID   string
	orgKID   string
	scaKP    crypto.KeyPair
	scaDID   string
	scaKID   string
	empKP    crypto.KeyPair
	empDID   string
	resolver did.Resolver
}

func newOrgFixture(t *testing.T) *orgFixture {
	t.Helper()
	orgKP, _ := crypto.Generate()
	orgDID := "did:web:acme.example"
	orgKID := orgDID + "#k1"

	scaKP, _ := crypto.Generate()
	scaDID := "did:web:sca.acme.example"
	scaKID := scaDID + "#k1"

	empKP, _ := crypto.Generate()
	empDID, _ := did.EncodeKey(empKP.Public)

	orgDoc := &did.Document{
		ID: orgDID,
		VerificationMethod: []did.VerificationMethod{{
			ID: orgKID, Controller: orgDID, Public: orgKP.Public,
		}},
		Authentication:   []string{orgKID},
		AssertionMethod:  []string{orgKID},
		DelegatedIssuers: []string{scaDID},
	}
	scaDoc := &did.Document{
		ID: scaDID,
		VerificationMethod: []did.VerificationMethod{{
			ID: scaKID, Controller: scaDID, Public: scaKP.Public,
		}},
		Authentication:  []string{scaKID},
		AssertionMethod: []string{scaKID},
	}
	return &orgFixture{
		orgKP: orgKP, orgDID: orgDID, orgKID: orgKID,
		scaKP: scaKP, scaDID: scaDID, scaKID: scaKID,
		empKP: empKP, empDID: empDID,
		resolver: &stubResolver{docs: map[string]*did.Document{
			orgDID: orgDoc,
			scaDID: scaDoc,
		}},
	}
}

func TestAffiliationCredentialRoundtripDirectIssuer(t *testing.T) {
	f := newOrgFixture(t)
	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	c := AffiliationCredential{
		Issuer:      f.orgDID,
		Subject:     f.empDID,
		JTI:         "urn:uuid:aff-1",
		IssuedAt:    now,
		Expires:     now.Add(7 * 24 * time.Hour),
		Affiliation: f.orgDID,
		Role:        "member",
		Groups:      []string{"engineering", "platform-team"},
	}
	jwt, err := IssueAffiliationCredential(f.orgKP, c, IssueOptions{IssuerKeyID: f.orgKID})
	if err != nil {
		t.Fatalf("IssueAffiliationCredential: %v", err)
	}
	got, err := VerifyAffiliationCredential(context.Background(), f.resolver, jwt, now.Add(time.Hour))
	if err != nil {
		t.Fatalf("VerifyAffiliationCredential: %v", err)
	}
	if got.Affiliation != f.orgDID || got.Role != "member" || len(got.Groups) != 2 {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}
}

func TestAffiliationCredentialRoundtripDelegatedSCA(t *testing.T) {
	f := newOrgFixture(t)
	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	c := AffiliationCredential{
		Issuer:      f.scaDID,
		Subject:     f.empDID,
		JTI:         "urn:uuid:aff-2",
		IssuedAt:    now,
		Expires:     now.Add(7 * 24 * time.Hour),
		Affiliation: f.orgDID,
	}
	jwt, err := IssueAffiliationCredential(f.scaKP, c, IssueOptions{IssuerKeyID: f.scaKID})
	if err != nil {
		t.Fatalf("IssueAffiliationCredential: %v", err)
	}
	got, err := VerifyAffiliationCredential(context.Background(), f.resolver, jwt, now.Add(time.Hour))
	if err != nil {
		t.Fatalf("VerifyAffiliationCredential (delegated): %v", err)
	}
	if got.Issuer != f.scaDID || got.Affiliation != f.orgDID {
		t.Fatalf("delegated roundtrip mismatch: %+v", got)
	}
}

func TestAffiliationCredentialRejectsUnauthorizedIssuer(t *testing.T) {
	f := newOrgFixture(t)
	// A rogue SCA that the org has NOT delegated to.
	rogueKP, _ := crypto.Generate()
	rogueDID := "did:web:rogue.example"
	rogueKID := rogueDID + "#k1"
	rogueDoc := &did.Document{
		ID: rogueDID,
		VerificationMethod: []did.VerificationMethod{{
			ID: rogueKID, Controller: rogueDID, Public: rogueKP.Public,
		}},
		Authentication: []string{rogueKID}, AssertionMethod: []string{rogueKID},
	}
	r := &stubResolver{docs: map[string]*did.Document{
		f.orgDID: {ID: f.orgDID, VerificationMethod: []did.VerificationMethod{{
			ID: f.orgKID, Controller: f.orgDID, Public: f.orgKP.Public,
		}}, Authentication: []string{f.orgKID}, AssertionMethod: []string{f.orgKID},
			DelegatedIssuers: []string{f.scaDID}, // does NOT include rogueDID
		},
		rogueDID: rogueDoc,
	}}
	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	c := AffiliationCredential{
		Issuer:      rogueDID,
		Subject:     f.empDID,
		JTI:         "urn:uuid:aff-rogue",
		IssuedAt:    now,
		Expires:     now.Add(time.Hour),
		Affiliation: f.orgDID,
	}
	jwt, err := IssueAffiliationCredential(rogueKP, c, IssueOptions{IssuerKeyID: rogueKID})
	if err != nil {
		t.Fatalf("IssueAffiliationCredential: %v", err)
	}
	if _, err := VerifyAffiliationCredential(context.Background(), r, jwt, now.Add(time.Minute)); err == nil {
		t.Fatalf("expected delegation rejection")
	}
}

func TestAffiliationIssueRejectsLifetimeOverCap(t *testing.T) {
	f := newOrgFixture(t)
	now := time.Now().UTC()
	c := AffiliationCredential{
		Issuer:      f.orgDID,
		Subject:     f.empDID,
		JTI:         "urn:uuid:x",
		IssuedAt:    now,
		Expires:     now.Add(60 * 24 * time.Hour), // 60d, over 30d cap
		Affiliation: f.orgDID,
	}
	if _, err := IssueAffiliationCredential(f.orgKP, c, IssueOptions{IssuerKeyID: f.orgKID}); err == nil {
		t.Fatalf("expected lifetime-cap error")
	}
}

func TestAffiliationIssueRejectsNonWebSubjectIssuer(t *testing.T) {
	f := newOrgFixture(t)
	now := time.Now().UTC()
	// Issuer as did:key — not allowed.
	c := AffiliationCredential{
		Issuer:      f.empDID,
		Subject:     f.empDID,
		JTI:         "urn:uuid:x",
		IssuedAt:    now,
		Expires:     now.Add(time.Hour),
		Affiliation: f.orgDID,
	}
	if _, err := IssueAffiliationCredential(f.empKP, c, IssueOptions{IssuerKeyID: f.empDID}); err == nil {
		t.Fatalf("expected did:key issuer rejection")
	}
}

func TestAffiliationVerifyExpired(t *testing.T) {
	f := newOrgFixture(t)
	iat := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	c := AffiliationCredential{
		Issuer: f.orgDID, Subject: f.empDID, JTI: "urn:uuid:exp",
		IssuedAt: iat, Expires: iat.Add(time.Hour),
		Affiliation: f.orgDID,
	}
	jwt, err := IssueAffiliationCredential(f.orgKP, c, IssueOptions{IssuerKeyID: f.orgKID})
	if err != nil {
		t.Fatalf("IssueAffiliationCredential: %v", err)
	}
	if _, err := VerifyAffiliationCredential(context.Background(), f.resolver, jwt, iat.Add(2*time.Hour)); err == nil {
		t.Fatalf("expected expiration error")
	}
}
