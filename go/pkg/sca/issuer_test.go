// SPDX-License-Identifier: MIT

package sca_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/go/pkg/did"
	"github.com/shadownet-protocol/shadownet/go/pkg/sca"
	"github.com/shadownet-protocol/shadownet/go/pkg/storemem"
	"github.com/shadownet-protocol/shadownet/go/pkg/vc"
)

// instantMethod is a synchronous ProofMethod for tests: every Start returns
// readyAt = now. cmd/sca-server ships the production version.
type instantMethod struct{ name string }

func (m instantMethod) Name() string { return m.name }
func (m instantMethod) Start(_ context.Context, _ sca.Session) (sca.NextStep, *time.Time, error) {
	now := time.Now().UTC()
	return sca.NextStep{Kind: sca.StepInPerson, TTL: 60}, &now, nil
}

type fixture struct {
	t       *testing.T
	issuer  *sca.Issuer
	server  *httptest.Server
	subject crypto.KeyPair
	subjDID string
	subjKID string
	now     func() time.Time
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	scaKP, err := crypto.Generate()
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	scaDID, _ := did.EncodeKey(scaKP.Public)
	scaKID := scaDID + "#" + strings.TrimPrefix(scaDID, "did:key:")

	subjKP, err := crypto.Generate()
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	subjDID, _ := did.EncodeKey(subjKP.Public)
	subjKID := subjDID + "#" + strings.TrimPrefix(subjDID, "did:key:")

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	policy := sca.Policy{
		Issuer:                 scaDID,
		Version:                vc.Version,
		FreshnessWindowSeconds: 86400,
		StatusListBase:         "http://example/status/",
		Levels: []sca.LevelPolicy{{
			Level:                  vc.LevelL1,
			Method:                 "instant-approval",
			CredentialLifetimeDays: 90,
		}},
	}
	issuer := &sca.Issuer{
		DID:        scaDID,
		KeyID:      scaKID,
		Key:        scaKP,
		Resolver:   did.NewKeyResolver(),
		Sessions:   storemem.NewSCASessionStore(),
		Issuance:   storemem.NewSCAIssuanceStore(),
		Revocation: storemem.NewSCARevocationStore(sca.DefaultListID),
		Methods:    map[string]sca.ProofMethod{"instant-approval": instantMethod{name: "instant-approval"}},
		Policy:     policy,
		Now:        func() time.Time { return now },
	}
	if err := issuer.Validate(); err != nil {
		t.Fatalf("issuer.Validate: %v", err)
	}
	srv := httptest.NewServer(issuer.Handler())
	t.Cleanup(srv.Close)
	return &fixture{
		t: t, issuer: issuer, server: srv,
		subject: subjKP, subjDID: subjDID, subjKID: subjKID,
		now: func() time.Time { return now },
	}
}

func (f *fixture) authJWT() string {
	f.t.Helper()
	now := f.now()
	jwt, err := sca.IssueSubjectAuth(f.subject, f.subjDID, f.subjKID, f.issuer.DID, "auth-1", now, now.Add(30*time.Second))
	if err != nil {
		f.t.Fatalf("IssueSubjectAuth: %v", err)
	}
	return jwt
}

func (f *fixture) post(path string, body any) (*http.Response, []byte) {
	f.t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		f.t.Fatalf("marshal: %v", err)
	}
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

func TestSCAEndToEnd(t *testing.T) {
	f := newFixture(t)

	// 1) /proof/start
	resp, body := f.post("/proof/start", sca.ProofStartRequest{
		Version: vc.Version, Subject: f.subjDID, Level: vc.LevelL1,
	})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/proof/start status %d: %s", resp.StatusCode, body)
	}
	var ps sca.ProofStartResponse
	if err := json.Unmarshal(body, &ps); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if ps.SessionID == "" || ps.Method != "instant-approval" {
		t.Fatalf("unexpected /proof/start response: %+v", ps)
	}

	// 2) /proof/status — instant-approval should already be ready
	resp, body = f.post("/proof/status", sca.ProofStatusRequest{Version: vc.Version, SessionID: ps.SessionID})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/proof/status status %d: %s", resp.StatusCode, body)
	}
	var pst sca.ProofStatusResponse
	_ = json.Unmarshal(body, &pst)
	if pst.Status != string(sca.StateReady) {
		t.Fatalf("status = %q, want ready", pst.Status)
	}

	// 3) /issuance — submit CSR
	now := f.now()
	csr, err := sca.IssueCSR(f.subject, f.subjDID, f.subjKID, f.issuer.DID, vc.LevelL1, vc.SubjectPerson, now, now.Add(2*time.Minute))
	if err != nil {
		t.Fatalf("IssueCSR: %v", err)
	}
	resp, body = f.post("/issuance", sca.IssuanceRequest{Version: vc.Version, CSR: csr, SessionID: ps.SessionID})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/issuance status %d: %s", resp.StatusCode, body)
	}
	var ir sca.IssuanceResponse
	_ = json.Unmarshal(body, &ir)
	if ir.Credential == "" {
		t.Fatalf("missing credential in /issuance response")
	}

	// 4) Verify the credential against the issuer's DID.
	c, err := vc.VerifyCredential(context.Background(), did.NewKeyResolver(), ir.Credential, now.Add(time.Minute))
	if err != nil {
		t.Fatalf("VerifyCredential: %v", err)
	}
	if c.Subject != f.subjDID || c.Issuer != f.issuer.DID || c.Level != vc.LevelL1 {
		t.Fatalf("credential mismatch: %+v", c)
	}
	if c.Status == nil {
		t.Fatalf("credential missing status entry")
	}

	// 5) Re-using the same session must fail with session_consumed.
	resp, body = f.post("/issuance", sca.IssuanceRequest{Version: vc.Version, CSR: csr, SessionID: ps.SessionID})
	if resp.StatusCode != http.StatusGone {
		t.Fatalf("expected 410 on re-use, got %d: %s", resp.StatusCode, body)
	}
	var er sca.ErrorBody
	_ = json.Unmarshal(body, &er)
	if er.Error != sca.CodeSessionConsumed {
		t.Fatalf("error code = %q, want %q", er.Error, sca.CodeSessionConsumed)
	}

	// 6) /freshness
	resp, body = f.post("/freshness", sca.FreshnessRequest{Version: vc.Version, CredentialJTI: c.JTI})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/freshness status %d: %s", resp.StatusCode, body)
	}
	var fr sca.FreshnessResponse
	_ = json.Unmarshal(body, &fr)
	freshObj, err := vc.VerifyFreshness(context.Background(), did.NewKeyResolver(), fr.FreshnessProof, now.Add(time.Minute))
	if err != nil {
		t.Fatalf("VerifyFreshness: %v", err)
	}
	if freshObj.CredentialJTI != c.JTI {
		t.Fatalf("freshness jti mismatch")
	}

	// 7) /status/<list> — fetch and verify
	stResp, err := f.server.Client().Get(f.server.URL + "/status/" + sca.DefaultListID)
	if err != nil {
		t.Fatalf("GET /status: %v", err)
	}
	stBody, _ := io.ReadAll(stResp.Body)
	stResp.Body.Close()
	if stResp.StatusCode != http.StatusOK {
		t.Fatalf("/status/%s status %d: %s", sca.DefaultListID, stResp.StatusCode, stBody)
	}
	if stResp.Header.Get("Cache-Control") == "" {
		t.Fatalf("/status missing Cache-Control")
	}
	list, _, err := vc.VerifyStatusListCredential(context.Background(), did.NewKeyResolver(), string(stBody), now.Add(time.Minute))
	if err != nil {
		t.Fatalf("VerifyStatusListCredential: %v", err)
	}
	revoked, err := list.Get(c.Status.StatusListIndex)
	if err != nil || revoked {
		t.Fatalf("expected non-revoked at index %d", c.Status.StatusListIndex)
	}

	// 8) Revoke and re-fetch the status list.
	if err := f.issuer.Revoke(context.Background(), c.JTI); err != nil {
		t.Fatalf("Revoke: %v", err)
	}
	stResp, _ = f.server.Client().Get(f.server.URL + "/status/" + sca.DefaultListID)
	stBody, _ = io.ReadAll(stResp.Body)
	stResp.Body.Close()
	list, _, err = vc.VerifyStatusListCredential(context.Background(), did.NewKeyResolver(), string(stBody), now.Add(time.Minute))
	if err != nil {
		t.Fatalf("VerifyStatusListCredential post-revoke: %v", err)
	}
	revoked, _ = list.Get(c.Status.StatusListIndex)
	if !revoked {
		t.Fatalf("expected revoked bit set after Revoke")
	}

	// 9) Freshness must now return 403 revoked.
	resp, body = f.post("/freshness", sca.FreshnessRequest{Version: vc.Version, CredentialJTI: c.JTI})
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("expected 403 after revoke, got %d: %s", resp.StatusCode, body)
	}
	_ = json.Unmarshal(body, &er)
	if er.Error != sca.CodeRevoked {
		t.Fatalf("error code = %q, want %q", er.Error, sca.CodeRevoked)
	}
}

func TestSCAUnauthorizedEndpoints(t *testing.T) {
	f := newFixture(t)
	// /proof/start without Authorization
	req, _ := http.NewRequest(http.MethodPost, f.server.URL+"/proof/start", strings.NewReader(`{"shadownet:v":"0.1","subject":"x","level":"x"}`))
	req.Header.Set("Content-Type", "application/json")
	resp, err := f.server.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

func TestSCAPolicyAndDIDDocument(t *testing.T) {
	f := newFixture(t)
	resp, err := f.server.Client().Get(f.server.URL + "/.well-known/sca/policy.json")
	if err != nil {
		t.Fatalf("policy: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("policy status %d", resp.StatusCode)
	}
	var p sca.Policy
	_ = json.NewDecoder(resp.Body).Decode(&p)
	if p.Issuer != f.issuer.DID {
		t.Fatalf("policy issuer = %q", p.Issuer)
	}

	resp, err = f.server.Client().Get(f.server.URL + "/.well-known/did.json")
	if err != nil {
		t.Fatalf("did doc: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("did doc status %d", resp.StatusCode)
	}
	var raw map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&raw)
	if raw["id"] != f.issuer.DID {
		t.Fatalf("did doc id = %v", raw["id"])
	}
}

func TestSCAInvalidLevel(t *testing.T) {
	f := newFixture(t)
	resp, body := f.post("/proof/start", sca.ProofStartRequest{
		Version: vc.Version, Subject: f.subjDID, Level: "urn:shadownet:level:LX",
	})
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400; body=%s", resp.StatusCode, body)
	}
	var er sca.ErrorBody
	_ = json.Unmarshal(body, &er)
	if er.Error != sca.CodeInvalidLevel {
		t.Fatalf("error = %q, want %q", er.Error, sca.CodeInvalidLevel)
	}
}

func TestSCAFreshnessNotHolder(t *testing.T) {
	f := newFixture(t)
	// Issue a credential first.
	resp, body := f.post("/proof/start", sca.ProofStartRequest{Version: vc.Version, Subject: f.subjDID, Level: vc.LevelL1})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("proof/start: %d %s", resp.StatusCode, body)
	}
	var ps sca.ProofStartResponse
	_ = json.Unmarshal(body, &ps)
	now := f.now()
	csr, _ := sca.IssueCSR(f.subject, f.subjDID, f.subjKID, f.issuer.DID, vc.LevelL1, vc.SubjectPerson, now, now.Add(time.Minute))
	resp, body = f.post("/issuance", sca.IssuanceRequest{Version: vc.Version, CSR: csr, SessionID: ps.SessionID})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("issuance: %d %s", resp.StatusCode, body)
	}
	var ir sca.IssuanceResponse
	_ = json.Unmarshal(body, &ir)
	c, _ := vc.VerifyCredential(context.Background(), did.NewKeyResolver(), ir.Credential, now)

	// Now another keypair calls /freshness for the same jti.
	otherKP, _ := crypto.Generate()
	otherDID, _ := did.EncodeKey(otherKP.Public)
	otherKID := otherDID + "#" + strings.TrimPrefix(otherDID, "did:key:")
	auth, err := sca.IssueSubjectAuth(otherKP, otherDID, otherKID, f.issuer.DID, "auth-other", now, now.Add(30*time.Second))
	if err != nil {
		t.Fatalf("IssueSubjectAuth: %v", err)
	}
	req, _ := http.NewRequest(http.MethodPost, f.server.URL+"/freshness", bytes.NewReader([]byte(`{"shadownet:v":"0.1","credentialJti":"`+c.JTI+`"}`)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+auth)
	resp, err = f.server.Client().Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", resp.StatusCode)
	}
	rawBody, _ := io.ReadAll(resp.Body)
	var er sca.ErrorBody
	_ = json.Unmarshal(rawBody, &er)
	if er.Error != sca.CodeNotHolder {
		t.Fatalf("error = %q, want %q (body=%s)", er.Error, sca.CodeNotHolder, rawBody)
	}
}

// Ensure Issuer.Validate detects misconfiguration.
func TestIssuerValidate(t *testing.T) {
	if err := (&sca.Issuer{}).Validate(); err == nil {
		t.Fatalf("expected validation error on empty issuer")
	}
	// Sanity: a fully-wired one passes.
	if err := newFixture(t).issuer.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
}

// Ensure the public Error type unwraps.
func TestErrorUnwrap(t *testing.T) {
	cause := errors.New("root cause")
	e := sca.Wrap(http.StatusBadRequest, sca.CodeCSRInvalid, "boom", cause)
	if !errors.Is(e, cause) {
		t.Fatalf("errors.Is should chain to cause")
	}
}
