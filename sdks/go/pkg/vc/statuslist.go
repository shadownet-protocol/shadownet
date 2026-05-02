// SPDX-License-Identifier: MIT

package vc

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
)

// StatusList is a bit-indexed revocation list per the W3C BitstringStatusList
// specification (RFC-0003 §Revocation). A bit set to 1 means revoked.
//
// Encoding: the bit array is gzip-compressed and then base64url-encoded
// without padding, exactly the wire form W3C specifies for the
// `encodedList` field of a status list credential.
type StatusList struct {
	bits []byte
	size uint64
}

// NewStatusList builds an empty list large enough to address `size` indices.
// Size is rounded up to a multiple of 8 internally.
func NewStatusList(size uint64) *StatusList {
	if size == 0 {
		size = 8
	}
	n := (size + 7) / 8
	return &StatusList{bits: make([]byte, n), size: size}
}

// Size returns the number of indices the list addresses.
func (s *StatusList) Size() uint64 { return s.size }

// Get returns the bit value at idx.
func (s *StatusList) Get(idx uint64) (bool, error) {
	if idx >= s.size {
		return false, fmt.Errorf("vc: status index %d out of range (size %d)", idx, s.size)
	}
	return s.bits[idx/8]&(1<<(7-idx%8)) != 0, nil
}

// Set writes the bit value at idx.
func (s *StatusList) Set(idx uint64, val bool) error {
	if idx >= s.size {
		return fmt.Errorf("vc: status index %d out of range (size %d)", idx, s.size)
	}
	mask := byte(1 << (7 - idx%8))
	if val {
		s.bits[idx/8] |= mask
	} else {
		s.bits[idx/8] &^= mask
	}
	return nil
}

// Encode returns the gzip+base64url-encoded representation that goes into the
// encodedList field of a status list credential.
func (s *StatusList) Encode() (string, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	if _, err := gz.Write(s.bits); err != nil {
		return "", fmt.Errorf("vc: gzip status list: %w", err)
	}
	if err := gz.Close(); err != nil {
		return "", fmt.Errorf("vc: gzip close: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(buf.Bytes()), nil
}

// DecodeStatusList parses an encodedList value into a StatusList. The size in
// bits is inferred from the decompressed body length (×8).
func DecodeStatusList(encoded string) (*StatusList, error) {
	raw, err := base64.RawURLEncoding.DecodeString(encoded)
	if err != nil {
		return nil, fmt.Errorf("vc: decode status list base64: %w", err)
	}
	gr, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		return nil, fmt.Errorf("vc: gunzip: %w", err)
	}
	defer gr.Close()
	bits, err := io.ReadAll(gr)
	if err != nil {
		return nil, fmt.Errorf("vc: read gunzip: %w", err)
	}
	if len(bits) == 0 {
		return nil, errors.New("vc: empty status list")
	}
	return &StatusList{bits: bits, size: uint64(len(bits)) * 8}, nil
}

// StatusFetcher resolves a credentialStatus.statusListCredential URL to a
// usable StatusList. Implementations typically fetch + verify a status-list
// VC; pkg/sca and pkg/a2a wire concrete fetchers in.
type StatusFetcher interface {
	Fetch(ctx context.Context, statusListCredential string) (*StatusList, error)
}
