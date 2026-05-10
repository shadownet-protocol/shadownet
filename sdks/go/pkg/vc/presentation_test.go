// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/did"
)

func TestPresentationRoundtrip(t *testing.T) {
	issKP, issuer, issKID := issuerSetup(t)
	holderKP, holder := subjectSetup(t)
	holderKID := holder + "#" + strings.TrimPrefix(holder, "did:key:")

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	credJWT, err := IssueCredential(issKP, Credential{
		Issuer: issuer, Subject: holder, JTI: "urn:uuid:c1",
		IssuedAt: now, Expires: now.Add(48 * time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
	}, IssueOptions{IssuerKeyID: issKID})
	if err != nil {
		t.Fatalf("IssueCredential: %v", err)
	}

	verifier := "did:key:z6MkVerifierPubkey"
	nonce := "nonce-001"
	vp, err := IssuePresentation(holderKP, holder, holderKID, verifier, nonce, []string{credJWT}, now, now.Add(60*time.Second))
	if err != nil {
		t.Fatalf("IssuePresentation: %v", err)
	}
	got, err := VerifyPresentation(context.Background(), did.NewKeyResolver(), vp, verifier, nonce, now.Add(10*time.Second))
	if err != nil {
		t.Fatalf("VerifyPresentation: %v", err)
	}
	if got.Holder != holder || got.Audience != verifier || got.Nonce != nonce {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}
	if len(got.Credentials) != 1 || got.Credentials[0] != credJWT {
		t.Fatalf("credentials roundtrip mismatch")
	}
}

func TestPresentationRejectsLifetimeOverCap(t *testing.T) {
	holderKP, holder := subjectSetup(t)
	holderKID := holder + "#" + strings.TrimPrefix(holder, "did:key:")
	now := time.Now().UTC()
	_, err := IssuePresentation(holderKP, holder, holderKID, "did:key:zVer", "n", []string{"x"}, now, now.Add(5*time.Minute))
	if err == nil {
		t.Fatalf("expected error: VP lifetime > 120 s")
	}
}

func TestPresentationAudienceMismatch(t *testing.T) {
	issKP, issuer, issKID := issuerSetup(t)
	holderKP, holder := subjectSetup(t)
	holderKID := holder + "#" + strings.TrimPrefix(holder, "did:key:")

	now := time.Now().UTC()
	credJWT, _ := IssueCredential(issKP, Credential{
		Issuer: issuer, Subject: holder, JTI: "urn:uuid:1",
		IssuedAt: now, Expires: now.Add(time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
	}, IssueOptions{IssuerKeyID: issKID})

	vp, _ := IssuePresentation(holderKP, holder, holderKID, "did:key:zVerifierA", "n1", []string{credJWT}, now, now.Add(60*time.Second))
	if _, err := VerifyPresentation(context.Background(), did.NewKeyResolver(), vp, "did:key:zVerifierB", "n1", now); err == nil {
		t.Fatalf("expected aud-mismatch error")
	}
}

func TestPresentationNonceMismatch(t *testing.T) {
	issKP, issuer, issKID := issuerSetup(t)
	holderKP, holder := subjectSetup(t)
	holderKID := holder + "#" + strings.TrimPrefix(holder, "did:key:")

	now := time.Now().UTC()
	credJWT, _ := IssueCredential(issKP, Credential{
		Issuer: issuer, Subject: holder, JTI: "urn:uuid:1",
		IssuedAt: now, Expires: now.Add(time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
	}, IssueOptions{IssuerKeyID: issKID})

	vp, _ := IssuePresentation(holderKP, holder, holderKID, "did:key:zVer", "good-nonce", []string{credJWT}, now, now.Add(60*time.Second))
	if _, err := VerifyPresentation(context.Background(), did.NewKeyResolver(), vp, "did:key:zVer", "wrong-nonce", now); err == nil {
		t.Fatalf("expected nonce-mismatch error")
	}
}
