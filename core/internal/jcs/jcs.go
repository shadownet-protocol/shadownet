// SPDX-License-Identifier: MIT

// Package jcs implements RFC 8785 — JSON Canonicalization Scheme — for the
// subset of JSON Shadownet uses on the wire (objects, arrays, strings,
// integers, booleans, null). Floating-point numbers are intentionally
// rejected: ECMA-262 ToString divergence is the single most subtle source
// of cross-impl JCS bugs, and Shadownet wire artefacts (AgentCards,
// envelopes, credentials, CSRs) never carry floats.
//
// The implementation is paired with the conformance corpus at
// conformance/fixtures/cross/jcs/ (added in Phase 2) so byte-for-byte
// interop with python-sdk's shadownet.jcs.canonicalize is verified on
// every CI run, not just inside this package's own tests.
//
// Spec: RFC 8785. The relevant sections are cited at the point that
// implements each rule.
package jcs

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// ErrFloatUnsupported is returned by Canonicalize when the input contains a
// non-integer JSON Number. Shadownet wire artefacts never carry floats;
// this restriction trades general-purpose JCS coverage for a guarantee of
// bit-exact cross-impl behaviour.
var ErrFloatUnsupported = errors.New("jcs: float canonicalization is intentionally unsupported")

// ErrInvalidInput is returned for inputs JCS rejects per RFC 8785: lone
// surrogates in strings, non-string object keys, NaN/Infinity, unsupported
// value types.
var ErrInvalidInput = errors.New("jcs: invalid input")

// Canonicalize returns the RFC 8785 canonical JSON encoding of v.
//
// v MUST be a value the standard library's encoding/json package can emit
// (map[string]any, []any, string, json.Number, bool, nil, or any value
// json.Unmarshal would produce when decoding into an interface{} target).
// Floats are rejected with ErrFloatUnsupported.
func Canonicalize(v any) ([]byte, error) {
	var b strings.Builder
	if err := encode(&b, v); err != nil {
		return nil, err
	}
	return []byte(b.String()), nil
}

// CanonicalizeBytes accepts a JSON document (as encoding/json would parse it)
// and returns its canonical form. Convenience wrapper around json.Unmarshal
// + Canonicalize.
func CanonicalizeBytes(doc []byte) ([]byte, error) {
	dec := json.NewDecoder(strings.NewReader(string(doc)))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, fmt.Errorf("jcs: parse input: %w", err)
	}
	if dec.More() {
		return nil, fmt.Errorf("%w: trailing JSON after document", ErrInvalidInput)
	}
	return Canonicalize(v)
}

func encode(b *strings.Builder, v any) error {
	switch x := v.(type) {
	case nil:
		b.WriteString("null")
		return nil
	case bool:
		if x {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
		return nil
	case string:
		return encodeString(b, x)
	case json.Number:
		return encodeNumber(b, x.String())
	case float32:
		return encodeNumberFloat(b, float64(x))
	case float64:
		return encodeNumberFloat(b, x)
	case int:
		b.WriteString(strconv.FormatInt(int64(x), 10))
		return nil
	case int64:
		b.WriteString(strconv.FormatInt(x, 10))
		return nil
	case uint64:
		b.WriteString(strconv.FormatUint(x, 10))
		return nil
	case []any:
		return encodeArray(b, x)
	case map[string]any:
		return encodeObject(b, x)
	default:
		return fmt.Errorf("%w: unsupported type %T", ErrInvalidInput, v)
	}
}

func encodeArray(b *strings.Builder, arr []any) error {
	b.WriteByte('[')
	for i, item := range arr {
		if i > 0 {
			b.WriteByte(',')
		}
		if err := encode(b, item); err != nil {
			return err
		}
	}
	b.WriteByte(']')
	return nil
}

// encodeObject implements RFC 8785 §3.2.3: property names are sorted by
// their UTF-16 code-unit representation interpreted as an array of unsigned
// integers.
func encodeObject(b *strings.Builder, obj map[string]any) error {
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		return utf16Less(keys[i], keys[j])
	})
	b.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(',')
		}
		if err := encodeString(b, k); err != nil {
			return err
		}
		b.WriteByte(':')
		if err := encode(b, obj[k]); err != nil {
			return err
		}
	}
	b.WriteByte('}')
	return nil
}

// utf16Less returns true if a sorts before b under UTF-16 code-unit
// ordering. Strings are converted to []uint16 once and compared as unsigned
// integer sequences.
func utf16Less(a, b string) bool {
	ua := utf16.Encode([]rune(a))
	ub := utf16.Encode([]rune(b))
	n := len(ua)
	if len(ub) < n {
		n = len(ub)
	}
	for i := 0; i < n; i++ {
		if ua[i] != ub[i] {
			return ua[i] < ub[i]
		}
	}
	return len(ua) < len(ub)
}

// encodeString implements RFC 8785 §3.2.2.2. Lone surrogates and any other
// malformed UTF-8 byte sequence (including CESU-8 surrogate halves smuggled
// in via Java-style encoding) terminate the canonicalization.
func encodeString(b *strings.Builder, s string) error {
	if !utf8.ValidString(s) {
		// utf8.ValidString catches lone surrogates: their CESU-8 encoding
		// (0xED 0xA0-0xBF 0x80-0xBF) is not valid UTF-8 either.
		return fmt.Errorf("%w: input string is not valid UTF-8 (lone surrogate or malformed sequence)", ErrInvalidInput)
	}
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if r < 0x20 {
				fmt.Fprintf(b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return nil
}

// encodeNumber handles a json.Number string. Shadownet only canonicalizes
// integers, so a non-integer literal is rejected.
func encodeNumber(b *strings.Builder, lit string) error {
	if lit == "" {
		return fmt.Errorf("%w: empty number literal", ErrInvalidInput)
	}
	if strings.ContainsAny(lit, ".eE") {
		return ErrFloatUnsupported
	}
	if _, err := strconv.ParseInt(lit, 10, 64); err != nil {
		// Fall back to big-integer-aware parse: very large integers can be
		// out of int64 range but still valid JSON integers. Accept the
		// literal verbatim if it's a syntactically valid integer.
		if !isIntegerLiteral(lit) {
			return fmt.Errorf("%w: malformed integer %q: %v", ErrInvalidInput, lit, err)
		}
	}
	b.WriteString(lit)
	return nil
}

func encodeNumberFloat(b *strings.Builder, f float64) error {
	_ = b
	_ = f
	return ErrFloatUnsupported
}

func isIntegerLiteral(s string) bool {
	if s == "" {
		return false
	}
	i := 0
	if s[0] == '-' {
		i = 1
		if len(s) == 1 {
			return false
		}
	}
	for ; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}
