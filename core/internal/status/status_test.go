// SPDX-License-Identifier: MIT

package status_test

import (
	"errors"
	"testing"

	"github.com/shadownet-protocol/shadownet/core/internal/status"
)

func TestEmpty(t *testing.T) {
	t.Parallel()
	l := status.Empty(64)
	if l.Size() != 64 {
		t.Fatalf("Size = %d, want 64", l.Size())
	}
	rev, err := l.IsRevoked(0)
	if err != nil {
		t.Fatal(err)
	}
	if rev {
		t.Fatal("empty list should not have any revoked indices")
	}
}

func TestEmptyRoundedUp(t *testing.T) {
	t.Parallel()
	l := status.Empty(10)
	// 10 bits round up to 16 (next byte boundary).
	if l.Size() != 16 {
		t.Fatalf("Size = %d, want 16", l.Size())
	}
}

func TestEmptyZeroDefaultsToEightBits(t *testing.T) {
	t.Parallel()
	if status.Empty(0).Size() != 8 {
		t.Fatal("Empty(0) should yield 8 bits")
	}
	if status.Empty(-1).Size() != 8 {
		t.Fatal("Empty(-1) should yield 8 bits")
	}
}

func TestWithRevokedImmutable(t *testing.T) {
	t.Parallel()
	a := status.Empty(64)
	b, err := a.WithRevoked(5)
	if err != nil {
		t.Fatal(err)
	}
	got, err := a.IsRevoked(5)
	if err != nil {
		t.Fatal(err)
	}
	if got {
		t.Fatal("original list should be untouched")
	}
	got, err = b.IsRevoked(5)
	if err != nil {
		t.Fatal(err)
	}
	if !got {
		t.Fatal("modified list should have bit 5 set")
	}
}

func TestBigEndianWithinByte(t *testing.T) {
	t.Parallel()
	// Single byte with the high bit set: idx 0 should be revoked.
	l := status.Empty(8)
	l, _ = l.WithRevoked(0)
	got, _ := l.IsRevoked(0)
	if !got {
		t.Fatal("idx 0 should be the MSB of byte 0")
	}
	got, _ = l.IsRevoked(7)
	if got {
		t.Fatal("idx 7 should be the LSB of byte 0")
	}

	l2, _ := status.Empty(8).WithRevoked(7)
	got, _ = l2.IsRevoked(7)
	if !got {
		t.Fatal("idx 7 should be the LSB after WithRevoked(7)")
	}
	got, _ = l2.IsRevoked(0)
	if got {
		t.Fatal("idx 0 should remain unset")
	}
}

func TestOutOfRange(t *testing.T) {
	t.Parallel()
	l := status.Empty(8)
	if _, err := l.IsRevoked(8); !errors.Is(err, status.ErrOutOfRange) {
		t.Fatalf("expected ErrOutOfRange, got %v", err)
	}
	if _, err := l.IsRevoked(-1); !errors.Is(err, status.ErrOutOfRange) {
		t.Fatalf("expected ErrOutOfRange, got %v", err)
	}
	if _, err := l.WithRevoked(100); !errors.Is(err, status.ErrOutOfRange) {
		t.Fatalf("expected ErrOutOfRange, got %v", err)
	}
}

func TestEncodeDecodeRoundtrip(t *testing.T) {
	t.Parallel()
	original := status.Empty(1024)
	original, _ = original.WithRevoked(0)
	original, _ = original.WithRevoked(871)
	original, _ = original.WithRevoked(1023)

	encoded, err := original.Encode()
	if err != nil {
		t.Fatal(err)
	}
	if encoded == "" {
		t.Fatal("encoded form should not be empty")
	}

	decoded, err := status.Decode(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if decoded.Size() != original.Size() {
		t.Fatalf("Size %d != %d", decoded.Size(), original.Size())
	}
	for _, idx := range []int{0, 871, 1023} {
		got, err := decoded.IsRevoked(idx)
		if err != nil {
			t.Fatal(err)
		}
		if !got {
			t.Fatalf("idx %d should be revoked after roundtrip", idx)
		}
	}
}

func TestDecodeEmptyBody(t *testing.T) {
	t.Parallel()
	if _, err := status.Decode(""); !errors.Is(err, status.ErrEmpty) {
		t.Fatalf("expected ErrEmpty, got %v", err)
	}
	if _, err := status.Decode("   \n\t"); !errors.Is(err, status.ErrEmpty) {
		t.Fatalf("expected ErrEmpty for whitespace-only body, got %v", err)
	}
}

func TestDecodeBadBase64(t *testing.T) {
	t.Parallel()
	if _, err := status.Decode("not!!base64??"); err == nil {
		t.Fatal("expected error for bad base64")
	}
}

func TestDecodeBadGzip(t *testing.T) {
	t.Parallel()
	// Valid base64url but not gzip.
	if _, err := status.Decode("aGVsbG8"); err == nil {
		t.Fatal("expected error for non-gzip payload")
	}
}

func TestPaddedBase64Tolerated(t *testing.T) {
	t.Parallel()
	original, _ := status.Empty(64).WithRevoked(3)
	encoded, err := original.Encode()
	if err != nil {
		t.Fatal(err)
	}
	// Force padded base64url to mimic emitters that include trailing '='.
	padded := encoded
	for len(padded)%4 != 0 {
		padded += "="
	}
	if _, err := status.Decode(padded); err != nil {
		t.Fatalf("padded base64url should be tolerated: %v", err)
	}
}
