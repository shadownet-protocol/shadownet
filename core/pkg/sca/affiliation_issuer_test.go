// SPDX-License-Identifier: MIT

package sca_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
	"github.com/shadownet-protocol/shadownet/core/pkg/sca"
	"github.com/shadownet-protocol/shadownet/core/pkg/storemem"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

type affResolver struct {
	docs map[string]*did.Document
}

func (r *affResolver) Resolve(ctx context.Context, d string) (*did.Document, error) {
	if doc, ok := r.docs[d]; ok {
		return doc, nil
	}
	return did.NewKeyResolver().Resolve(ctx, d)
}

type affFixture struct {
	t       *testing.T
	issuer  *sca.Issuer
	server  *httptest.Server
	subject crypto.KeyPair
	subjDID string
	subjKID string
	orgDID  string
	now     time.Time
}

func newAffFixture(t *testing.T, mode sca.Mode) *affFixture {
	t.Helper()
	orgDID := "did:web:acme.example"
	orgKID := orgDID + "#k1"
	orgKP, _ := crypto.Generate()

	subjKP, _ := crypto.Generate()
	subjDID, _ := did.EncodeKey(subjKP.Public)
	subjKID := subjDID + "#" + subjDID[len("did:key:"):]

	resolver := &affResolver{docs: map[string]*did.Document{
		orgDID: {
			ID: orgDID,
			VerificationMethod: []did.VerificationMethod{{
				ID: orgKID, Controller: orgDID, Public: orgKP.Public,
			}},
			Authentication:  []string{orgKID},
			AssertionMethod: []string{orgKID},
		},
	}}

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	policy := sca.Policy{
		Issuer:                            orgDID,
		Version:                           vc.Version,
		Mode:                              mode,
		AffiliationOrg:                    orgDID,
		AffiliationFreshnessWindowSeconds: 3600,
		AffiliationLifetimeDays:           7,
		AffiliationStatusListBase:         "http://acme.example/status/affiliation/",
		StatusListBase:                    "http://acme.example/status/",
	}
	issuer := &sca.Issuer{
		DID:                   orgDID,
		KeyID:                 orgKID,
		Key:                   orgKP,
		Resolver:              resolver,
		Issuance:              storemem.NewSCAIssuanceStore(),
		AffiliationRevocation: storemem.NewSCARevocationStore("aff"),
		Policy:                policy,
		Now:                   func() time.Time { return now },
	}
	if err := issuer.Validate(); err != nil {
		t.Fatalf("issuer.Validate: %v", err)
	}
	srv := httptest.NewServer(issuer.Handler())
	t.Cleanup(srv.Close)
	return &affFixture{
		t: t, issuer: issuer, server: srv,
		subject: subjKP, subjDID: subjDID, subjKID: subjKID,
		orgDID: orgDID, now: now,
	}
}

func (f *affFixture) authJWT() string {
	f.t.Helper()
	jwt, err := sca.IssueSubjectAuth(f.subject, f.subjDID, f.subjKID, f.issuer.DID, "aff-auth-1", f.now, f.now.Add(30*time.Second))
	if err != nil {
		f.t.Fatalf("IssueSubjectAuth: %v", err)
	}
	return jwt
}

func (f *affFixture) postAff(path string, body any) (*http.Response, []byte) {
	f.t.Helper()
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequest(http.MethodPost, f.server.URL+path, bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+f.authJWT())
	resp, err := f.server.Client().Do(req)
	if err != nil {
		f.t.Fatalf("POST %s: %v", path, err)
	}
	defer resp.Body.Close()
	out, _ := io.ReadAll(resp.Body)
	return resp, out
}

func TestAffiliationIssuanceEndToEnd(t *testing.T) {
	f := newAffFixture(t, sca.ModeAffiliation)

	resp, body := f.postAff("/issuance/affiliation", sca.AffiliationIssuanceRequest{
		Version:     vc.Version,
		Subject:     f.subjDID,
		Affiliation: f.orgDID,
		Role:        "member",
		Groups:      []string{"engineering"},
	})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/issuance/affiliation status %d: %s", resp.StatusCode, body)
	}
	var ir sca.AffiliationIssuanceResponse
	if err := json.Unmarshal(body, &ir); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if ir.Credential == "" {
		t.Fatalf("missing credential in response")
	}

	c, err := vc.VerifyAffiliationCredential(context.Background(), f.issuer.Resolver, ir.Credential, f.now.Add(time.Minute))
	if err != nil {
		t.Fatalf("VerifyAffiliationCredential: %v", err)
	}
	if c.Subject != f.subjDID || c.Affiliation != f.orgDID || c.Role != "member" {
		t.Fatalf("affiliation mismatch: %+v", c)
	}
	if c.Status == nil {
		t.Fatalf("affiliation must have status entry")
	}
}

func TestAffiliationIssuanceRejectsWhenModeIsPersonhood(t *testing.T) {
	f := newAffFixture(t, sca.ModeAffiliation)
	// Override mode to personhood to confirm the gate.
	f.issuer.Policy.Mode = sca.ModePersonhood
	// Re-Validate would fail (no Sessions/Revocation/Methods), but the mode
	// gate fires before Validate; we exercise the runtime path directly.
	resp, body := f.postAff("/issuance/affiliation", sca.AffiliationIssuanceRequest{
		Version:     vc.Version,
		Subject:     f.subjDID,
		Affiliation: f.orgDID,
	})
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("expected 403, got %d: %s", resp.StatusCode, body)
	}
	var er sca.ErrorBody
	_ = json.Unmarshal(body, &er)
	if er.Error != sca.CodeModeNotEnabled {
		t.Fatalf("error code = %q, want %q", er.Error, sca.CodeModeNotEnabled)
	}
}

func TestAffiliationIssuanceRejectsMismatchedOrg(t *testing.T) {
	f := newAffFixture(t, sca.ModeAffiliation)
	resp, body := f.postAff("/issuance/affiliation", sca.AffiliationIssuanceRequest{
		Version:     vc.Version,
		Subject:     f.subjDID,
		Affiliation: "did:web:other.example",
	})
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("expected 403 on org mismatch, got %d: %s", resp.StatusCode, body)
	}
	var er sca.ErrorBody
	_ = json.Unmarshal(body, &er)
	if er.Error != sca.CodeAffiliationOrg {
		t.Fatalf("error code = %q, want %q", er.Error, sca.CodeAffiliationOrg)
	}
}

func TestAffiliationFreshnessUsesAffiliationWindow(t *testing.T) {
	f := newAffFixture(t, sca.ModeAffiliation)
	// Issue an affiliation credential first.
	_, body := f.postAff("/issuance/affiliation", sca.AffiliationIssuanceRequest{
		Version: vc.Version, Subject: f.subjDID, Affiliation: f.orgDID,
	})
	var ir sca.AffiliationIssuanceResponse
	_ = json.Unmarshal(body, &ir)
	cred, _ := vc.VerifyAffiliationCredential(context.Background(), f.issuer.Resolver, ir.Credential, f.now.Add(time.Minute))

	// Request freshness; the proof's exp - iat must equal the affiliation
	// window (3600s in this fixture), not the personhood window.
	resp, freshBody := f.postAff("/freshness", sca.FreshnessRequest{
		Version:       vc.Version,
		CredentialJTI: cred.JTI,
	})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/freshness status %d: %s", resp.StatusCode, freshBody)
	}
	var fr sca.FreshnessResponse
	_ = json.Unmarshal(freshBody, &fr)
	parsedFresh, err := vc.VerifyFreshness(context.Background(), f.issuer.Resolver, fr.FreshnessProof, f.now.Add(time.Minute))
	if err != nil {
		t.Fatalf("VerifyFreshness: %v", err)
	}
	got := parsedFresh.Expires.Sub(parsedFresh.IssuedAt)
	if got != time.Hour {
		t.Errorf("affiliation freshness lifetime = %v, want %v", got, time.Hour)
	}
}
