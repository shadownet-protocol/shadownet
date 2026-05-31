// SPDX-License-Identifier: MIT

package issuer_test

import (
	"errors"
	"testing"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
)

func TestIdempotencyKeyStableAcrossEquivalentReqs(t *testing.T) {
	t.Parallel()
	k1, err := issuer.IdempotencyKey("alice@sh4dow.org", "acme.example",
		map[string]any{"kind": "org_affiliation", "org": "acme.example"})
	if err != nil {
		t.Fatal(err)
	}
	// Same logical request, different map order at the call site.
	k2, err := issuer.IdempotencyKey("alice@sh4dow.org", "acme.example",
		map[string]any{"org": "acme.example", "kind": "org_affiliation"})
	if err != nil {
		t.Fatal(err)
	}
	if k1 != k2 {
		t.Fatalf("equivalent reqs produced different keys: %q vs %q", k1, k2)
	}
}

func TestIdempotencyKeyDiffersOnAnyChange(t *testing.T) {
	t.Parallel()
	base, err := issuer.IdempotencyKey("alice@sh4dow.org", "acme.example",
		map[string]any{"kind": "org_affiliation", "org": "acme.example"})
	if err != nil {
		t.Fatal(err)
	}
	mutations := []struct {
		name       string
		iss, aud   string
		req        map[string]any
		expectDiff bool
	}{
		{
			name: "different-iss",
			iss:  "bob@sh4dow.org", aud: "acme.example",
			req:        map[string]any{"kind": "org_affiliation", "org": "acme.example"},
			expectDiff: true,
		},
		{
			name: "different-aud",
			iss:  "alice@sh4dow.org", aud: "other.example",
			req:        map[string]any{"kind": "org_affiliation", "org": "acme.example"},
			expectDiff: true,
		},
		{
			name: "different-req-org",
			iss:  "alice@sh4dow.org", aud: "acme.example",
			req:        map[string]any{"kind": "org_affiliation", "org": "other.example"},
			expectDiff: true,
		},
	}
	for _, m := range mutations {
		m := m
		t.Run(m.name, func(t *testing.T) {
			t.Parallel()
			got, err := issuer.IdempotencyKey(m.iss, m.aud, m.req)
			if err != nil {
				t.Fatal(err)
			}
			if (got == base) == m.expectDiff {
				t.Fatalf("mutation %q: equal=%v, expectDiff=%v", m.name, got == base, m.expectDiff)
			}
		})
	}
}

func TestIdempotencyKeyConcatenationCannotCollide(t *testing.T) {
	t.Parallel()
	// Smoke test the 0x1F unit-separator: without it, "iss=ab"+"aud=c"
	// and "iss=a"+"aud=bc" could collide. With it, they can't.
	k1, _ := issuer.IdempotencyKey("ab", "c", map[string]any{"kind": "x", "org": "y"})
	k2, _ := issuer.IdempotencyKey("a", "bc", map[string]any{"kind": "x", "org": "y"})
	if k1 == k2 {
		t.Fatalf("unit-separator failed; same key for distinct inputs: %s", k1)
	}
}

func TestIdempotencyKeyRequiresAllInputs(t *testing.T) {
	t.Parallel()
	if _, err := issuer.IdempotencyKey("", "acme.example", map[string]any{"k": "v"}); !errors.Is(err, issuer.ErrInvalid) {
		t.Fatalf("expected ErrInvalid for empty iss, got %v", err)
	}
	if _, err := issuer.IdempotencyKey("alice@sh4dow.org", "", map[string]any{"k": "v"}); !errors.Is(err, issuer.ErrInvalid) {
		t.Fatalf("expected ErrInvalid for empty aud, got %v", err)
	}
	if _, err := issuer.IdempotencyKey("alice@sh4dow.org", "acme.example", nil); !errors.Is(err, issuer.ErrInvalid) {
		t.Fatalf("expected ErrInvalid for empty req, got %v", err)
	}
}
