// SPDX-License-Identifier: MIT

package queue_test

import (
	"context"
	"crypto/ed25519"
	"path/filepath"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/hooks/queue"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/sqlitestore"
)

func newStore(t *testing.T) *sqlitestore.Store {
	t.Helper()
	path := filepath.Join(t.TempDir(), "queue.db")
	s, err := sqlitestore.Open("file:"+path, 32)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func newCSR() (issuer.CSRView, ed25519.PublicKey) {
	pub, _, _ := ed25519.GenerateKey(nil)
	return issuer.CSRView{
		Iss:    "alice@sh4dow.org",
		Aud:    "acme.example",
		Kind:   "org_affiliation",
		Org:    "acme.example",
		Expiry: time.Now().Add(time.Hour),
	}, pub
}

func TestQueueFirstPostReturnsPending(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	h, err := queue.New(queue.Config{
		Store:   store,
		NextURL: "https://acme.example/.well-known/shadownet/issue",
	})
	if err != nil {
		t.Fatal(err)
	}
	csr, pub := newCSR()
	d, err := h.Evaluate(context.Background(), csr, pub)
	if err != nil {
		t.Fatal(err)
	}
	if d.Outcome != issuer.OutcomePending {
		t.Fatalf("first post outcome = %v, want OutcomePending", d.Outcome)
	}
	if d.NextURL != "https://acme.example/.well-known/shadownet/issue" {
		t.Fatalf("NextURL = %q", d.NextURL)
	}
	if d.HandleID == "" {
		t.Fatal("HandleID should be set")
	}
}

func TestQueueRepostReusesHandle(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	h, _ := queue.New(queue.Config{Store: store, NextURL: "https://x.example/issue"})
	csr, pub := newCSR()

	d1, _ := h.Evaluate(context.Background(), csr, pub)
	d2, _ := h.Evaluate(context.Background(), csr, pub)
	if d1.HandleID != d2.HandleID {
		t.Fatalf("re-post should reuse handle: %q vs %q", d1.HandleID, d2.HandleID)
	}
}

func TestQueueAdvancesOnApproval(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	h, _ := queue.New(queue.Config{Store: store, NextURL: "https://x.example/issue"})
	csr, pub := newCSR()

	d1, _ := h.Evaluate(context.Background(), csr, pub)
	if err := store.UpdatePendingStatus(context.Background(), d1.HandleID, issuer.PendingApproved, "", time.Now()); err != nil {
		t.Fatal(err)
	}
	d2, err := h.Evaluate(context.Background(), csr, pub)
	if err != nil {
		t.Fatal(err)
	}
	if d2.Outcome != issuer.OutcomeApprove {
		t.Fatalf("post-approval re-post = %v, want OutcomeApprove", d2.Outcome)
	}
}

func TestQueueSurfacesRejection(t *testing.T) {
	t.Parallel()
	store := newStore(t)
	h, _ := queue.New(queue.Config{Store: store, NextURL: "https://x.example/issue"})
	csr, pub := newCSR()
	d1, _ := h.Evaluate(context.Background(), csr, pub)
	if err := store.UpdatePendingStatus(context.Background(), d1.HandleID, issuer.PendingRejected, "vetting failed", time.Now()); err != nil {
		t.Fatal(err)
	}
	d2, _ := h.Evaluate(context.Background(), csr, pub)
	if d2.Outcome != issuer.OutcomeReject {
		t.Fatalf("post-rejection re-post = %v, want OutcomeReject", d2.Outcome)
	}
	if d2.Reason != "vetting failed" {
		t.Fatalf("reason not surfaced: %q", d2.Reason)
	}
}

func TestQueueRejectsBadConfig(t *testing.T) {
	t.Parallel()
	if _, err := queue.New(queue.Config{NextURL: "https://x.example/issue"}); err == nil {
		t.Fatal("expected error for missing Store")
	}
	if _, err := queue.New(queue.Config{Store: newStore(t)}); err == nil {
		t.Fatal("expected error for missing NextURL")
	}
}
