// SPDX-License-Identifier: MIT

package a2a

import (
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
)

// TestSweepVPCacheRemovesExpired exercises the sweeper directly: a server
// with a mix of past- and future-expiry entries should retain only the
// future ones after SweepVPCache is called.
func TestSweepVPCacheRemovesExpired(t *testing.T) {
	now := time.Date(2026, 5, 1, 12, 0, 0, 0, time.UTC)
	s := &Server{
		Verifier: &vc.Verifier{FreshnessWindow: time.Hour},
		Now:      func() time.Time { return now },
		vps:      make(map[string]cachedVP),
	}
	for i := 0; i < 5; i++ {
		// Past expiry: should be evicted.
		s.vps[expiredKey(i)] = cachedVP{expiresAt: now.Add(-time.Minute)}
	}
	for i := 0; i < 5; i++ {
		// Future expiry: should remain.
		s.vps[freshKey(i)] = cachedVP{expiresAt: now.Add(time.Hour)}
	}

	s.SweepVPCache()

	if got := len(s.vps); got != 5 {
		t.Fatalf("after sweep, len(vps) = %d, want 5", got)
	}
	for i := 0; i < 5; i++ {
		if _, ok := s.vps[expiredKey(i)]; ok {
			t.Fatalf("expired entry %s survived sweep", expiredKey(i))
		}
		if _, ok := s.vps[freshKey(i)]; !ok {
			t.Fatalf("fresh entry %s evicted by sweep", freshKey(i))
		}
	}
}

// TestVPCacheAutoSweep confirms that vpSweepInterval cache writes trigger
// an automatic sweep without an explicit SweepVPCache call.
func TestVPCacheAutoSweep(t *testing.T) {
	now := time.Date(2026, 5, 1, 12, 0, 0, 0, time.UTC)
	s := &Server{
		Verifier: &vc.Verifier{FreshnessWindow: time.Hour},
		Now:      func() time.Time { return now },
	}
	// Seed an expired entry.
	s.cacheVP("did:key:zStale", nil)
	s.mu.Lock()
	s.vps["did:key:zStale"] = cachedVP{expiresAt: now.Add(-time.Hour)}
	s.mu.Unlock()

	// One more write below the threshold leaves the stale entry in place.
	for i := 0; i < vpSweepInterval-2; i++ {
		s.cacheVP(freshKey(i), nil)
	}
	s.mu.Lock()
	if _, ok := s.vps["did:key:zStale"]; !ok {
		s.mu.Unlock()
		t.Fatal("stale entry evicted before threshold")
	}
	s.mu.Unlock()

	// One more write hits exactly vpSweepInterval and triggers the sweep.
	s.cacheVP(freshKey(vpSweepInterval), nil)

	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.vps["did:key:zStale"]; ok {
		t.Fatal("stale entry should have been swept once writes reached vpSweepInterval")
	}
}

func expiredKey(i int) string { return "did:key:zExpired-" + suffix(i) }
func freshKey(i int) string   { return "did:key:zFresh-" + suffix(i) }

func suffix(i int) string {
	const hex = "0123456789abcdef"
	if i < 16 {
		return string(hex[i])
	}
	return string(hex[i/16]) + string(hex[i%16])
}
