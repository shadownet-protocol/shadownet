// SPDX-License-Identifier: MIT

package sns

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/did"
)

// DefaultMaxRecordBytes caps a single record body. Records are tens of bytes
// in practice; 16 KiB matches did:web's cap and is generous.
const DefaultMaxRecordBytes = 16 * 1024

// NegativeCacheTTL is RFC-0005's bound on negative-response caching.
const NegativeCacheTTL = 60 * time.Second

// ResolvePath is the well-known URL prefix for resolution per RFC-0005.
const ResolvePath = "/.well-known/sns/v1/resolve"

// Resolver fetches and verifies SNS records. It caches positive results
// until exp and negative results for at most NegativeCacheTTL.
type Resolver struct {
	Client      *http.Client
	DIDResolver did.Resolver
	Now         func() time.Time
	MaxBytes    int64

	mu       sync.Mutex
	posCache map[string]positiveCacheEntry
	negCache map[string]negativeCacheEntry
}

type positiveCacheEntry struct {
	record    *SignedRecord
	expiresAt time.Time
}

type negativeCacheEntry struct {
	expiresAt time.Time
	tombstone bool
}

// NewResolver builds a Resolver with safe defaults: TLS 1.3 and 10s timeout.
// Caller MUST set DIDResolver.
func NewResolver(didR did.Resolver) *Resolver {
	return &Resolver{
		Client: &http.Client{
			Timeout: 10 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig:       &tls.Config{MinVersion: tls.VersionTLS13},
				ResponseHeaderTimeout: 5 * time.Second,
				IdleConnTimeout:       30 * time.Second,
			},
		},
		DIDResolver: didR,
		Now:         time.Now,
		MaxBytes:    DefaultMaxRecordBytes,
		posCache:    make(map[string]positiveCacheEntry),
		negCache:    make(map[string]negativeCacheEntry),
	}
}

// ErrTombstoned is returned when the upstream provider has 410'd a name.
var ErrTombstoned = errors.New("sns: record tombstoned")

// Resolve looks up a Shadowname.
func (r *Resolver) Resolve(ctx context.Context, shadowname string) (*SignedRecord, error) {
	if r.DIDResolver == nil {
		return nil, errors.New("sns: Resolver.DIDResolver required")
	}
	now := r.now()
	canon, err := ParseShadowname(shadowname)
	if err != nil {
		return nil, err
	}
	canonStr := canon.String()

	r.mu.Lock()
	if e, ok := r.posCache[canonStr]; ok && e.expiresAt.After(now) {
		r.mu.Unlock()
		return e.record, nil
	}
	if n, ok := r.negCache[canonStr]; ok && n.expiresAt.After(now) {
		r.mu.Unlock()
		if n.tombstone {
			return nil, ErrTombstoned
		}
		return nil, ErrRecordNotFound
	}
	r.mu.Unlock()

	url := "https://" + canon.Provider + ResolvePath + "?name=" + canonStr
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("sns: build request: %w", err)
	}
	req.Header.Set("Accept", "application/jwt")
	resp, err := r.Client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("sns: fetch %s: %w", url, err)
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
		// fall through
	case http.StatusNotFound:
		r.cacheNegative(canonStr, false, now)
		return nil, ErrRecordNotFound
	case http.StatusGone:
		r.cacheNegative(canonStr, true, now)
		return nil, ErrTombstoned
	case http.StatusTooManyRequests:
		return nil, fmt.Errorf("sns: %s: rate limited", url)
	default:
		return nil, fmt.Errorf("sns: %s: status %d", url, resp.StatusCode)
	}

	body, err := io.ReadAll(http.MaxBytesReader(nil, resp.Body, r.MaxBytes))
	if err != nil {
		return nil, fmt.Errorf("sns: read body: %w", err)
	}
	rec, err := VerifyRecord(ctx, r.DIDResolver, string(body), canonStr, now)
	if err != nil {
		return nil, err
	}
	if cc := resp.Header.Get("Cache-Control"); strings.Contains(cc, "no-store") {
		return rec, nil
	}
	r.mu.Lock()
	r.posCache[canonStr] = positiveCacheEntry{record: rec, expiresAt: rec.Expires}
	r.mu.Unlock()
	return rec, nil
}

func (r *Resolver) now() time.Time {
	if r.Now != nil {
		return r.Now()
	}
	return time.Now().UTC()
}

func (r *Resolver) cacheNegative(name string, tombstone bool, now time.Time) {
	r.mu.Lock()
	r.negCache[name] = negativeCacheEntry{expiresAt: now.Add(NegativeCacheTTL), tombstone: tombstone}
	r.mu.Unlock()
}
