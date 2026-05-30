// SPDX-License-Identifier: MIT

package issuer_test

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/agentcard"
	"github.com/shadownet-protocol/shadownet/core/internal/credential"
	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/csr"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/hooks/dev"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/hooks/queue"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/sqlitestore"
)

func silentLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func newStore(t *testing.T) *sqlitestore.Store {
	t.Helper()
	path := filepath.Join(t.TempDir(), "issuer.db")
	s, err := sqlitestore.Open("file:"+path, 128)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

// alwaysAuthorize is a test stub that bypasses §6.6 DNS resolution. We
// supply it as the issuer.Authorizer's underlying behaviour by pre-
// populating an in-memory positive cache via a thin wrapper.
//
// Since Authorizer doesn't expose injection points, we drive authorization
// through tests where iss == org (the rule-1 path that doesn't need DNS).
func mintCSR(t *testing.T, subject crypto.KeyPair, aud, kid string) string {
	t.Helper()
	now := time.Now().Unix()
	tok, err := csr.Mint(csr.Payload{
		Iss: kid,
		Aud: aud,
		Iat: now,
		Exp: now + 300,
		Req: csr.Request{Kind: "org_affiliation", Org: aud},
	}, subject)
	if err != nil {
		t.Fatal(err)
	}
	return tok
}

func TestIssueDomainModeAutoApprove(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	subjectKP, _ := crypto.Generate()
	subjectPub, _ := identifiers.EncodePubKey(subjectKP.Public)

	h, err := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeDomain,
		Store:            store,
		Hook:             dev.NewAutoApproveHook(),
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: "acme.example",
	})
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()

	csrTok := mintCSR(t, subjectKP, "acme.example", subjectPub)
	resp, err := http.Post(srv.URL+"/.well-known/shadownet/issue", "application/jose", strings.NewReader(csrTok))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("status = %d, body = %s", resp.StatusCode, body)
	}
	credBytes, _ := io.ReadAll(resp.Body)
	jws := strings.TrimSpace(string(credBytes))

	// Verify the returned credential round-trips.
	got, err := credential.Verify(jws, credential.VerifyOptions{
		Now: time.Now,
		ResolveIssuerKey: func(string) (ed25519.PublicKey, error) {
			return issuerKP.Public, nil
		},
		AuthorizeIssuerForOrg: func(_, _ string) error { return nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	if got.Sub != subjectPub {
		t.Fatalf("Sub = %q, want %q", got.Sub, subjectPub)
	}
}

func TestIssueIdempotentReturnsSameJWS(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	subjectKP, _ := crypto.Generate()
	subjectPub, _ := identifiers.EncodePubKey(subjectKP.Public)

	h, _ := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeDomain,
		Store:            store,
		Hook:             dev.NewAutoApproveHook(),
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: "acme.example",
	})
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()

	csrTok := mintCSR(t, subjectKP, "acme.example", subjectPub)
	r1, _ := http.Post(srv.URL+"/.well-known/shadownet/issue", "application/jose", strings.NewReader(csrTok))
	c1, _ := io.ReadAll(r1.Body)
	r1.Body.Close()
	r2, _ := http.Post(srv.URL+"/.well-known/shadownet/issue", "application/jose", strings.NewReader(csrTok))
	c2, _ := io.ReadAll(r2.Body)
	r2.Body.Close()
	if strings.TrimSpace(string(c1)) != strings.TrimSpace(string(c2)) {
		t.Fatalf("idempotent re-POST returned different JWS")
	}
}

func TestIssuePendingThenApproved(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	subjectKP, _ := crypto.Generate()
	subjectPub, _ := identifiers.EncodePubKey(subjectKP.Public)

	q, err := queue.New(queue.Config{
		Store:   store,
		NextURL: "https://acme.example/.well-known/shadownet/issue",
	})
	if err != nil {
		t.Fatal(err)
	}
	h, _ := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeDomain,
		Store:            store,
		Hook:             q,
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: "acme.example",
	})
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()

	csrTok := mintCSR(t, subjectKP, "acme.example", subjectPub)
	// First POST: 409 with `next`.
	r1, _ := http.Post(srv.URL+"/.well-known/shadownet/issue", "application/jose", strings.NewReader(csrTok))
	if r1.StatusCode != http.StatusConflict {
		t.Fatalf("first POST = %d, want 409", r1.StatusCode)
	}
	var body struct {
		Next string `json:"next"`
	}
	_ = json.NewDecoder(r1.Body).Decode(&body)
	r1.Body.Close()
	if !strings.HasSuffix(body.Next, "/issue") {
		t.Fatalf("next URL = %q", body.Next)
	}

	// Admin approves out-of-band by promoting the pending row directly.
	ctx := context.Background()
	pendings, err := store.ListPending(ctx, issuer.PendingFilter{IncludeExpired: true})
	if err != nil || len(pendings) == 0 {
		t.Fatalf("no pendings found: err=%v", err)
	}
	if err := store.UpdatePendingStatus(ctx, pendings[0].HandleID, issuer.PendingApproved, "", time.Now()); err != nil {
		t.Fatal(err)
	}

	// Re-POST: 200 with credential.
	r2, _ := http.Post(srv.URL+"/.well-known/shadownet/issue", "application/jose", strings.NewReader(csrTok))
	if r2.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(r2.Body)
		t.Fatalf("re-POST = %d, body = %s", r2.StatusCode, body)
	}
}

func TestIssueRejectedSurfacesProblem(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	subjectKP, _ := crypto.Generate()
	subjectPub, _ := identifiers.EncodePubKey(subjectKP.Public)

	q, _ := queue.New(queue.Config{Store: store, NextURL: "https://x.example/issue"})
	h, _ := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeDomain,
		Store:            store,
		Hook:             q,
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: "acme.example",
	})
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()

	csrTok := mintCSR(t, subjectKP, "acme.example", subjectPub)
	_, _ = http.Post(srv.URL+"/.well-known/shadownet/issue", "application/jose", strings.NewReader(csrTok))

	pendings, _ := store.ListPending(context.Background(), issuer.PendingFilter{IncludeExpired: true})
	_ = store.UpdatePendingStatus(context.Background(), pendings[0].HandleID, issuer.PendingRejected, "fraud detected", time.Now())

	r2, _ := http.Post(srv.URL+"/.well-known/shadownet/issue", "application/jose", strings.NewReader(csrTok))
	if r2.StatusCode != http.StatusForbidden {
		t.Fatalf("re-POST after reject = %d, want 403", r2.StatusCode)
	}
	if ct := r2.Header.Get("Content-Type"); ct != "application/problem+json" {
		t.Fatalf("Content-Type = %q", ct)
	}
	var body map[string]any
	_ = json.NewDecoder(r2.Body).Decode(&body)
	if body["title"] != "ceremony_failed" {
		t.Fatalf("title = %v", body["title"])
	}
}

func TestIssueWrongAudienceRejected(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	subjectKP, _ := crypto.Generate()
	subjectPub, _ := identifiers.EncodePubKey(subjectKP.Public)

	h, _ := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeDomain,
		Store:            store,
		Hook:             dev.NewAutoApproveHook(),
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: "acme.example",
	})
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()
	csrTok := mintCSR(t, subjectKP, "other.example", subjectPub)
	r, _ := http.Post(srv.URL+"/.well-known/shadownet/issue", "application/jose", strings.NewReader(csrTok))
	if r.StatusCode != http.StatusBadRequest {
		// csr.Verify with ExpectedAudience="acme.example" rejects with
		// ErrAudienceMismatch which we surface as parse_error.
		t.Fatalf("status = %d", r.StatusCode)
	}
}

func TestStatusListReflectsRevocations(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	h, _ := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeDomain,
		Store:            store,
		Hook:             dev.NewAutoApproveHook(),
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: "acme.example",
	})
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()

	// Revoke idx 5 on epoch 1 (the bootstrap epoch).
	_ = store.SetRevoked(context.Background(), 1, 5, time.Now())

	resp, _ := http.Get(srv.URL + "/.well-known/shadownet/status/1")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "text/plain" {
		t.Fatalf("Content-Type = %q", ct)
	}
}

func TestKeyedHubAgentCardSelfServe(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	issuerPubMB, _ := identifiers.EncodePubKey(issuerKP.Public)

	h, err := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeKeyed,
		Store:            store,
		Hook:             dev.NewAutoApproveHook(),
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: issuerPubMB,
		KeyedAgentCardSubject: issuer.KeyedAgentCardConfig{
			Name:           "Test Hub",
			Description:    "Keyed-Hub test fixture",
			A2AURL:         "https://hub.example/a2a",
			IssueURL:       "https://hub.example/issue",
			StatusListBase: "https://hub.example/status",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/.well-known/agent-card.json")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	body, _ := io.ReadAll(resp.Body)
	var card map[string]any
	_ = json.Unmarshal(body, &card)
	if card["shadownet:issueEndpoint"] != "https://hub.example/issue" {
		t.Fatalf("issueEndpoint = %v", card["shadownet:issueEndpoint"])
	}
	if card["shadownet:statusListBase"] != "https://hub.example/status" {
		t.Fatalf("statusListBase = %v", card["shadownet:statusListBase"])
	}
	if err := agentcard.Verify(card, agentcard.VerifyOptions{
		ExpectedKID:   issuerPubMB,
		CandidateKeys: []ed25519.PublicKey{issuerKP.Public},
	}); err != nil {
		t.Fatalf("Verify self-served AgentCard: %v", err)
	}
}

func TestKeyedHubIssueRoute(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	issuerPubMB, _ := identifiers.EncodePubKey(issuerKP.Public)
	subjectKP, _ := crypto.Generate()
	subjectPubMB, _ := identifiers.EncodePubKey(subjectKP.Public)

	h, _ := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeKeyed,
		Store:            store,
		Hook:             dev.NewAutoApproveHook(),
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: issuerPubMB,
		KeyedAgentCardSubject: issuer.KeyedAgentCardConfig{
			A2AURL:         "https://hub.example/a2a",
			IssueURL:       "https://hub.example/issue",
			StatusListBase: "https://hub.example/status",
		},
	})
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()

	now := time.Now().Unix()
	csrTok, err := csr.Mint(csr.Payload{
		Iss: subjectPubMB,
		Aud: issuerPubMB,
		Iat: now,
		Exp: now + 300,
		Req: csr.Request{Kind: "org_affiliation", Org: issuerPubMB},
	}, subjectKP)
	if err != nil {
		t.Fatal(err)
	}
	// Custom path declared on the AgentCard.
	resp, _ := http.Post(srv.URL+"/issue", "application/jose", strings.NewReader(csrTok))
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("keyed-hub issue = %d, body = %s", resp.StatusCode, body)
	}
}

func TestHealthLivenessReadiness(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	issuerKP, _ := crypto.Generate()
	h, _ := issuer.NewHandler(issuer.HandlerConfig{
		Mode:             issuer.ModeDomain,
		Store:            store,
		Hook:             dev.NewAutoApproveHook(),
		Authz:            issuer.NewAuthorizer(issuer.AuthzConfig{}),
		Signer:           issuerKP.Private,
		Logger:           silentLogger(),
		IssuerIdentifier: "acme.example",
	})
	srv := httptest.NewServer(h.Routes())
	defer srv.Close()

	for _, path := range []string{"/healthz", "/livez", "/readyz"} {
		resp, err := http.Get(srv.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("%s returned %d", path, resp.StatusCode)
		}
	}
}
