// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/go/pkg/did"
)

type fakeStatus struct {
	list *StatusList
	url  string
}

func (f *fakeStatus) Fetch(_ context.Context, url string) (*StatusList, error) {
	if url != f.url {
		return nil, errors.New("unexpected status list URL: " + url)
	}
	return f.list, nil
}

type vpFixture struct {
	issuerKP   crypto.KeyPair
	issuer     string
	issuerKID  string
	holderKP   crypto.KeyPair
	holder     string
	holderKID  string
	verifier   string
	now        time.Time
	resolver   did.Resolver
	trustStore *MemoryTrustStore
}

func newVPFixture(t *testing.T) *vpFixture {
	t.Helper()
	issKP, issuer, issKID := issuerSetup(t)
	holderKP, holder := subjectSetup(t)
	holderKID := holder + "#" + strings.TrimPrefix(holder, "did:key:")
	verifier, _ := did.EncodeKey(mustGenerate(t).Public)
	return &vpFixture{
		issuerKP:  issKP,
		issuer:    issuer,
		issuerKID: issKID,
		holderKP:  holderKP,
		holder:    holder,
		holderKID: holderKID,
		verifier:  verifier,
		now:       time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC),
		resolver:  did.NewKeyResolver(),
		trustStore: NewMemoryTrustStore([]TrustEntry{{
			Issuer:         issuer,
			AcceptedLevels: []string{LevelL1, LevelL2},
		}}),
	}
}

func mustGenerate(t *testing.T) crypto.KeyPair {
	t.Helper()
	kp, err := crypto.Generate()
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	return kp
}

func (f *vpFixture) issueCred(t *testing.T, level, jti string, lifetime time.Duration) string {
	t.Helper()
	jwt, err := IssueCredential(f.issuerKP, Credential{
		Issuer: f.issuer, Subject: f.holder, JTI: jti,
		IssuedAt: f.now, Expires: f.now.Add(lifetime),
		Level: level, SubjectType: SubjectPerson,
	}, IssueOptions{IssuerKeyID: f.issuerKID})
	if err != nil {
		t.Fatalf("IssueCredential: %v", err)
	}
	return jwt
}

func (f *vpFixture) issueFreshness(t *testing.T, jti string, iat time.Time, lifetime time.Duration) string {
	t.Helper()
	jwt, err := IssueFreshness(f.issuerKP, f.issuer, f.issuerKID, jti, iat, iat.Add(lifetime))
	if err != nil {
		t.Fatalf("IssueFreshness: %v", err)
	}
	return jwt
}

func (f *vpFixture) presentVPAt(t *testing.T, jws []string, nonce string, iat time.Time) string {
	t.Helper()
	vp, err := IssuePresentation(f.holderKP, f.holder, f.holderKID, f.verifier, nonce, jws, iat, iat.Add(60*time.Second))
	if err != nil {
		t.Fatalf("IssuePresentation: %v", err)
	}
	return vp
}

func TestVerifierHappyPathWithinWindow(t *testing.T) {
	f := newVPFixture(t)
	cred := f.issueCred(t, LevelL2, "urn:uuid:1", 30*24*time.Hour)
	tNow := f.now.Add(time.Hour) // well within 24h freshness window
	vp := f.presentVPAt(t, []string{cred}, "nonce-1", tNow)

	v := &Verifier{
		Resolver:   f.resolver,
		TrustStore: f.trustStore,
		Now:        func() time.Time { return tNow },
	}
	out, err := v.VerifyPresentation(context.Background(), vp, f.verifier, "nonce-1")
	if err != nil {
		t.Fatalf("VerifyPresentation: %v", err)
	}
	if len(out.Credentials) != 1 || out.Credentials[0].Credential.Level != LevelL2 {
		t.Fatalf("unexpected credentials: %+v", out.Credentials)
	}

	if err := out.EvaluatePredicate(mustParsePredicate(t, `{"level":"urn:shadownet:level:L2"}`)); err != nil {
		t.Fatalf("predicate L2 should match: %v", err)
	}
	if err := out.EvaluatePredicate(mustParsePredicate(t, `{"level":"urn:shadownet:level:L3"}`)); err == nil {
		t.Fatalf("predicate L3 should fail")
	}
}

func TestVerifierFreshnessRequiredAfterWindow(t *testing.T) {
	f := newVPFixture(t)
	cred := f.issueCred(t, LevelL1, "urn:uuid:2", 30*24*time.Hour)
	// VP minted 30h after iat → past 24h freshness window.
	tNow := f.now.Add(30 * time.Hour)
	v := &Verifier{
		Resolver:   f.resolver,
		TrustStore: f.trustStore,
		Now:        func() time.Time { return tNow },
	}
	// Without freshness: must fail.
	{
		vp, err := IssuePresentation(f.holderKP, f.holder, f.holderKID, f.verifier, "n", []string{cred}, tNow, tNow.Add(60*time.Second))
		if err != nil {
			t.Fatalf("IssuePresentation: %v", err)
		}
		_, err = v.VerifyPresentation(context.Background(), vp, f.verifier, "n")
		var verr *Error
		if !errors.As(err, &verr) || verr.Code != ReasonFreshnessStale {
			t.Fatalf("expected freshness_stale, got %v", err)
		}
	}
	// With freshness proof issued just now: must succeed.
	{
		fresh := f.issueFreshness(t, "urn:uuid:2", tNow, 12*time.Hour)
		vp, err := IssuePresentation(f.holderKP, f.holder, f.holderKID, f.verifier, "n", []string{cred, fresh}, tNow, tNow.Add(60*time.Second))
		if err != nil {
			t.Fatalf("IssuePresentation: %v", err)
		}
		out, err := v.VerifyPresentation(context.Background(), vp, f.verifier, "n")
		if err != nil {
			t.Fatalf("VerifyPresentation: %v", err)
		}
		if out.Credentials[0].Freshness == nil {
			t.Fatalf("expected attached freshness proof")
		}
	}
}

func TestVerifierUntrustedIssuerSilentlyFiltered(t *testing.T) {
	f := newVPFixture(t)
	// Use a different issuer not in the trust store.
	otherKP := mustGenerate(t)
	otherIssuer, _ := did.EncodeKey(otherKP.Public)
	otherKID := otherIssuer + "#" + strings.TrimPrefix(otherIssuer, "did:key:")
	cred, err := IssueCredential(otherKP, Credential{
		Issuer: otherIssuer, Subject: f.holder, JTI: "urn:uuid:other",
		IssuedAt: f.now, Expires: f.now.Add(time.Hour),
		Level: LevelL2, SubjectType: SubjectPerson,
	}, IssueOptions{IssuerKeyID: otherKID})
	if err != nil {
		t.Fatalf("IssueCredential: %v", err)
	}
	tNow := f.now.Add(time.Minute)
	vp := f.presentVPAt(t, []string{cred}, "n", tNow)

	v := &Verifier{Resolver: f.resolver, TrustStore: f.trustStore, Now: func() time.Time { return tNow }}
	out, err := v.VerifyPresentation(context.Background(), vp, f.verifier, "n")
	if err != nil {
		t.Fatalf("VerifyPresentation: %v", err)
	}
	if len(out.Credentials) != 0 {
		t.Fatalf("untrusted credentials should be filtered, got %d", len(out.Credentials))
	}
	err = out.EvaluatePredicate(mustParsePredicate(t, `{"level":"urn:shadownet:level:L2"}`))
	var verr *Error
	if !errors.As(err, &verr) || verr.Code != ReasonLevelInsufficient {
		t.Fatalf("expected level_insufficient, got %v", err)
	}
}

func TestVerifierRevoked(t *testing.T) {
	f := newVPFixture(t)
	statusURL := "https://sca.example/status/2026-q3"
	cred, err := IssueCredential(f.issuerKP, Credential{
		Issuer: f.issuer, Subject: f.holder, JTI: "urn:uuid:rev",
		IssuedAt: f.now, Expires: f.now.Add(time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
		Status: &Status{StatusListIndex: 5, StatusListCredential: statusURL},
	}, IssueOptions{IssuerKeyID: f.issuerKID})
	if err != nil {
		t.Fatalf("IssueCredential: %v", err)
	}
	tNow := f.now.Add(time.Minute)
	vp := f.presentVPAt(t, []string{cred}, "n", tNow)

	list := NewStatusList(64)
	if err := list.Set(5, true); err != nil {
		t.Fatalf("Set: %v", err)
	}
	v := &Verifier{
		Resolver:      f.resolver,
		TrustStore:    f.trustStore,
		StatusFetcher: &fakeStatus{list: list, url: statusURL},
		Now:           func() time.Time { return tNow },
	}
	_, err = v.VerifyPresentation(context.Background(), vp, f.verifier, "n")
	var verr *Error
	if !errors.As(err, &verr) || verr.Code != ReasonRevoked {
		t.Fatalf("expected revoked, got %v", err)
	}
}

func TestVerifierStatusFetcherMissing(t *testing.T) {
	f := newVPFixture(t)
	cred, _ := IssueCredential(f.issuerKP, Credential{
		Issuer: f.issuer, Subject: f.holder, JTI: "urn:uuid:nostat",
		IssuedAt: f.now, Expires: f.now.Add(time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
		Status: &Status{StatusListIndex: 0, StatusListCredential: "https://x"},
	}, IssueOptions{IssuerKeyID: f.issuerKID})
	tNow := f.now.Add(time.Minute)
	vp := f.presentVPAt(t, []string{cred}, "n", tNow)
	v := &Verifier{Resolver: f.resolver, TrustStore: f.trustStore, Now: func() time.Time { return tNow }}
	_, err := v.VerifyPresentation(context.Background(), vp, f.verifier, "n")
	var verr *Error
	if !errors.As(err, &verr) || verr.Code != ReasonRevoked {
		t.Fatalf("expected revoked when StatusFetcher missing, got %v", err)
	}
}

func TestVerifierWrongHolder(t *testing.T) {
	f := newVPFixture(t)
	// Issue a credential whose subject is some OTHER did:key, then have f.holder
	// (different keys) sign a VP that bundles it. The holder == subject check
	// must reject this.
	otherKP, otherDID := subjectSetup(t)
	cred, err := IssueCredential(f.issuerKP, Credential{
		Issuer: f.issuer, Subject: otherDID, JTI: "urn:uuid:other-sub",
		IssuedAt: f.now, Expires: f.now.Add(time.Hour),
		Level: LevelL1, SubjectType: SubjectPerson,
	}, IssueOptions{IssuerKeyID: f.issuerKID})
	if err != nil {
		t.Fatalf("IssueCredential: %v", err)
	}
	_ = otherKP

	vp, err := IssuePresentation(f.holderKP, f.holder, f.holderKID, f.verifier, "n", []string{cred}, f.now, f.now.Add(60*time.Second))
	if err != nil {
		t.Fatalf("IssuePresentation: %v", err)
	}
	v := &Verifier{Resolver: f.resolver, TrustStore: f.trustStore, Now: func() time.Time { return f.now.Add(time.Minute) }}
	_, err = v.VerifyPresentation(context.Background(), vp, f.verifier, "n")
	var verr *Error
	if !errors.As(err, &verr) || verr.Code != ReasonPresentationInvalid {
		t.Fatalf("expected presentation_invalid, got %v", err)
	}
}
