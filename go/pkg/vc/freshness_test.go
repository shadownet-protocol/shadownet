// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/go/pkg/did"
)

func TestFreshnessRoundtrip(t *testing.T) {
	issKP, issuer, kid := issuerSetup(t)
	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)

	jwt, err := IssueFreshness(issKP, issuer, kid, "urn:uuid:cred-1", now, now.Add(time.Hour))
	if err != nil {
		t.Fatalf("IssueFreshness: %v", err)
	}
	got, err := VerifyFreshness(context.Background(), did.NewKeyResolver(), jwt, now)
	if err != nil {
		t.Fatalf("VerifyFreshness: %v", err)
	}
	if got.CredentialJTI != "urn:uuid:cred-1" {
		t.Fatalf("jti = %q", got.CredentialJTI)
	}
}

func TestFreshnessRejectsLifetimeOverCap(t *testing.T) {
	issKP, issuer, kid := issuerSetup(t)
	now := time.Now().UTC()
	if _, err := IssueFreshness(issKP, issuer, kid, "x", now, now.Add(48*time.Hour)); err == nil {
		t.Fatalf("expected lifetime-cap error")
	}
}

func TestFreshnessExpired(t *testing.T) {
	issKP, issuer, kid := issuerSetup(t)
	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	jwt, _ := IssueFreshness(issKP, issuer, kid, "x", now, now.Add(time.Minute))
	if _, err := VerifyFreshness(context.Background(), did.NewKeyResolver(), jwt, now.Add(time.Hour)); err == nil {
		t.Fatalf("expected expired error")
	}
}
