// SPDX-License-Identifier: MIT

package sca_test

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sca"
)

// fastBackoff is the test schedule: 5 zero-delay attempts so the retry path
// completes in microseconds rather than the spec's 35-minute schedule.
var fastBackoff = []time.Duration{0, 0, 0, 0, 0}

func TestHTTPCallerHappyPathHMACMatches(t *testing.T) {
	var (
		gotSig  atomic.Pointer[string]
		gotBody atomic.Pointer[[]byte]
		hits    atomic.Int32
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		body, _ := io.ReadAll(r.Body)
		s := r.Header.Get("X-SCA-Callback-Sig")
		gotSig.Store(&s)
		gotBody.Store(&body)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	caller := &sca.HTTPCaller{Client: srv.Client(), Backoff: fastBackoff}
	sess := sca.Session{ID: "ses-happy", CallbackURL: srv.URL}

	caller.Notify(context.Background(), sess, "ready")
	waitFor(t, func() bool { return hits.Load() == 1 }, time.Second)

	if hits.Load() != 1 {
		t.Fatalf("expected exactly 1 callback delivery, got %d", hits.Load())
	}
	body := *gotBody.Load()
	sig := *gotSig.Load()
	if err := sca.VerifyCallbackSignature(sig, sess.ID, body); err != nil {
		t.Fatalf("HMAC mismatch: %v (sig=%q body=%s)", err, sig, body)
	}
	// Body shape per RFC-0004 §Callbacks.
	var got map[string]any
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatalf("parse body: %v", err)
	}
	if got["shadownet:v"] != "0.1" || got["sessionId"] != "ses-happy" || got["status"] != "ready" {
		t.Fatalf("unexpected body shape: %+v", got)
	}
}

func TestHTTPCallerHMACBytesMatchManualComputation(t *testing.T) {
	// Reference vector: receiver records the exact body bytes it gets, then
	// recomputes HMAC-SHA256(sessionID, body) inline. JSON map ordering is
	// stable for encoding/json (alphabetical keys) but we don't depend on
	// that here — we hash whatever the wire actually carried.
	const sessID = "ses-test-vector"
	var (
		gotSig  atomic.Pointer[string]
		gotBody atomic.Pointer[[]byte]
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		s := r.Header.Get("X-SCA-Callback-Sig")
		gotSig.Store(&s)
		gotBody.Store(&body)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	caller := &sca.HTTPCaller{Client: srv.Client(), Backoff: fastBackoff}
	caller.Notify(context.Background(), sca.Session{ID: sessID, CallbackURL: srv.URL}, "ready")
	waitFor(t, func() bool { return gotSig.Load() != nil }, time.Second)

	body := *gotBody.Load()
	sig := *gotSig.Load()
	mac := hmac.New(sha256.New, []byte(sessID))
	mac.Write(body)
	want := "sha256=" + hex.EncodeToString(mac.Sum(nil))
	if sig != want {
		t.Fatalf("\n  got  %s\n  want %s\n  body %s", sig, want, body)
	}
}

func TestHTTPCallerRetriesOnFailureThenGivesUp(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	caller := &sca.HTTPCaller{Client: srv.Client(), Backoff: fastBackoff}
	caller.Notify(context.Background(), sca.Session{ID: "ses-fail", CallbackURL: srv.URL}, "ready")

	waitFor(t, func() bool { return hits.Load() == int32(len(fastBackoff)) }, 2*time.Second)
	if hits.Load() != int32(len(fastBackoff)) {
		t.Fatalf("delivery hits = %d, want %d (one per attempt before exhaustion)", hits.Load(), len(fastBackoff))
	}
}

func TestHTTPCallerRespectsContextCancellation(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	// Schedule with a non-trivial second-attempt delay so we have a window
	// to cancel before the retry fires.
	backoff := []time.Duration{0, 200 * time.Millisecond, 200 * time.Millisecond}
	caller := &sca.HTTPCaller{Client: srv.Client(), Backoff: backoff}

	ctx, cancel := context.WithCancel(context.Background())
	caller.Notify(ctx, sca.Session{ID: "ses-cancel", CallbackURL: srv.URL}, "ready")
	// Wait for the first attempt to land, then cancel.
	waitFor(t, func() bool { return hits.Load() >= 1 }, 500*time.Millisecond)
	cancel()
	time.Sleep(500 * time.Millisecond)

	// At most one or two attempts should have fired; certainly not all 3.
	if hits.Load() >= int32(len(backoff)) {
		t.Fatalf("context cancellation did not stop retries: %d attempts (max %d)", hits.Load(), len(backoff)-1)
	}
}

func TestHTTPCallerSkipsWhenCallbackURLEmpty(t *testing.T) {
	caller := &sca.HTTPCaller{Backoff: fastBackoff}
	// No URL → Notify must be a no-op (no goroutine, no panic).
	caller.Notify(context.Background(), sca.Session{ID: "ses-empty", CallbackURL: ""}, "ready")
}

func TestVerifyCallbackSignatureRejectsTamperedBody(t *testing.T) {
	body := []byte(`{"x":1}`)
	mac := hmac.New(sha256.New, []byte("k"))
	mac.Write(body)
	good := "sha256=" + hex.EncodeToString(mac.Sum(nil))

	if err := sca.VerifyCallbackSignature(good, "k", body); err != nil {
		t.Fatalf("VerifyCallbackSignature on good input: %v", err)
	}
	if err := sca.VerifyCallbackSignature(good, "k", []byte(`{"x":2}`)); err == nil {
		t.Fatal("VerifyCallbackSignature accepted tampered body")
	}
	if err := sca.VerifyCallbackSignature("not-a-sig", "k", body); err == nil {
		t.Fatal("VerifyCallbackSignature accepted malformed header")
	}
	if err := sca.VerifyCallbackSignature(strings.Replace(good, "sha256=", "sha512=", 1), "k", body); err == nil {
		t.Fatal("VerifyCallbackSignature accepted wrong scheme prefix")
	}
}

func waitFor(t *testing.T, cond func() bool, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(2 * time.Millisecond)
	}
}
