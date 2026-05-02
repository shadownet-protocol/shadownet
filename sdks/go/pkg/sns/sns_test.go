// SPDX-License-Identifier: MIT

package sns_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet-go/internal/storemem"
	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
	"github.com/shadownet-protocol/shadownet-go/pkg/sns"
	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
)

func TestParseShadowname(t *testing.T) {
	cases := map[string]struct {
		ok    bool
		local string
		host  string
	}{
		"mahdi@shadownet.example":      {true, "mahdi", "shadownet.example"},
		"MAHDI@SHADOWNET.example":      {true, "mahdi", "SHADOWNET.example"},
		"a.b-c_d@x.example":            {true, "a.b-c_d", "x.example"},
		"@x.example":                   {false, "", ""},
		"x@":                           {false, "", ""},
		"x":                            {false, "", ""},
		"with space@x":                 {false, "", ""},
		strings.Repeat("a", 64) + "@x": {false, "", ""},
	}
	for in, want := range cases {
		got, err := sns.ParseShadowname(in)
		if want.ok && err != nil {
			t.Errorf("ParseShadowname(%q): unexpected error %v", in, err)
		}
		if !want.ok && err == nil {
			t.Errorf("ParseShadowname(%q): expected error, got %+v", in, got)
		}
		if want.ok && (got.Local != want.local || got.Provider != want.host) {
			t.Errorf("ParseShadowname(%q) = %+v, want {%q,%q}", in, got, want.local, want.host)
		}
	}
}

func TestRecordRoundtrip(t *testing.T) {
	provKP, _ := crypto.Generate()
	provDID, _ := did.EncodeKey(provKP.Public)
	provKID := provDID + "#" + strings.TrimPrefix(provDID, "did:key:")

	subjKP, _ := crypto.Generate()
	subjDID, _ := did.EncodeKey(subjKP.Public)
	pubJWK, _ := crypto.PublicJWK(subjKP.Public, "")

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	rec := sns.Record{
		Shadowname:  "alice@example.org",
		DID:         subjDID,
		Endpoint:    "https://shadow.example.org/u/alice/a2a",
		PublicKey:   pubJWK,
		SubjectType: vc.SubjectPerson,
		TTL:         300,
	}
	jwt, err := sns.IssueRecord(provKP, provDID, provKID, rec, now)
	if err != nil {
		t.Fatalf("IssueRecord: %v", err)
	}
	got, err := sns.VerifyRecord(context.Background(), did.NewKeyResolver(), jwt, "alice@example.org", now.Add(time.Minute))
	if err != nil {
		t.Fatalf("VerifyRecord: %v", err)
	}
	if got.Issuer != provDID || got.Record.DID != subjDID || got.Record.TTL != 300 {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}
}

func TestRecordTTLBounds(t *testing.T) {
	provKP, _ := crypto.Generate()
	provDID, _ := did.EncodeKey(provKP.Public)
	provKID := provDID + "#" + strings.TrimPrefix(provDID, "did:key:")
	subjKP, _ := crypto.Generate()
	subjDID, _ := did.EncodeKey(subjKP.Public)
	pubJWK, _ := crypto.PublicJWK(subjKP.Public, "")
	now := time.Now().UTC()
	for _, ttl := range []int{30, 0, 90000} {
		_, err := sns.IssueRecord(provKP, provDID, provKID, sns.Record{
			Shadowname: "x@y", DID: subjDID, Endpoint: "https://y/a", PublicKey: pubJWK,
			SubjectType: vc.SubjectPerson, TTL: ttl,
		}, now)
		if err == nil {
			t.Fatalf("expected ttl bound error for %d", ttl)
		}
	}
}

func TestRecordSubMismatch(t *testing.T) {
	provKP, _ := crypto.Generate()
	provDID, _ := did.EncodeKey(provKP.Public)
	provKID := provDID + "#" + strings.TrimPrefix(provDID, "did:key:")
	subjKP, _ := crypto.Generate()
	subjDID, _ := did.EncodeKey(subjKP.Public)
	pubJWK, _ := crypto.PublicJWK(subjKP.Public, "")
	now := time.Now().UTC()
	jwt, _ := sns.IssueRecord(provKP, provDID, provKID, sns.Record{
		Shadowname: "alice@example.org", DID: subjDID, Endpoint: "https://x/y",
		PublicKey: pubJWK, SubjectType: vc.SubjectPerson, TTL: 300,
	}, now)
	if _, err := sns.VerifyRecord(context.Background(), did.NewKeyResolver(), jwt, "bob@example.org", now); err == nil {
		t.Fatalf("expected sub-mismatch error")
	}
}

func TestSNSResolveAndUpdate(t *testing.T) {
	provKP, _ := crypto.Generate()
	provDID, _ := did.EncodeKey(provKP.Public)
	provKID := provDID + "#" + strings.TrimPrefix(provDID, "did:key:")

	subjKP, _ := crypto.Generate()
	subjDID, _ := did.EncodeKey(subjKP.Public)
	subjKID := subjDID + "#" + strings.TrimPrefix(subjDID, "did:key:")
	pubJWK, _ := crypto.PublicJWK(subjKP.Public, "")

	store := storemem.NewSNSRecordStore()
	server := &sns.Server{
		ProviderDID: provDID, ProviderKID: provKID, Key: provKP,
		Records:     store,
		DIDResolver: did.NewKeyResolver(),
		DefaultTTL:  300,
		Now:         func() time.Time { return time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC) },
	}
	if err := server.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	srv := httptest.NewServer(server.Handler())
	defer srv.Close()

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)

	// 1) PUT /v1/records/alice
	auth, err := sns.IssueSubjectAuth(subjKP, subjDID, subjKID, provDID, now, now.Add(30*time.Second))
	if err != nil {
		t.Fatalf("IssueSubjectAuth: %v", err)
	}
	body, _ := json.Marshal(sns.UpdateRequest{
		Version: sns.Version, DID: subjDID, Endpoint: "https://shadow.x/u/alice/a2a",
		PublicKey: pubJWK, SubjectType: vc.SubjectPerson, TTL: 300,
	})
	req, _ := http.NewRequest(http.MethodPut, srv.URL+"/v1/records/alice", strings.NewReader(string(body)))
	req.Header.Set("Authorization", "Bearer "+auth)
	req.Header.Set("Content-Type", "application/json")
	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("PUT: %v", err)
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		t.Fatalf("PUT status %d: %s", resp.StatusCode, raw)
	}
	resp.Body.Close()

	// 2) Resolve via the well-known endpoint
	resp, err = srv.Client().Get(srv.URL + sns.ResolvePath + "?name=alice@something")
	if err != nil {
		t.Fatalf("GET resolve: %v", err)
	}
	jwt, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("resolve status %d: %s", resp.StatusCode, jwt)
	}
	rec, err := sns.VerifyRecord(context.Background(), did.NewKeyResolver(), string(jwt), "alice@something", now.Add(30*time.Second))
	if err != nil {
		t.Fatalf("VerifyRecord: %v", err)
	}
	if rec.Record.DID != subjDID {
		t.Fatalf("rec.DID = %q, want %q", rec.Record.DID, subjDID)
	}

	// 3) DELETE → 204; resolve → 410
	auth2, _ := sns.IssueSubjectAuth(subjKP, subjDID, subjKID, provDID, now, now.Add(30*time.Second))
	dreq, _ := http.NewRequest(http.MethodDelete, srv.URL+"/v1/records/alice", nil)
	dreq.Header.Set("Authorization", "Bearer "+auth2)
	dresp, err := srv.Client().Do(dreq)
	if err != nil {
		t.Fatalf("DELETE: %v", err)
	}
	if dresp.StatusCode != http.StatusNoContent {
		raw, _ := io.ReadAll(dresp.Body)
		t.Fatalf("DELETE status %d: %s", dresp.StatusCode, raw)
	}
	dresp.Body.Close()

	resp, _ = srv.Client().Get(srv.URL + sns.ResolvePath + "?name=alice@something")
	if resp.StatusCode != http.StatusGone {
		t.Fatalf("after delete: status %d, want 410", resp.StatusCode)
	}
	resp.Body.Close()
}

func TestSNSResolverFlow(t *testing.T) {
	provKP, _ := crypto.Generate()
	provDID, _ := did.EncodeKey(provKP.Public)
	provKID := provDID + "#" + strings.TrimPrefix(provDID, "did:key:")
	subjKP, _ := crypto.Generate()
	subjDID, _ := did.EncodeKey(subjKP.Public)
	pubJWK, _ := crypto.PublicJWK(subjKP.Public, "")

	store := storemem.NewSNSRecordStore()
	_ = store.Put(context.Background(), sns.Record{
		Shadowname: "carol@example.org", DID: subjDID, Endpoint: "https://x/u/carol/a2a",
		PublicKey: pubJWK, SubjectType: vc.SubjectPerson, TTL: 300, IssuedAt: time.Now(),
	})
	server := &sns.Server{
		ProviderDID: provDID, ProviderKID: provKID, Key: provKP,
		Records: store, DIDResolver: did.NewKeyResolver(), DefaultTTL: 300,
	}
	srv := httptest.NewServer(server.Handler())
	defer srv.Close()

	// Build a Resolver pointed at the test server's host. Since ParseShadowname
	// extracts provider as the part after '@', we'd ordinarily DNS-resolve
	// that host. For the test we use a custom http.Client whose RoundTripper
	// rewrites the request URL to the test server.
	rewriter := &rewritingTransport{base: srv.Client().Transport, target: srv.URL}
	res := sns.NewResolver(did.NewKeyResolver())
	res.Client = &http.Client{Transport: rewriter}

	rec, err := res.Resolve(context.Background(), "carol@example.org")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if rec.Record.DID != subjDID {
		t.Fatalf("DID mismatch")
	}

	// Negative caching: unknown name returns 404, second call hits cache.
	if _, err := res.Resolve(context.Background(), "ghost@example.org"); !errors.Is(err, sns.ErrRecordNotFound) {
		t.Fatalf("expected ErrRecordNotFound, got %v", err)
	}
	rewriter.calls = 0
	_, _ = res.Resolve(context.Background(), "ghost@example.org")
	if rewriter.calls != 0 {
		t.Fatalf("expected cache hit, but %d upstream calls happened", rewriter.calls)
	}
}

// rewritingTransport rewrites URLs so a Resolver that wants to talk to
// "https://example.org/.well-known/sns/v1/resolve?name=..." instead hits a
// local httptest.Server.
type rewritingTransport struct {
	base   http.RoundTripper
	target string
	calls  int
}

func (t *rewritingTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	t.calls++
	rebuilt := *req
	cloneURL := *req.URL
	// target is e.g. "https://127.0.0.1:12345"
	prefix := strings.TrimPrefix(t.target, "https://")
	cloneURL.Host = prefix
	cloneURL.Scheme = "https"
	rebuilt.URL = &cloneURL
	rebuilt.Host = ""
	if t.base == nil {
		t.base = http.DefaultTransport
	}
	return t.base.RoundTrip(&rebuilt)
}
