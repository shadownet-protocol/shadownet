// SPDX-License-Identifier: MIT

package issuer_test

import (
	"context"
	"crypto/ed25519"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
)

// The authz tests below exercise rules 1 (iss == org) and 2 (iss is a
// sub-domain of org) — the paths that don't touch DNS. Rule 3 (DNS
// delegate=) is integration-tested separately against a controlled
// resolver in a build-tag-gated test that the conformance suite owns.

func TestAuthorizeIssEqualsOrg(t *testing.T) {
	t.Parallel()
	a := issuer.NewAuthorizer(issuer.AuthzConfig{Now: time.Now})
	if err := a.Authorize(context.Background(), "acme.example", "acme.example"); err != nil {
		t.Fatalf("iss==org domain: %v", err)
	}
	if err := a.Authorize(context.Background(), "ACME.Example", "acme.example"); err != nil {
		t.Fatalf("iss==org case insensitive: %v", err)
	}
}

func TestAuthorizeKeyedIssEqualsKeyedOrg(t *testing.T) {
	t.Parallel()
	pub, _, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	pk, err := identifiers.EncodePubKey(pub)
	if err != nil {
		t.Fatal(err)
	}
	a := issuer.NewAuthorizer(issuer.AuthzConfig{Now: time.Now})
	if err := a.Authorize(context.Background(), pk, pk); err != nil {
		t.Fatalf("keyed iss==org: %v", err)
	}
}

func TestAuthorizeKeyedIssWithDomainOrgRejected(t *testing.T) {
	t.Parallel()
	// Per §6.6: keyed issuers can only use rule 1 (iss == org).
	// A keyed iss against a domain org has no valid rule.
	pub, _, _ := ed25519.GenerateKey(nil)
	pk, _ := identifiers.EncodePubKey(pub)
	a := issuer.NewAuthorizer(issuer.AuthzConfig{Now: time.Now})
	err := a.Authorize(context.Background(), pk, "acme.example")
	if !errors.Is(err, issuer.ErrNotAuthorized) {
		t.Fatalf("expected ErrNotAuthorized, got %v", err)
	}
	if !strings.Contains(err.Error(), "keyed issuer") {
		t.Fatalf("error should mention keyed-issuer carve-out: %v", err)
	}
}

func TestAuthorizeDomainIssWithKeyedOrgRejected(t *testing.T) {
	t.Parallel()
	pub, _, _ := ed25519.GenerateKey(nil)
	pk, _ := identifiers.EncodePubKey(pub)
	a := issuer.NewAuthorizer(issuer.AuthzConfig{Now: time.Now})
	err := a.Authorize(context.Background(), "acme.example", pk)
	if !errors.Is(err, issuer.ErrNotAuthorized) {
		t.Fatalf("expected ErrNotAuthorized, got %v", err)
	}
}

func TestAuthorizeSubdomainAccepted(t *testing.T) {
	t.Parallel()
	a := issuer.NewAuthorizer(issuer.AuthzConfig{Now: time.Now})
	if err := a.Authorize(context.Background(), "hr.acme.example", "acme.example"); err != nil {
		t.Fatalf("subdomain rule 2: %v", err)
	}
}

func TestAuthorizeSuffixNotSubdomain(t *testing.T) {
	t.Parallel()
	// fakeacme.example happens to end in "acme.example" but is NOT a
	// sub-domain. IsSubdomainOf must reject this.
	a := issuer.NewAuthorizer(issuer.AuthzConfig{Now: time.Now})
	err := a.Authorize(context.Background(), "fakeacme.example", "acme.example")
	if !errors.Is(err, issuer.ErrNotAuthorized) {
		t.Fatalf("expected ErrNotAuthorized for suffix-but-not-subdomain, got %v", err)
	}
}

func TestAuthorizeUnknownIdentifierRejected(t *testing.T) {
	t.Parallel()
	a := issuer.NewAuthorizer(issuer.AuthzConfig{Now: time.Now})
	if err := a.Authorize(context.Background(), "", "acme.example"); err == nil {
		t.Fatal("expected error for empty iss")
	}
	if err := a.Authorize(context.Background(), "acme.example", "not-a-valid-id!@#"); err == nil {
		t.Fatal("expected error for malformed org")
	}
}
