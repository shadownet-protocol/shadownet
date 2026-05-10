// SPDX-License-Identifier: MIT

package did

import (
	"bytes"
	"strings"
	"testing"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/crypto"
)

func TestEncodeDecodeKeyRoundtrip(t *testing.T) {
	for i := 0; i < 8; i++ {
		kp, err := crypto.Generate()
		if err != nil {
			t.Fatalf("Generate: %v", err)
		}
		didStr, err := EncodeKey(kp.Public)
		if err != nil {
			t.Fatalf("EncodeKey: %v", err)
		}
		if !strings.HasPrefix(didStr, "did:key:z") {
			t.Fatalf("did:key prefix wrong: %q", didStr)
		}
		got, err := DecodeKey(didStr)
		if err != nil {
			t.Fatalf("DecodeKey: %v", err)
		}
		if !bytes.Equal(got, kp.Public) {
			t.Fatalf("public key roundtrip mismatch")
		}
	}
}

func TestDecodeKeyTolerantOfFragment(t *testing.T) {
	kp, _ := crypto.Generate()
	didStr, _ := EncodeKey(kp.Public)
	withFrag := didStr + "#" + strings.TrimPrefix(didStr, "did:key:")
	got, err := DecodeKey(withFrag)
	if err != nil {
		t.Fatalf("DecodeKey with fragment: %v", err)
	}
	if !bytes.Equal(got, kp.Public) {
		t.Fatalf("fragment-handling roundtrip mismatch")
	}
}

func TestDecodeKeyRejects(t *testing.T) {
	cases := map[string]string{
		"not-a-did":        "did:key:abcd", // missing 'z' multibase
		"wrong-method":     "did:web:example.com",
		"empty body":       "did:key:",
		"bad multibase":    "did:key:z!!!",
		"wrong multicodec": "did:key:z2DEFGabc", // valid base58 but not 0xed01 prefix
		"too short":        "did:key:z" + base58Encode([]byte{0xed, 0x01, 0x00}),
	}
	for name, in := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := DecodeKey(in); err == nil {
				t.Fatalf("expected error decoding %q", in)
			}
		})
	}
}

func TestMethod(t *testing.T) {
	cases := map[string]string{
		"did:key:z6MkABC":        "key",
		"did:web:example.com":    "web",
		"did:web:example.com#k1": "web",
		"not-a-did":              "",
		"did:":                   "",
	}
	for in, want := range cases {
		if got := Method(in); got != want {
			t.Errorf("Method(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestSplitDIDURL(t *testing.T) {
	d, f := SplitDIDURL("did:key:z6MkExample#0")
	if d != "did:key:z6MkExample" || f != "0" {
		t.Fatalf("got (%q,%q)", d, f)
	}
	d, f = SplitDIDURL("did:web:example.com")
	if d != "did:web:example.com" || f != "" {
		t.Fatalf("got (%q,%q)", d, f)
	}
}

func TestBase58Roundtrip(t *testing.T) {
	for _, in := range [][]byte{
		nil,
		{0x00},
		{0x00, 0x00, 0x01},
		{0xed, 0x01, 0xff, 0xee, 0xdd, 0xcc, 0xbb, 0xaa},
	} {
		s := base58Encode(in)
		out, err := base58Decode(s)
		if err != nil {
			t.Fatalf("base58Decode(%q): %v", s, err)
		}
		if len(in) == 0 && len(out) == 0 {
			continue
		}
		if !bytes.Equal(in, out) {
			t.Fatalf("roundtrip: in=%x out=%x s=%q", in, out, s)
		}
	}
}
