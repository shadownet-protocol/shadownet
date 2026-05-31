// SPDX-License-Identifier: MIT

package identifiers_test

import (
	"crypto/ed25519"
	"errors"
	"strings"
	"testing"

	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
)

func TestClassify(t *testing.T) {
	t.Parallel()
	pubEncoded := mustEncodeNewKey(t)
	cases := []struct {
		in   string
		want identifiers.Class
	}{
		{"alice@sh4dow.org", identifiers.ClassShadowname},
		{"acme.example", identifiers.ClassDomain},
		{"acme.example.", identifiers.ClassDomain},
		{pubEncoded, identifiers.ClassPubKey},
		{"not-a-thing@", identifiers.ClassUnknown},
		{"@nope", identifiers.ClassUnknown},
		{"", identifiers.ClassUnknown},
		{"z6MkNotARealKey", identifiers.ClassUnknown}, // wrong base58 length
	}
	for _, c := range cases {
		c := c
		t.Run(c.in, func(t *testing.T) {
			t.Parallel()
			if got := identifiers.Classify(c.in); got != c.want {
				t.Fatalf("Classify(%q) = %v, want %v", c.in, got, c.want)
			}
		})
	}
}

func TestValidateShadowname(t *testing.T) {
	t.Parallel()
	if err := identifiers.ValidateShadowname("alice@sh4dow.org"); err != nil {
		t.Fatalf("happy path: %v", err)
	}
	bad := []string{
		"",
		"missing-at",
		"@no-local",
		"local@",
		strings.Repeat("a", 64) + "@too.long",
		"bad rune@host",
		"alice@.bad",
	}
	for _, s := range bad {
		s := s
		t.Run(s, func(t *testing.T) {
			t.Parallel()
			err := identifiers.ValidateShadowname(s)
			if err == nil {
				t.Fatalf("expected error for %q", s)
			}
			if !errors.Is(err, identifiers.ErrInvalid) {
				t.Fatalf("expected ErrInvalid, got %v", err)
			}
		})
	}
}

func TestCanonicalShadowname(t *testing.T) {
	t.Parallel()
	out, err := identifiers.CanonicalShadowname("Alice@SH4DOW.org")
	if err != nil {
		t.Fatal(err)
	}
	if out != "alice@sh4dow.org" {
		t.Fatalf("CanonicalShadowname lower-cases: got %q", out)
	}
}

func TestSplitShadowname(t *testing.T) {
	t.Parallel()
	local, provider, err := identifiers.SplitShadowname("alice@sh4dow.org")
	if err != nil {
		t.Fatal(err)
	}
	if local != "alice" || provider != "sh4dow.org" {
		t.Fatalf("Split = %q, %q", local, provider)
	}
}

func TestValidateDomain(t *testing.T) {
	t.Parallel()
	good := []string{"acme.example", "hr.acme.example", "x", "acme.example."}
	for _, s := range good {
		s := s
		t.Run(s, func(t *testing.T) {
			t.Parallel()
			if err := identifiers.ValidateDomain(s); err != nil {
				t.Fatalf("ValidateDomain(%q) = %v", s, err)
			}
		})
	}
	bad := []string{"", "-bad.example", "bad-.example", "a..b", strings.Repeat("a", 64) + ".example", strings.Repeat("a.", 200) + "x"}
	for _, s := range bad {
		s := s
		t.Run(s, func(t *testing.T) {
			t.Parallel()
			if err := identifiers.ValidateDomain(s); err == nil {
				t.Fatalf("expected error for %q", s)
			}
		})
	}
}

func TestIsSubdomainOf(t *testing.T) {
	t.Parallel()
	cases := []struct {
		candidate, parent string
		want              bool
	}{
		{"acme.example", "acme.example", true},
		{"hr.acme.example", "acme.example", true},
		{"HR.acme.EXAMPLE", "Acme.Example", true},
		{"fakeacme.example", "acme.example", false}, // suffix, not subdomain
		{"acmeexample", "acme.example", false},
		{"other.example", "acme.example", false},
		{"", "acme.example", false},
	}
	for _, c := range cases {
		c := c
		t.Run(c.candidate+"/"+c.parent, func(t *testing.T) {
			t.Parallel()
			if got := identifiers.IsSubdomainOf(c.candidate, c.parent); got != c.want {
				t.Fatalf("IsSubdomainOf(%q, %q) = %v, want %v", c.candidate, c.parent, got, c.want)
			}
		})
	}
}

func TestPubKeyRoundtrip(t *testing.T) {
	t.Parallel()
	for i := 0; i < 16; i++ {
		pub, _, err := ed25519.GenerateKey(nil)
		if err != nil {
			t.Fatal(err)
		}
		s, err := identifiers.EncodePubKey(pub)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(s, "z6Mk") {
			t.Fatalf("encoded pubkey must start with z6Mk: %q", s)
		}
		back, err := identifiers.DecodePubKey(s)
		if err != nil {
			t.Fatal(err)
		}
		if !pub.Equal(back) {
			t.Fatalf("round-trip mismatch on iteration %d", i)
		}
	}
}

func TestDecodePubKeyBadInputs(t *testing.T) {
	t.Parallel()
	bad := []string{"", "z", "abc", "z6Mk", "x6MkSomething"}
	for _, s := range bad {
		s := s
		t.Run(s, func(t *testing.T) {
			t.Parallel()
			if _, err := identifiers.DecodePubKey(s); err == nil {
				t.Fatalf("expected error for %q", s)
			}
		})
	}
}

func TestEncodePubKeyRejectsWrongLength(t *testing.T) {
	t.Parallel()
	if _, err := identifiers.EncodePubKey(ed25519.PublicKey{1, 2, 3}); err == nil {
		t.Fatal("expected error for short pubkey")
	}
}

func mustEncodeNewKey(t *testing.T) string {
	t.Helper()
	pub, _, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	s, err := identifiers.EncodePubKey(pub)
	if err != nil {
		t.Fatal(err)
	}
	return s
}
