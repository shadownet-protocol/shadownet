// SPDX-License-Identifier: MIT

// Package identifiers parses and validates the three identifier forms
// Shadownet v0.2 carries on the wire: Domain, Shadowname, and a multibase
// Ed25519 public key (the "keyed" form). The Class function classifies a
// bare string; the parse helpers canonicalize and reject invalid inputs.
//
// Spec: RFC 0001 §3 (identifier grammar), §3.3 (wire-internal bare form),
// §5.1 (Shadowname grammar). The schemas at shadownet-specs/schemas widen
// `iss`, `sub`, `org`, `aud` in credential.schema.json and csr.schema.json
// to accept either a hostname or a `z6Mk...` multibase Ed25519 public key.
package identifiers

import (
	"crypto/ed25519"
	"errors"
	"fmt"
	"strings"
	"unicode"
)

// ErrInvalid wraps every parse/validate failure in this package.
var ErrInvalid = errors.New("identifiers: invalid")

// Class is the classification of a bare identifier as it appears in wire
// fields like envelope `from`/`to`, credential `iss`/`sub`/`org`, CSR
// `iss`/`aud`/`req.org`, and trust-store entries.
type Class int

const (
	// ClassUnknown is returned for inputs that don't match any of the three
	// recognized forms.
	ClassUnknown Class = iota
	// ClassDomain is a DNS-resolvable hostname (RFC 1035 / RFC 5891).
	ClassDomain
	// ClassShadowname is local@provider per RFC 0001 §5.1.
	ClassShadowname
	// ClassPubKey is a multibase Ed25519 public key (z6Mk… per the multibase
	// + 0xed01 multicodec convention).
	ClassPubKey
)

// String returns the lowercase Class name (for diagnostics, not the wire).
func (c Class) String() string {
	switch c {
	case ClassDomain:
		return "domain"
	case ClassShadowname:
		return "shadowname"
	case ClassPubKey:
		return "pubkey"
	default:
		return "unknown"
	}
}

// Classify returns the form of a wire-internal identifier string.
//
// The disambiguation rule from RFC 0001 §3.3: contains "@" → Shadowname;
// starts with the multibase z6Mk prefix → public key; otherwise → domain
// (subject to the syntactic validity of each form below).
func Classify(s string) Class {
	switch {
	case strings.Contains(s, "@"):
		if ValidateShadowname(s) == nil {
			return ClassShadowname
		}
	case strings.HasPrefix(s, "z6Mk"):
		if ValidatePubKey(s) == nil {
			return ClassPubKey
		}
	default:
		if ValidateDomain(s) == nil {
			return ClassDomain
		}
	}
	return ClassUnknown
}

// IsShadowname reports whether s is a syntactically valid Shadowname.
func IsShadowname(s string) bool { return Classify(s) == ClassShadowname }

// IsDomain reports whether s is a syntactically valid domain.
func IsDomain(s string) bool { return Classify(s) == ClassDomain }

// IsPubKey reports whether s is a syntactically valid multibase Ed25519
// public key.
func IsPubKey(s string) bool { return Classify(s) == ClassPubKey }

// ValidateShadowname checks the local@provider grammar from RFC 0001 §5.1.
//
//	shadowname  =  local "@" provider
//	local       =  1*63 ( ALPHA / DIGIT / "_" / "-" / "." )
//	provider    =  domain
//
// Shadownames are case-insensitive on the local part; callers SHOULD pass
// the canonical (lowercase) form to ValidateShadowname or call
// CanonicalShadowname instead.
func ValidateShadowname(s string) error {
	at := strings.IndexByte(s, '@')
	if at < 0 {
		return fmt.Errorf("%w: shadowname missing '@': %q", ErrInvalid, s)
	}
	local, provider := s[:at], s[at+1:]
	if err := validateShadownameLocal(local); err != nil {
		return err
	}
	return ValidateDomain(provider)
}

// CanonicalShadowname lowercases the local part of a syntactically-valid
// Shadowname per RFC 0001 §2 (Shadownames are case-insensitive on the
// local part; canonical form is lowercase). The provider portion is also
// lowercased — domains are case-insensitive per RFC 4343.
func CanonicalShadowname(s string) (string, error) {
	at := strings.IndexByte(s, '@')
	if at < 0 {
		return "", fmt.Errorf("%w: shadowname missing '@': %q", ErrInvalid, s)
	}
	local, provider := s[:at], s[at+1:]
	if err := validateShadownameLocal(local); err != nil {
		return "", err
	}
	if err := ValidateDomain(provider); err != nil {
		return "", err
	}
	return strings.ToLower(local) + "@" + strings.ToLower(provider), nil
}

// SplitShadowname returns the (local, provider) parts of a canonical
// Shadowname or an error if s is malformed.
func SplitShadowname(s string) (local, provider string, err error) {
	at := strings.IndexByte(s, '@')
	if at < 0 {
		return "", "", fmt.Errorf("%w: shadowname missing '@': %q", ErrInvalid, s)
	}
	local, provider = s[:at], s[at+1:]
	if err := validateShadownameLocal(local); err != nil {
		return "", "", err
	}
	if err := ValidateDomain(provider); err != nil {
		return "", "", err
	}
	return local, provider, nil
}

func validateShadownameLocal(local string) error {
	if n := len(local); n < 1 || n > 63 {
		return fmt.Errorf("%w: shadowname local length %d not in [1,63]", ErrInvalid, n)
	}
	for _, r := range local {
		switch {
		case r >= 'A' && r <= 'Z':
		case r >= 'a' && r <= 'z':
		case r >= '0' && r <= '9':
		case r == '_' || r == '-' || r == '.':
		default:
			return fmt.Errorf("%w: shadowname local has invalid rune %q", ErrInvalid, r)
		}
	}
	return nil
}

// ValidateDomain checks the host-name shape RFC 1035 §2.3.1 / RFC 5891
// (IDNA2008). It does not perform a DNS lookup. Trailing dots and
// uppercase letters are accepted; CanonicalDomain returns the lowercase
// form with trailing dot stripped.
func ValidateDomain(s string) error {
	s = strings.TrimSuffix(s, ".")
	if n := len(s); n < 1 || n > 253 {
		return fmt.Errorf("%w: domain length %d not in [1,253]", ErrInvalid, n)
	}
	for _, label := range strings.Split(s, ".") {
		if err := validateDomainLabel(label); err != nil {
			return err
		}
	}
	return nil
}

// CanonicalDomain returns the lowercase form with any trailing dot stripped.
// It does not perform IDNA processing for non-ASCII inputs; the caller is
// responsible for converting punycode upstream of the wire.
func CanonicalDomain(s string) (string, error) {
	if err := ValidateDomain(s); err != nil {
		return "", err
	}
	return strings.ToLower(strings.TrimSuffix(s, ".")), nil
}

func validateDomainLabel(label string) error {
	n := len(label)
	if n < 1 || n > 63 {
		return fmt.Errorf("%w: domain label length %d not in [1,63]", ErrInvalid, n)
	}
	for i, r := range label {
		switch {
		case r >= '0' && r <= '9':
		case r >= 'A' && r <= 'Z':
		case r >= 'a' && r <= 'z':
		case r == '-':
			if i == 0 || i == n-1 {
				return fmt.Errorf("%w: domain label %q starts or ends with '-'", ErrInvalid, label)
			}
		default:
			if !unicode.IsLetter(r) && !unicode.IsDigit(r) {
				return fmt.Errorf("%w: domain label %q has invalid rune %q", ErrInvalid, label, r)
			}
		}
	}
	return nil
}

// ValidatePubKey checks that s is the multibase form (z + base58btc) of an
// Ed25519 public key with multicodec prefix 0xed01 ("z6Mk…"). Wraps
// DecodePubKey for callers that need a yes/no answer.
func ValidatePubKey(s string) error {
	_, err := DecodePubKey(s)
	return err
}

// IsSubdomainOf returns true if `candidate` equals `parent` or is a strict
// sub-domain of it. Used by the §6.6 authorization check when both `iss`
// and `org` are domains.
func IsSubdomainOf(candidate, parent string) bool {
	c, err := CanonicalDomain(candidate)
	if err != nil {
		return false
	}
	p, err := CanonicalDomain(parent)
	if err != nil {
		return false
	}
	return c == p || strings.HasSuffix(c, "."+p)
}

// EncodePubKey returns the multibase z + base58btc form of an Ed25519
// public key with the 0xed01 multicodec prefix, i.e. the "z6Mk…" wire
// form Shadownet uses everywhere it needs a key identifier.
func EncodePubKey(pub ed25519.PublicKey) (string, error) {
	if n := len(pub); n != ed25519.PublicKeySize {
		return "", fmt.Errorf("%w: ed25519 pubkey must be %d bytes, got %d", ErrInvalid, ed25519.PublicKeySize, n)
	}
	buf := make([]byte, 0, 2+ed25519.PublicKeySize)
	buf = append(buf, ed25519PubMulticodec...)
	buf = append(buf, pub...)
	return "z" + b58encode(buf), nil
}

// DecodePubKey reverses EncodePubKey: parses a "z6Mk…" string back into
// the raw 32-byte Ed25519 public key.
func DecodePubKey(s string) (ed25519.PublicKey, error) {
	if !strings.HasPrefix(s, "z6Mk") {
		return nil, fmt.Errorf("%w: pubkey must start with z6Mk: %q", ErrInvalid, s)
	}
	raw, err := b58decode(s[1:])
	if err != nil {
		return nil, fmt.Errorf("%w: base58 decode: %v", ErrInvalid, err)
	}
	if len(raw) < len(ed25519PubMulticodec)+ed25519.PublicKeySize {
		return nil, fmt.Errorf("%w: pubkey payload too short", ErrInvalid)
	}
	for i, b := range ed25519PubMulticodec {
		if raw[i] != b {
			return nil, fmt.Errorf("%w: missing 0xed01 multicodec prefix", ErrInvalid)
		}
	}
	key := raw[len(ed25519PubMulticodec):]
	if len(key) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("%w: pubkey payload length %d, want %d", ErrInvalid, len(key), ed25519.PublicKeySize)
	}
	out := make(ed25519.PublicKey, ed25519.PublicKeySize)
	copy(out, key)
	return out, nil
}

// ed25519PubMulticodec is the unsigned-varint encoding of 0xED (Ed25519
// public key) per https://github.com/multiformats/multicodec.
var ed25519PubMulticodec = []byte{0xed, 0x01}

// base58btc alphabet (RFC 0001 §3.1: "multibase-encoded Ed25519 (base58btc)").
const b58Alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

func b58encode(data []byte) string {
	if len(data) == 0 {
		return ""
	}
	// Count leading zero bytes — encoded as '1' (alphabet index 0).
	leading := 0
	for leading < len(data) && data[leading] == 0 {
		leading++
	}
	// Big-endian integer division by 58.
	in := append([]byte(nil), data...)
	out := make([]byte, 0, len(data)*138/100+1)
	for start := leading; start < len(in); {
		rem := 0
		for i := start; i < len(in); i++ {
			acc := rem*256 + int(in[i])
			in[i] = byte(acc / 58)
			rem = acc % 58
		}
		out = append(out, b58Alphabet[rem])
		for start < len(in) && in[start] == 0 {
			start++
		}
	}
	for i := 0; i < leading; i++ {
		out = append(out, b58Alphabet[0])
	}
	// Reverse.
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return string(out)
}

func b58decode(s string) ([]byte, error) {
	if s == "" {
		return nil, nil
	}
	idx := make(map[byte]int, len(b58Alphabet))
	for i := 0; i < len(b58Alphabet); i++ {
		idx[b58Alphabet[i]] = i
	}
	leading := 0
	for leading < len(s) && s[leading] == b58Alphabet[0] {
		leading++
	}
	out := make([]byte, 0, len(s))
	for i := leading; i < len(s); i++ {
		v, ok := idx[s[i]]
		if !ok {
			return nil, fmt.Errorf("invalid base58 character %q at offset %d", s[i], i)
		}
		carry := v
		for j := range out {
			carry += int(out[j]) * 58
			out[j] = byte(carry & 0xff)
			carry >>= 8
		}
		for carry > 0 {
			out = append(out, byte(carry&0xff))
			carry >>= 8
		}
	}
	// Reverse and prepend leading zeros.
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	full := make([]byte, leading, leading+len(out))
	return append(full, out...), nil
}
