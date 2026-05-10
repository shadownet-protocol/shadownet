// SPDX-License-Identifier: MIT

package did

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

// DefaultMaxDocumentBytes caps the size of a fetched DID document at 16 KiB
// per RFC-0002 §did:web.
const DefaultMaxDocumentBytes = 16 * 1024

// DefaultCacheTTL is the cache lifetime applied when a fetched document does
// not carry a Cache-Control max-age directive (RFC-0002 §Resolution).
const DefaultCacheTTL = time.Hour

const didWebPrefix = "did:web:"

// WebResolver resolves did:web DIDs over HTTPS, with an in-memory cache.
//
// The zero value is not usable; build one via NewWebResolver.
type WebResolver struct {
	client     *http.Client
	maxBytes   int64
	defaultTTL time.Duration
	now        func() time.Time

	mu    sync.Mutex
	cache map[string]webCacheEntry
}

type webCacheEntry struct {
	doc       *Document
	expiresAt time.Time
}

// WebResolverOption configures NewWebResolver.
type WebResolverOption func(*WebResolver)

// WithHTTPClient overrides the resolver's HTTP client. Useful in tests against
// an httptest.Server (whose Client() trusts the test certificate).
func WithHTTPClient(c *http.Client) WebResolverOption {
	return func(r *WebResolver) { r.client = c }
}

// WithMaxDocumentBytes overrides the per-document size cap.
func WithMaxDocumentBytes(n int64) WebResolverOption {
	return func(r *WebResolver) { r.maxBytes = n }
}

// WithDefaultCacheTTL overrides the cache lifetime applied when the response
// has no Cache-Control max-age directive.
func WithDefaultCacheTTL(d time.Duration) WebResolverOption {
	return func(r *WebResolver) { r.defaultTTL = d }
}

// withClock is for tests that need a deterministic now().
func withClock(now func() time.Time) WebResolverOption {
	return func(r *WebResolver) { r.now = now }
}

// NewWebResolver returns a did:web resolver. The default HTTP client requires
// TLS 1.3 and follows up to 5 redirects.
func NewWebResolver(opts ...WebResolverOption) *WebResolver {
	r := &WebResolver{
		maxBytes:   DefaultMaxDocumentBytes,
		defaultTTL: DefaultCacheTTL,
		now:        time.Now,
		cache:      make(map[string]webCacheEntry),
	}
	for _, opt := range opts {
		opt(r)
	}
	if r.client == nil {
		r.client = &http.Client{
			Timeout: 10 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig:       &tls.Config{MinVersion: tls.VersionTLS13},
				ResponseHeaderTimeout: 5 * time.Second,
				IdleConnTimeout:       30 * time.Second,
			},
			CheckRedirect: func(_ *http.Request, via []*http.Request) error {
				if len(via) >= 5 {
					return errors.New("did: too many redirects")
				}
				return nil
			},
		}
	}
	return r
}

// Resolve fetches and parses the DID document for a did:web DID.
func (r *WebResolver) Resolve(ctx context.Context, did string) (*Document, error) {
	if !strings.HasPrefix(did, didWebPrefix) {
		return nil, fmt.Errorf("did: not a did:web: %q", did)
	}

	r.mu.Lock()
	if entry, ok := r.cache[did]; ok && entry.expiresAt.After(r.now()) {
		r.mu.Unlock()
		return entry.doc, nil
	}
	r.mu.Unlock()

	target, err := didWebToURL(did)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, fmt.Errorf("did: build request: %w", err)
	}
	req.Header.Set("Accept", "application/did+json, application/json")

	resp, err := r.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("did: fetch %s: %w", target, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("did: fetch %s: status %d", target, resp.StatusCode)
	}

	body, err := io.ReadAll(http.MaxBytesReader(nil, resp.Body, r.maxBytes))
	if err != nil {
		return nil, fmt.Errorf("did: read body: %w", err)
	}

	doc, err := parseDocument(body)
	if err != nil {
		return nil, err
	}
	if doc.ID != did {
		return nil, fmt.Errorf("did: document id %q does not match requested DID %q", doc.ID, did)
	}

	ttl := cacheControlMaxAge(resp.Header.Get("Cache-Control"))
	if ttl <= 0 {
		ttl = r.defaultTTL
	}

	r.mu.Lock()
	r.cache[did] = webCacheEntry{doc: doc, expiresAt: r.now().Add(ttl)}
	r.mu.Unlock()

	return doc, nil
}

// didWebToURL converts a did:web DID to the URL of its DID document, per the
// W3C did:web spec: domain colons after the first become path slashes; if no
// path is given the well-known location is used.
func didWebToURL(did string) (string, error) {
	rest := strings.TrimPrefix(did, didWebPrefix)
	if rest == "" {
		return "", errors.New("did: did:web has empty body")
	}
	parts := strings.Split(rest, ":")
	domain, err := url.PathUnescape(parts[0])
	if err != nil {
		return "", fmt.Errorf("did: decode domain: %w", err)
	}
	if domain == "" {
		return "", errors.New("did: did:web has empty domain")
	}
	if len(parts) == 1 {
		return "https://" + domain + "/.well-known/did.json", nil
	}
	pathParts := make([]string, 0, len(parts)-1)
	for _, p := range parts[1:] {
		x, err := url.PathUnescape(p)
		if err != nil {
			return "", fmt.Errorf("did: decode path segment: %w", err)
		}
		if x == "" {
			return "", errors.New("did: did:web has empty path segment")
		}
		pathParts = append(pathParts, x)
	}
	return "https://" + domain + "/" + strings.Join(pathParts, "/") + "/did.json", nil
}

// cacheControlMaxAge extracts the max-age directive in seconds, or 0 when
// absent or unparseable.
func cacheControlMaxAge(h string) time.Duration {
	if h == "" {
		return 0
	}
	for _, part := range strings.Split(h, ",") {
		part = strings.TrimSpace(part)
		if v, ok := strings.CutPrefix(part, "max-age="); ok {
			n, err := strconv.Atoi(v)
			if err != nil || n < 0 {
				return 0
			}
			return time.Duration(n) * time.Second
		}
	}
	return 0
}
