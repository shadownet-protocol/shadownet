// SPDX-License-Identifier: MIT

package did

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
)

func TestWebResolverFetchAndParse(t *testing.T) {
	kp, _ := crypto.Generate()
	jwk, err := crypto.PublicJWK(kp.Public, "")
	if err != nil {
		t.Fatalf("PublicJWK: %v", err)
	}

	var calls int32
	mux := http.NewServeMux()
	srv := httptest.NewTLSServer(mux)
	defer srv.Close()
	host := stripScheme(srv.URL)
	mux.HandleFunc("/.well-known/did.json", func(w http.ResponseWriter, _ *http.Request) {
		atomic.AddInt32(&calls, 1)
		doc := map[string]any{
			"id": "did:web:" + host,
			"verificationMethod": []map[string]any{{
				"id":           "did:web:" + host + "#k1",
				"type":         "JsonWebKey2020",
				"controller":   "did:web:" + host,
				"publicKeyJwk": jwk,
			}},
			"authentication":  []string{"#k1"},
			"assertionMethod": []string{"#k1"},
			// extra field that v0.1 verifiers MUST ignore
			"service": []map[string]any{{"id": "#svc", "type": "Foo", "serviceEndpoint": "https://example/foo"}},
		}
		w.Header().Set("Content-Type", "application/did+json")
		w.Header().Set("Cache-Control", "max-age=60")
		_ = json.NewEncoder(w).Encode(doc)
	})

	resolver := NewWebResolver(WithHTTPClient(srv.Client()))
	did := "did:web:" + host

	doc, err := resolver.Resolve(context.Background(), did)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if doc.ID != did {
		t.Fatalf("doc.ID = %q, want %q", doc.ID, did)
	}
	if len(doc.VerificationMethod) != 1 {
		t.Fatalf("verificationMethod count = %d", len(doc.VerificationMethod))
	}
	if !bytes.Equal(doc.VerificationMethod[0].Public, kp.Public) {
		t.Fatalf("public key mismatch")
	}

	// Cache hit: second resolve should not call upstream.
	if _, err := resolver.Resolve(context.Background(), did); err != nil {
		t.Fatalf("second Resolve: %v", err)
	}
	if c := atomic.LoadInt32(&calls); c != 1 {
		t.Fatalf("upstream calls = %d, want 1 (cache miss expected once)", c)
	}
}

func TestWebResolverIDMismatch(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "did:web:other.example"})
	}))
	defer srv.Close()
	resolver := NewWebResolver(WithHTTPClient(srv.Client()))
	if _, err := resolver.Resolve(context.Background(), "did:web:"+stripScheme(srv.URL)); err == nil {
		t.Fatalf("expected ID-mismatch error")
	}
}

func TestWebResolverSizeCap(t *testing.T) {
	big := strings.Repeat("x", 32*1024)
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"id":"did:web:x","filler":"` + big + `"}`))
	}))
	defer srv.Close()
	resolver := NewWebResolver(WithHTTPClient(srv.Client()), WithMaxDocumentBytes(4*1024))
	if _, err := resolver.Resolve(context.Background(), "did:web:"+stripScheme(srv.URL)); err == nil {
		t.Fatalf("expected oversize error")
	}
}

func TestWebResolverHonorsMaxAge(t *testing.T) {
	var clock int64 = 1_000_000
	now := func() time.Time { return time.Unix(atomic.LoadInt64(&clock), 0) }

	var calls int32
	mux := http.NewServeMux()
	srv := httptest.NewTLSServer(mux)
	defer srv.Close()
	host := stripScheme(srv.URL)
	mux.HandleFunc("/.well-known/did.json", func(w http.ResponseWriter, _ *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.Header().Set("Cache-Control", "max-age=10")
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "did:web:" + host})
	})

	resolver := NewWebResolver(WithHTTPClient(srv.Client()), withClock(now))
	did := "did:web:" + host

	if _, err := resolver.Resolve(context.Background(), did); err != nil {
		t.Fatalf("first Resolve: %v", err)
	}
	atomic.StoreInt64(&clock, 1_000_005)
	if _, err := resolver.Resolve(context.Background(), did); err != nil {
		t.Fatalf("warm-cache Resolve: %v", err)
	}
	if got := atomic.LoadInt32(&calls); got != 1 {
		t.Fatalf("calls within ttl = %d, want 1", got)
	}

	atomic.StoreInt64(&clock, 1_000_020) // 20s later, past 10s max-age
	if _, err := resolver.Resolve(context.Background(), did); err != nil {
		t.Fatalf("post-ttl Resolve: %v", err)
	}
	if got := atomic.LoadInt32(&calls); got != 2 {
		t.Fatalf("calls after ttl = %d, want 2", got)
	}
}

func TestDIDWebToURL(t *testing.T) {
	cases := map[string]string{
		"did:web:example.com":            "https://example.com/.well-known/did.json",
		"did:web:example.com:user:alice": "https://example.com/user/alice/did.json",
		"did:web:host%3A8443":            "https://host:8443/.well-known/did.json",
	}
	for in, want := range cases {
		got, err := didWebToURL(in)
		if err != nil {
			t.Errorf("didWebToURL(%q): %v", in, err)
			continue
		}
		if got != want {
			t.Errorf("didWebToURL(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestCacheControlMaxAge(t *testing.T) {
	cases := map[string]time.Duration{
		"":                                    0,
		"no-store":                            0,
		"max-age=0":                           0,
		"max-age=300":                         300 * time.Second,
		"public, max-age=60, must-revalidate": 60 * time.Second,
		"max-age=abc":                         0,
		"max-age=-5":                          0,
	}
	for in, want := range cases {
		if got := cacheControlMaxAge(in); got != want {
			t.Errorf("cacheControlMaxAge(%q) = %v, want %v", in, got, want)
		}
	}
}

// stripScheme returns the URL's host (with port) percent-encoded for use as
// a did:web body, where the host:port colon must be encoded as %3A per the
// W3C did:web spec.
func stripScheme(u string) string {
	host := strings.TrimPrefix(u, "https://")
	return strings.ReplaceAll(host, ":", "%3A")
}
