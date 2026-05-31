// SPDX-License-Identifier: MIT

package dev_test

import (
	"context"
	"crypto/ed25519"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/hooks/dev"
)

func TestAutoApproveEvaluatesToApprove(t *testing.T) {
	t.Parallel()
	pub, _, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	d, err := dev.NewAutoApproveHook().Evaluate(context.Background(), issuer.CSRView{
		Iss:    "alice@sh4dow.org",
		Aud:    "acme.example",
		Kind:   "org_affiliation",
		Org:    "acme.example",
		Expiry: time.Now().Add(time.Minute),
	}, pub)
	if err != nil {
		t.Fatal(err)
	}
	if d.Outcome != issuer.OutcomeApprove {
		t.Fatalf("expected OutcomeApprove, got %v", d.Outcome)
	}
}

func TestAssertAutoApproveRejectsNonLoopback(t *testing.T) {
	t.Parallel()
	err := dev.AssertAutoApproveNotPublic(nil, "0.0.0.0:8443")
	if err == nil {
		t.Fatal("expected error for non-loopback listener")
	}
	if !strings.Contains(err.Error(), "loopback") && !strings.Contains(err.Error(), "non-loopback") {
		t.Fatalf("error should mention loopback restriction: %v", err)
	}
}

func TestAssertAutoApproveAcceptsLoopback(t *testing.T) {
	t.Parallel()
	for _, addr := range []string{"127.0.0.1:8443", "[::1]:8443", "localhost:8443"} {
		addr := addr
		t.Run(addr, func(t *testing.T) {
			t.Parallel()
			if err := dev.AssertAutoApproveNotPublic(nil, addr); err != nil {
				t.Fatalf("loopback %q should be accepted: %v", addr, err)
			}
		})
	}
}

func TestAssertAutoApproveOptInBypass(t *testing.T) {
	// Sequential — uses env var.
	t.Setenv("SHADOWNET_ALLOW_AUTO_APPROVE", "1")
	if err := dev.AssertAutoApproveNotPublic(nil, "0.0.0.0:8443"); err != nil {
		t.Fatalf("opt-in bypass should accept non-loopback: %v", err)
	}
}
