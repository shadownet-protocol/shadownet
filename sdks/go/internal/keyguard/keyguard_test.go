// SPDX-License-Identifier: MIT

package keyguard

import (
	"crypto/ed25519"
	"encoding/hex"
	"strings"
	"testing"
)

func TestFixtureTableEntriesAreWellFormed(t *testing.T) {
	if len(fixturePublicKeys) == 0 {
		t.Fatal("fixturePublicKeys is empty")
	}
	for i, hx := range fixturePublicKeys {
		raw, err := hex.DecodeString(hx)
		if err != nil {
			t.Errorf("entry %d (%q): hex decode: %v", i, hx, err)
			continue
		}
		if len(raw) != ed25519.PublicKeySize {
			t.Errorf("entry %d (%q): length = %d, want %d", i, hx, len(raw), ed25519.PublicKeySize)
		}
	}
}

func TestIsFixtureKeyRecognizesAllTableEntries(t *testing.T) {
	for i, hx := range fixturePublicKeys {
		raw, err := hex.DecodeString(hx)
		if err != nil {
			t.Fatalf("entry %d hex decode: %v", i, err)
		}
		if !IsFixtureKey(ed25519.PublicKey(raw)) {
			t.Errorf("entry %d: IsFixtureKey returned false on its own table entry", i)
		}
	}
}

func TestIsFixtureKeyRejectsFreshKey(t *testing.T) {
	pub, _, err := ed25519.GenerateKey(nil) // crypto/rand
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}
	if IsFixtureKey(pub) {
		t.Fatalf("freshly-generated key flagged as fixture")
	}
}

func TestIsFixtureKeyRejectsWrongLength(t *testing.T) {
	for _, n := range []int{0, 16, 31, 33, 64} {
		if IsFixtureKey(make([]byte, n)) {
			t.Errorf("IsFixtureKey accepted %d-byte input", n)
		}
	}
}

func TestAssertNotFixtureAllowsFreshKey(t *testing.T) {
	t.Setenv(AllowEnv, "")
	pub, _, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}
	if err := AssertNotFixture(pub, "test-role"); err != nil {
		t.Fatalf("AssertNotFixture on fresh key: %v", err)
	}
}

func TestAssertNotFixtureRefusesFixtureKey(t *testing.T) {
	t.Setenv(AllowEnv, "")
	raw, err := hex.DecodeString(fixturePublicKeys[0])
	if err != nil {
		t.Fatalf("hex decode: %v", err)
	}
	err = AssertNotFixture(ed25519.PublicKey(raw), "sca-server")
	if err == nil {
		t.Fatal("expected error refusing fixture key")
	}
	msg := err.Error()
	if !strings.Contains(msg, "sca-server") {
		t.Errorf("error message should name the role; got %q", msg)
	}
	if !strings.Contains(msg, AllowEnv) {
		t.Errorf("error message should name the override env var %q; got %q", AllowEnv, msg)
	}
	if !strings.Contains(msg, "shadownet keygen") {
		t.Errorf("error message should point at `shadownet keygen` for the fix; got %q", msg)
	}
}

func TestAssertNotFixtureHonorsOptOut(t *testing.T) {
	t.Setenv(AllowEnv, "1")
	raw, _ := hex.DecodeString(fixturePublicKeys[0])
	if err := AssertNotFixture(ed25519.PublicKey(raw), "conformance"); err != nil {
		t.Fatalf("%s=1 should bypass the guard: %v", AllowEnv, err)
	}
}

func TestAssertNotFixtureOnlyAcceptsExactOptIn(t *testing.T) {
	// Only the literal "1" disables the guard. "true", "yes", "0" must NOT.
	for _, v := range []string{"true", "yes", "0", "", " 1", "1 "} {
		t.Run("env="+v, func(t *testing.T) {
			t.Setenv(AllowEnv, v)
			raw, _ := hex.DecodeString(fixturePublicKeys[0])
			if err := AssertNotFixture(ed25519.PublicKey(raw), "test"); err == nil {
				t.Fatalf("%s=%q should not bypass the guard", AllowEnv, v)
			}
		})
	}
}
