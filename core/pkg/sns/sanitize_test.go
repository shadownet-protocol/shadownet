// SPDX-License-Identifier: MIT

package sns_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
	"github.com/shadownet-protocol/shadownet/core/pkg/sns"
)

// failingStore is a RecordStore that always returns an error containing a
// distinctive sentinel string the test then asserts is NOT in the response.
type failingStore struct{}

const internalSentinel = "/var/lib/shadownet/sns.db: SECRET-INTERNAL-PATH"

func (failingStore) Get(context.Context, string) (sns.Record, error) {
	return sns.Record{}, errors.New("store: " + internalSentinel)
}

func (failingStore) Put(context.Context, sns.Record) error {
	return errors.New("store: " + internalSentinel)
}

func (failingStore) Delete(context.Context, string) error {
	return errors.New("store: " + internalSentinel)
}

// TestErrorResponsesDoNotLeakInternalDetails wires sns.Server to a store
// whose errors contain a recognizable sentinel; every error code path must
// log internally but return a sanitized response.
func TestErrorResponsesDoNotLeakInternalDetails(t *testing.T) {
	provKP, _ := crypto.Generate()
	provDID, _ := did.EncodeKey(provKP.Public)
	provKID := provDID + "#" + strings.TrimPrefix(provDID, "did:key:")

	logBuf := &strings.Builder{}
	logger := slog.New(slog.NewTextHandler(syncWriter{logBuf}, &slog.HandlerOptions{Level: slog.LevelDebug}))

	server := &sns.Server{
		ProviderDID: provDID, ProviderKID: provKID, Provider: "test.example",
		Key: provKP, Records: failingStore{},
		DIDResolver: did.NewKeyResolver(),
		DefaultTTL:  300,
		Now:         func() time.Time { return time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC) },
		Logger:      logger,
	}
	if err := server.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	srv := httptest.NewServer(server.Handler())
	defer srv.Close()

	// /resolve hits Records.Get → failing store → must not leak sentinel.
	resp, err := srv.Client().Get(srv.URL + sns.ResolvePath + "?name=alice@test.example")
	if err != nil {
		t.Fatalf("GET resolve: %v", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if strings.Contains(string(body), internalSentinel) {
		t.Fatalf("response leaked internal sentinel: %s", body)
	}
	// Body should still be a structured envelope.
	var env map[string]any
	if err := json.Unmarshal(body, &env); err != nil {
		t.Fatalf("response is not JSON: %s", body)
	}
	if env["detail"] == nil {
		t.Fatalf("expected sanitized detail, got: %s", body)
	}
	detailStr, _ := env["detail"].(string)
	if strings.Contains(detailStr, internalSentinel) {
		t.Fatalf("detail field leaked sentinel: %q", detailStr)
	}
	if strings.Contains(detailStr, "/") || strings.Contains(detailStr, "store:") {
		t.Fatalf("detail field looks unsanitized: %q", detailStr)
	}

	// Internal logger MUST still see the full reason.
	if !strings.Contains(logBuf.String(), internalSentinel) {
		t.Fatalf("logger did not capture internal sentinel; captured: %s", logBuf.String())
	}

	// Same check on PUT (with bogus auth, but the auth-failure path also
	// gets sanitization).
	bogusAuth := "Bearer not.a.valid.jwt"
	req, _ := http.NewRequest(http.MethodPut, srv.URL+"/v1/records/alice", strings.NewReader(`{}`))
	req.Header.Set("Authorization", bogusAuth)
	req.Header.Set("Content-Type", "application/json")
	resp, err = srv.Client().Do(req)
	if err != nil {
		t.Fatalf("PUT: %v", err)
	}
	body, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if strings.Contains(string(body), "kid") || strings.Contains(string(body), "JWT") || strings.Contains(string(body), "JWS") {
		t.Fatalf("PUT auth-failure response leaked JWT/JWS internals: %s", body)
	}
}

// syncWriter wraps a strings.Builder so the slog text handler can write to
// it from any goroutine without races.
type syncWriter struct{ b *strings.Builder }

func (s syncWriter) Write(p []byte) (int, error) { return s.b.Write(p) }
