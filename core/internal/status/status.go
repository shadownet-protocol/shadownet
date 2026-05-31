// SPDX-License-Identifier: MIT

// Package status encodes and decodes the per-epoch revocation bitstring
// served by the Issuer at /.well-known/shadownet/status/<epoch>. The wire
// shape is gzip-compressed raw bitstring, base64url-encoded (no padding)
// as a single ASCII string with Content-Type text/plain.
//
// Bit indexing is big-endian within each byte: bit at index 0 is the most
// significant bit of byte 0. This matches the W3C BitstringStatusList
// convention and the v0.1 Go reference (formerly at pkg/vc/statuslist.go).
//
// Spec: RFC 0001 §6.4.
package status

import (
	"bytes"
	"compress/gzip"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
)

// MaxBodyBytes caps the size of a status list we'll decode. 8 MiB addresses
// 64M credential indices and is far larger than any production deployment
// should need with the §6.4 RECOMMENDED epoch rotation.
const MaxBodyBytes = 8 * 1024 * 1024

// ErrOutOfRange is returned by List.IsRevoked / List.WithRevoked when idx
// exceeds the bitstring size.
var ErrOutOfRange = errors.New("status: index out of range")

// ErrEmpty is returned by Decode when the bitstring decompresses to zero
// bytes.
var ErrEmpty = errors.New("status: bitstring is empty")

// ErrTooLarge is returned by Decode when the gzip-decoded bitstring would
// exceed MaxBodyBytes.
var ErrTooLarge = errors.New("status: bitstring exceeds size cap")

// List is an immutable revocation bitstring. Use Empty to build a fresh
// one, WithRevoked to set a bit, IsRevoked to check, Encode/Decode to
// move on/off the wire.
type List struct {
	bits []byte
	size int
}

// Empty returns a List large enough to address `size` credential indices.
// size is rounded up to a byte boundary (the List.Size accessor reports
// the rounded value).
func Empty(size int) List {
	if size <= 0 {
		size = 8
	}
	bytes := (size + 7) / 8
	return List{bits: make([]byte, bytes), size: bytes * 8}
}

// FromRaw wraps an existing bitstring byte slice (most often returned by
// a Store's LoadStatusBits) in a List. The caller MUST NOT mutate `bits`
// after calling FromRaw — the slice is shared, not copied.
func FromRaw(bits []byte) List {
	return List{bits: bits, size: len(bits) * 8}
}

// Size returns the number of bit-indices the List addresses.
func (l List) Size() int { return l.size }

// IsRevoked reports whether bit idx is set in the bitstring.
func (l List) IsRevoked(idx int) (bool, error) {
	if idx < 0 || idx >= l.size {
		return false, fmt.Errorf("%w: idx %d not in [0,%d)", ErrOutOfRange, idx, l.size)
	}
	return (l.bits[idx/8]>>(7-uint(idx%8)))&1 == 1, nil
}

// WithRevoked returns a new List with bit idx set to 1. The receiver is not
// modified.
func (l List) WithRevoked(idx int) (List, error) {
	if idx < 0 || idx >= l.size {
		return List{}, fmt.Errorf("%w: idx %d not in [0,%d)", ErrOutOfRange, idx, l.size)
	}
	buf := make([]byte, len(l.bits))
	copy(buf, l.bits)
	buf[idx/8] |= 1 << (7 - uint(idx%8))
	return List{bits: buf, size: l.size}, nil
}

// Encode produces the wire form: gzip-compressed bitstring, base64url
// (no padding) as ASCII.
func (l List) Encode() (string, error) {
	var buf bytes.Buffer
	gz, err := gzip.NewWriterLevel(&buf, gzip.BestCompression)
	if err != nil {
		return "", fmt.Errorf("status: new gzip writer: %w", err)
	}
	if _, err := gz.Write(l.bits); err != nil {
		return "", fmt.Errorf("status: gzip write: %w", err)
	}
	if err := gz.Close(); err != nil {
		return "", fmt.Errorf("status: gzip close: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(buf.Bytes()), nil
}

// Decode parses the wire form back into a List. Empty bodies, malformed
// base64, malformed gzip, and decoded bitstrings larger than MaxBodyBytes
// all return an error — the §6.4 fail-closed contract.
func Decode(body string) (List, error) {
	body = trimSpace(body)
	if body == "" {
		return List{}, ErrEmpty
	}
	compressed, err := base64.RawURLEncoding.DecodeString(body)
	if err != nil {
		// Some emitters include padding; tolerate that.
		compressed2, err2 := base64.URLEncoding.DecodeString(body)
		if err2 != nil {
			return List{}, fmt.Errorf("status: base64 decode: %w", err)
		}
		compressed = compressed2
	}
	gz, err := gzip.NewReader(bytes.NewReader(compressed))
	if err != nil {
		return List{}, fmt.Errorf("status: gzip reader: %w", err)
	}
	defer func() { _ = gz.Close() }()
	bits, err := io.ReadAll(io.LimitReader(gz, MaxBodyBytes+1))
	if err != nil {
		return List{}, fmt.Errorf("status: gunzip read: %w", err)
	}
	if len(bits) > MaxBodyBytes {
		return List{}, ErrTooLarge
	}
	if len(bits) == 0 {
		return List{}, ErrEmpty
	}
	return List{bits: bits, size: len(bits) * 8}, nil
}

func trimSpace(s string) string {
	out := s
	for len(out) > 0 && (out[0] == ' ' || out[0] == '\t' || out[0] == '\n' || out[0] == '\r') {
		out = out[1:]
	}
	for len(out) > 0 {
		c := out[len(out)-1]
		if c != ' ' && c != '\t' && c != '\n' && c != '\r' {
			break
		}
		out = out[:len(out)-1]
	}
	return out
}
