// SPDX-License-Identifier: MIT

package provider_test

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/shadownet-protocol/shadownet/core/internal/agentcard"
	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/provider"
	"github.com/shadownet-protocol/shadownet/core/internal/provider/sqlitestore"
)

func newSilentLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// newServer builds an httptest.Server backed by an in-memory SQLite store
// with one Record pre-registered for the "alice" local. Returns the test
// server, the provider's signing public key, the shadow's public key, and
// a teardown function.
func newServer(t *testing.T) (server *httptest.Server, providerPub, shadowPub ed25519.PublicKey, shadowMB string, teardown func()) {
	t.Helper()
	providerKP, err := crypto.Generate()
	if err != nil {
		t.Fatal(err)
	}
	shadowKP, err := crypto.Generate()
	if err != nil {
		t.Fatal(err)
	}
	shadowMB, err = identifiers.EncodePubKey(shadowKP.Public)
	if err != nil {
		t.Fatal(err)
	}
	store, err := sqlitestore.Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	if err := store.PutRecord(context.Background(), provider.Record{
		Local:           "alice",
		ShadowPublicKey: shadowMB,
		A2AURL:          "https://shadow.example.com/v1/a2a/alice",
		DisplayName:     "Alice",
	}); err != nil {
		t.Fatal(err)
	}
	h := &provider.Handler{
		Store:          store,
		Signer:         providerKP.Private,
		ProviderDomain: "sh4dow.org",
		CacheMaxAge:    3600,
		Logger:         newSilentLogger(),
	}
	srv := httptest.NewServer(h.Routes())
	teardown = func() {
		srv.Close()
		_ = store.Close()
	}
	return srv, providerKP.Public, shadowKP.Public, shadowMB, teardown
}

func TestServeIdentity(t *testing.T) {
	t.Parallel()
	srv, providerPub, _, shadowMB, teardown := newServer(t)
	defer teardown()

	resp, err := http.Get(srv.URL + "/identity/alice")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "application/a2a+json" {
		t.Fatalf("Content-Type = %q", ct)
	}
	if cache := resp.Header.Get("Cache-Control"); !strings.Contains(cache, "max-age=3600") {
		t.Fatalf("Cache-Control = %q", cache)
	}
	if etag := resp.Header.Get("ETag"); etag == "" {
		t.Fatal("ETag missing")
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	var card map[string]any
	if err := json.Unmarshal(body, &card); err != nil {
		t.Fatal(err)
	}
	if card["shadownet:pk"] != shadowMB {
		t.Fatalf("shadownet:pk = %v", card["shadownet:pk"])
	}

	// Cross-check via the verifier package — proves the signed bytes
	// produced by the Provider validate end-to-end.
	if err := agentcard.Verify(card, agentcard.VerifyOptions{
		ExpectedKID:   "shadownet@sh4dow.org",
		CandidateKeys: []ed25519.PublicKey{providerPub},
	}); err != nil {
		t.Fatalf("Verify: %v", err)
	}
}

func TestServeIdentityCaseInsensitive(t *testing.T) {
	t.Parallel()
	srv, _, _, _, teardown := newServer(t)
	defer teardown()

	resp, err := http.Get(srv.URL + "/identity/ALICE")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}

func TestServeIdentity404(t *testing.T) {
	t.Parallel()
	srv, _, _, _, teardown := newServer(t)
	defer teardown()

	resp, err := http.Get(srv.URL + "/identity/missing")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}

func TestServeIdentityIfNoneMatch(t *testing.T) {
	t.Parallel()
	srv, _, _, _, teardown := newServer(t)
	defer teardown()

	resp1, err := http.Get(srv.URL + "/identity/alice")
	if err != nil {
		t.Fatal(err)
	}
	resp1.Body.Close()
	etag := resp1.Header.Get("ETag")
	if etag == "" {
		t.Fatal("expected ETag on first response")
	}

	req, _ := http.NewRequest(http.MethodGet, srv.URL+"/identity/alice", nil)
	req.Header.Set("If-None-Match", etag)
	resp2, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != http.StatusNotModified {
		t.Fatalf("status = %d (expected 304)", resp2.StatusCode)
	}
}

func TestHealthLivenessReadiness(t *testing.T) {
	t.Parallel()
	srv, _, _, _, teardown := newServer(t)
	defer teardown()

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

func TestTXTRecord(t *testing.T) {
	t.Parallel()
	kp, err := crypto.Generate()
	if err != nil {
		t.Fatal(err)
	}
	txt, err := provider.TXTRecord("https://shadow.sh4dow.org/v1", []ed25519.PublicKey{kp.Public})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(txt, "v=0.2;") {
		t.Fatalf("TXT must start with v=0.2;: %q", txt)
	}
	if !strings.Contains(txt, "ep=https://shadow.sh4dow.org/v1;") {
		t.Fatalf("ep= missing or wrong: %q", txt)
	}
	if !strings.Contains(txt, "pk=z6Mk") {
		t.Fatalf("pk=z6Mk missing: %q", txt)
	}
}

func TestTXTRecordWithIssuer(t *testing.T) {
	t.Parallel()
	kp, _ := crypto.Generate()
	txt, err := provider.TXTRecord("https://x.example", []ed25519.PublicKey{kp.Public}, "iss=true")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(txt, "iss=true") {
		t.Fatalf("iss=true should append: %q", txt)
	}
}
