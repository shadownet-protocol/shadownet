// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/did"
)

// issuerSetup creates an issuer keypair and a did:key DID for it. While
// real SCAs use did:web, did:key is sufficient for the credential machinery
// and avoids spinning up an HTTPS server in unit tests.
func issuerSetup(t *testing.T) (crypto.KeyPair, string, string) {
	t.Helper()
	kp, err := crypto.Generate()
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	issuer, _ := did.EncodeKey(kp.Public)
	kid := issuer + "#" + strings.TrimPrefix(issuer, "did:key:")
	return kp, issuer, kid
}

func subjectSetup(t *testing.T) (crypto.KeyPair, string) {
	t.Helper()
	kp, err := crypto.Generate()
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	d, _ := did.EncodeKey(kp.Public)
	return kp, d
}

func TestIssueVerifyCredentialRoundtrip(t *testing.T) {
	issKP, issuer, kid := issuerSetup(t)
	_, subject := subjectSetup(t)

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	in := Credential{
		Issuer:      issuer,
		Subject:     subject,
		JTI:         "urn:uuid:5b7c1c4a-0001",
		IssuedAt:    now,
		Expires:     now.Add(30 * 24 * time.Hour),
		Level:       LevelL1,
		SubjectType: SubjectPerson,
		Status: &Status{
			StatusListIndex:      42,
			StatusListCredential: "https://sca.example/status/2026-q3",
		},
	}
	jwt, err := IssueCredential(issKP, in, IssueOptions{IssuerKeyID: kid})
	if err != nil {
		t.Fatalf("IssueCredential: %v", err)
	}

	out, err := VerifyCredential(context.Background(), did.NewKeyResolver(), jwt, now)
	if err != nil {
		t.Fatalf("VerifyCredential: %v", err)
	}
	if out.Issuer != issuer || out.Subject != subject || out.Level != LevelL1 || out.SubjectType != SubjectPerson {
		t.Fatalf("roundtrip mismatch: %+v", out)
	}
	if out.Status == nil || out.Status.StatusListIndex != 42 {
		t.Fatalf("status roundtrip mismatch: %+v", out.Status)
	}
}

func TestIssueRejectsLifetimeOverCap(t *testing.T) {
	issKP, issuer, kid := issuerSetup(t)
	_, subject := subjectSetup(t)
	now := time.Now().UTC()
	in := Credential{
		Issuer: issuer, Subject: subject, JTI: "urn:uuid:x",
		IssuedAt: now, Expires: now.Add(120 * 24 * time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
	}
	if _, err := IssueCredential(issKP, in, IssueOptions{IssuerKeyID: kid}); err == nil {
		t.Fatalf("expected lifetime-cap error")
	}
}

func TestIssueRejectsOrgWithoutDIDWeb(t *testing.T) {
	issKP, issuer, kid := issuerSetup(t)
	_, subject := subjectSetup(t) // did:key
	now := time.Now().UTC()
	in := Credential{
		Issuer: issuer, Subject: subject, JTI: "urn:uuid:x",
		IssuedAt: now, Expires: now.Add(time.Hour),
		Level: LevelO1, SubjectType: SubjectOrganization,
	}
	if _, err := IssueCredential(issKP, in, IssueOptions{IssuerKeyID: kid}); err == nil {
		t.Fatalf("expected error: org subject must be did:web")
	}
}

func TestVerifyExpiredCredential(t *testing.T) {
	issKP, issuer, kid := issuerSetup(t)
	_, subject := subjectSetup(t)
	iat := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	in := Credential{
		Issuer: issuer, Subject: subject, JTI: "urn:uuid:exp",
		IssuedAt: iat, Expires: iat.Add(time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
	}
	jwt, err := IssueCredential(issKP, in, IssueOptions{IssuerKeyID: kid})
	if err != nil {
		t.Fatalf("IssueCredential: %v", err)
	}
	_, err = VerifyCredential(context.Background(), did.NewKeyResolver(), jwt, iat.Add(2*time.Hour))
	if err == nil {
		t.Fatalf("expected expiration error")
	}
}

func TestVerifyTamperedSignature(t *testing.T) {
	issKP, issuer, kid := issuerSetup(t)
	_, subject := subjectSetup(t)
	now := time.Now().UTC()
	in := Credential{
		Issuer: issuer, Subject: subject, JTI: "urn:uuid:t",
		IssuedAt: now, Expires: now.Add(time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
	}
	jwt, _ := IssueCredential(issKP, in, IssueOptions{IssuerKeyID: kid})

	// Flip a byte in the signature segment.
	parts := strings.Split(jwt, ".")
	if len(parts) != 3 {
		t.Fatalf("expected 3 parts, got %d", len(parts))
	}
	tampered := parts[0] + "." + parts[1] + "." + flipChar(parts[2])
	if _, err := VerifyCredential(context.Background(), did.NewKeyResolver(), tampered, now); err == nil {
		t.Fatalf("expected signature failure")
	}
}

func flipChar(s string) string {
	if s == "" {
		return s
	}
	b := []byte(s)
	if b[0] == 'A' {
		b[0] = 'B'
	} else {
		b[0] = 'A'
	}
	return string(b)
}
