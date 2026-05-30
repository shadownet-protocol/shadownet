// SPDX-License-Identifier: MIT

package jcs_test

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/shadownet-protocol/shadownet/core/internal/jcs"
)

func TestLiterals(t *testing.T) {
	t.Parallel()
	cases := []struct {
		in   any
		want string
	}{
		{nil, "null"},
		{true, "true"},
		{false, "false"},
	}
	for _, c := range cases {
		c := c
		t.Run(c.want, func(t *testing.T) {
			t.Parallel()
			got, err := jcs.Canonicalize(c.in)
			if err != nil {
				t.Fatal(err)
			}
			if string(got) != c.want {
				t.Fatalf("got %q want %q", got, c.want)
			}
		})
	}
}

func TestIntegers(t *testing.T) {
	t.Parallel()
	cases := []struct {
		in   any
		want string
	}{
		{0, "0"},
		{1, "1"},
		{-1, "-1"},
		{int64(1730000050), "1730000050"},
		{json.Number("42"), "42"},
		{json.Number("-9223372036854775807"), "-9223372036854775807"},
		{json.Number("123456789012345678901234567890"), "123456789012345678901234567890"},
	}
	for _, c := range cases {
		c := c
		t.Run(c.want, func(t *testing.T) {
			t.Parallel()
			got, err := jcs.Canonicalize(c.in)
			if err != nil {
				t.Fatal(err)
			}
			if string(got) != c.want {
				t.Fatalf("got %q want %q", got, c.want)
			}
		})
	}
}

func TestFloatRejected(t *testing.T) {
	t.Parallel()
	bad := []any{1.5, float32(2.0), json.Number("3.14"), json.Number("1e10")}
	for _, v := range bad {
		v := v
		t.Run("", func(t *testing.T) {
			t.Parallel()
			if _, err := jcs.Canonicalize(v); !errors.Is(err, jcs.ErrFloatUnsupported) {
				t.Fatalf("expected ErrFloatUnsupported for %v, got %v", v, err)
			}
		})
	}
}

func TestStrings(t *testing.T) {
	t.Parallel()
	type tc struct {
		name string
		in   string
		want string
	}
	cases := []tc{
		{"hello", "hello", "\"hello\""},
		{"escaped-quote", "he said \"hi\"", "\"he said \\\"hi\\\"\""},
		{"escaped-backslash", "a\\b", "\"a\\\\b\""},
		{"tab", "a\tb", "\"a\\tb\""},
		{"newline", "a\nb", "\"a\\nb\""},
		{"carriage-return", "a\rb", "\"a\\rb\""},
		{"backspace", "a\bb", "\"a\\bb\""},
		{"formfeed", "a\fb", "\"a\\fb\""},
		{"control-chars", "\x00\x01\x1f", "\"\\u0000\\u0001\\u001f\""},
		{"unicode-bmp", "é", "\"é\""}, // §3.2.2.2: U+0080+ verbatim
		{"unicode-euro", "€", "\"€\""},
	}
	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			got, err := jcs.Canonicalize(c.in)
			if err != nil {
				t.Fatal(err)
			}
			if string(got) != c.want {
				t.Fatalf("got %q want %q", got, c.want)
			}
		})
	}
}

func TestLoneSurrogateRejected(t *testing.T) {
	t.Parallel()
	// "\xed\xa0\x80" is the UTF-8 encoding of U+D800 (a lone high surrogate).
	bad := string([]byte{0xed, 0xa0, 0x80})
	if _, err := jcs.Canonicalize(bad); err == nil {
		t.Fatal("expected error for lone surrogate")
	}
}

func TestArrayPreservesOrder(t *testing.T) {
	t.Parallel()
	out, err := jcs.Canonicalize([]any{1, 2, 3})
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != "[1,2,3]" {
		t.Fatalf("got %q", out)
	}
}

func TestEmptyArray(t *testing.T) {
	t.Parallel()
	out, err := jcs.Canonicalize([]any{})
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != "[]" {
		t.Fatalf("got %q", out)
	}
}

func TestObjectKeysSortedUTF16(t *testing.T) {
	t.Parallel()
	obj := map[string]any{"b": 2, "a": 1}
	out, err := jcs.Canonicalize(obj)
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != "{\"a\":1,\"b\":2}" {
		t.Fatalf("got %q", out)
	}
}

func TestObjectKeysSortedUnicode(t *testing.T) {
	t.Parallel()
	// "é" (U+00E9) sorts after ASCII letters under code-unit ordering.
	obj := map[string]any{"é": 1, "z": 2}
	out, err := jcs.Canonicalize(obj)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(out), "{\"z\":2,") {
		t.Fatalf("got %q", out)
	}
}

func TestObjectRecursive(t *testing.T) {
	t.Parallel()
	doc := map[string]any{
		"outer": map[string]any{
			"z": 1,
			"a": map[string]any{"y": 2, "b": 3},
		},
		"first": "x",
	}
	out, err := jcs.Canonicalize(doc)
	if err != nil {
		t.Fatal(err)
	}
	want := "{\"first\":\"x\",\"outer\":{\"a\":{\"b\":3,\"y\":2},\"z\":1}}"
	if string(out) != want {
		t.Fatalf("got %q want %q", out, want)
	}
}

// TestEnvelopeMsgHashShape mirrors the envelope §8.4 canonical input we feed
// into msgHash from the python-sdk's test suite, ensuring the two impls
// agree on this concrete shape before the cross-corpus test runs.
func TestEnvelopeMsgHashShape(t *testing.T) {
	t.Parallel()
	doc := map[string]any{
		"messageId": "01HZ7K3CWAB4D6N5XT0M2EXAMPLE",
		"role":      "ROLE_USER",
		"parts":     []any{map[string]any{"text": "hi"}},
		"contextId": "01HZ7K2BV5R2K0DW3FCONTEXT0001",
		"metadata":  map[string]any{},
	}
	got, err := jcs.Canonicalize(doc)
	if err != nil {
		t.Fatal(err)
	}
	want := "{\"contextId\":\"01HZ7K2BV5R2K0DW3FCONTEXT0001\",\"messageId\":\"01HZ7K3CWAB4D6N5XT0M2EXAMPLE\",\"metadata\":{},\"parts\":[{\"text\":\"hi\"}],\"role\":\"ROLE_USER\"}"
	if string(got) != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestCanonicalizeBytes(t *testing.T) {
	t.Parallel()
	// json.NewDecoder.UseNumber() makes the parser preserve integer literals.
	in := []byte("{\"b\": 2, \"a\": 1}")
	got, err := jcs.CanonicalizeBytes(in)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "{\"a\":1,\"b\":2}" {
		t.Fatalf("got %q", got)
	}
}

func TestCanonicalizeBytesRejectsTrailing(t *testing.T) {
	t.Parallel()
	if _, err := jcs.CanonicalizeBytes([]byte("{}{}")); err == nil {
		t.Fatal("expected error for trailing JSON")
	}
}

func TestUnsupportedType(t *testing.T) {
	t.Parallel()
	if _, err := jcs.Canonicalize(struct{ X int }{1}); !errors.Is(err, jcs.ErrInvalidInput) {
		t.Fatalf("expected ErrInvalidInput, got %v", err)
	}
}
