// SPDX-License-Identifier: MIT

package vc

import (
	"testing"
)

func TestStatusListGetSet(t *testing.T) {
	s := NewStatusList(64)
	for i := uint64(0); i < 64; i++ {
		v, err := s.Get(i)
		if err != nil {
			t.Fatalf("Get: %v", err)
		}
		if v {
			t.Fatalf("bit %d set on fresh list", i)
		}
	}
	for _, idx := range []uint64{0, 1, 7, 8, 9, 31, 63} {
		if err := s.Set(idx, true); err != nil {
			t.Fatalf("Set: %v", err)
		}
	}
	for _, idx := range []uint64{0, 1, 7, 8, 9, 31, 63} {
		v, _ := s.Get(idx)
		if !v {
			t.Fatalf("expected bit %d set", idx)
		}
	}
	for _, idx := range []uint64{2, 6, 10, 32} {
		v, _ := s.Get(idx)
		if v {
			t.Fatalf("expected bit %d clear", idx)
		}
	}
}

func TestStatusListOutOfRange(t *testing.T) {
	s := NewStatusList(8)
	if _, err := s.Get(8); err == nil {
		t.Fatal("expected out-of-range error")
	}
	if err := s.Set(8, true); err == nil {
		t.Fatal("expected out-of-range error")
	}
}

func TestStatusListEncodeDecodeRoundtrip(t *testing.T) {
	s := NewStatusList(1024)
	for _, idx := range []uint64{0, 7, 8, 100, 1023} {
		if err := s.Set(idx, true); err != nil {
			t.Fatalf("Set: %v", err)
		}
	}
	encoded, err := s.Encode()
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	dec, err := DecodeStatusList(encoded)
	if err != nil {
		t.Fatalf("DecodeStatusList: %v", err)
	}
	if dec.Size() != s.Size() {
		t.Fatalf("size mismatch: %d vs %d", dec.Size(), s.Size())
	}
	for i := uint64(0); i < s.Size(); i++ {
		got, _ := dec.Get(i)
		want, _ := s.Get(i)
		if got != want {
			t.Fatalf("bit %d roundtrip mismatch", i)
		}
	}
}

func TestStatusListDecodeRejectsBadInput(t *testing.T) {
	if _, err := DecodeStatusList("***not-base64url***"); err == nil {
		t.Fatal("expected base64 error")
	}
	if _, err := DecodeStatusList("AAAA"); err == nil {
		t.Fatal("expected gunzip error")
	}
}
