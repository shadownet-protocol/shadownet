// SPDX-License-Identifier: MIT

package issuer

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
)

// ErrNotAuthorized is the §6.6 outcome surfaced to callers when the
// candidate issuer is not authorized for the named org.
var ErrNotAuthorized = errors.New("issuer: iss not authorized for org per §6.6")

// AuthzConfig wires the §6.6 check into the Issuer.
type AuthzConfig struct {
	// Resolver is the DNS resolver used for the delegate= TXT lookup
	// (RFC 0001 §6.6 path 3). nil → net.DefaultResolver.
	Resolver *net.Resolver

	// LookupTimeout caps each DNS lookup. 0 → 2 seconds.
	LookupTimeout time.Duration

	// PositiveCacheTTL caches successful TXT lookups. 0 → 60 seconds.
	PositiveCacheTTL time.Duration

	// NegativeCacheTTL caches NXDOMAIN / no-answer / timeout results. 0
	// → 10 seconds. Kept short so a freshly added delegate becomes
	// available without an Issuer restart.
	NegativeCacheTTL time.Duration

	// Now overrides time.Now for deterministic tests.
	Now func() time.Time
}

// Authorizer implements the §6.6 affiliation-issuer authorization check
// with a small in-memory positive/negative cache so repeated CSRs from the
// same issuer don't hammer DNS.
type Authorizer struct {
	cfg AuthzConfig
	mu  sync.Mutex
	pos map[string]cacheEntry // org → delegate list snapshot
	neg map[string]time.Time  // org → "don't retry before" timestamp
}

type cacheEntry struct {
	delegates []string
	expiresAt time.Time
}

// NewAuthorizer returns an Authorizer with the given config. Zero values
// in cfg are populated with defaults.
func NewAuthorizer(cfg AuthzConfig) *Authorizer {
	if cfg.Resolver == nil {
		cfg.Resolver = net.DefaultResolver
	}
	if cfg.LookupTimeout == 0 {
		cfg.LookupTimeout = 2 * time.Second
	}
	if cfg.PositiveCacheTTL == 0 {
		cfg.PositiveCacheTTL = 60 * time.Second
	}
	if cfg.NegativeCacheTTL == 0 {
		cfg.NegativeCacheTTL = 10 * time.Second
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	return &Authorizer{
		cfg: cfg,
		pos: make(map[string]cacheEntry),
		neg: make(map[string]time.Time),
	}
}

// Authorize implements §6.6:
//
//  1. iss == org              — always accepted. This is the ONLY path
//     available to key-identified issuers (per the spec carve-out).
//  2. iss is a sub-domain of  — accepted only when BOTH iss and org are
//     org's domain               domains.
//  3. iss is listed in the    — accepted only when org is a domain.
//     _shadownet.<org-domain>     The Resolver is consulted with the
//     TXT record under            configured timeout + cache.
//     delegate= keys.
func (a *Authorizer) Authorize(ctx context.Context, iss, org string) error {
	issClass := identifiers.Classify(iss)
	orgClass := identifiers.Classify(org)

	if issClass == identifiers.ClassUnknown || orgClass == identifiers.ClassUnknown {
		return fmt.Errorf("%w: unknown identifier class (iss=%v org=%v)", ErrNotAuthorized, issClass, orgClass)
	}

	// Rule 1: iss == org. For domains, compare canonicalized forms.
	if matchesIssEqOrg(iss, issClass, org, orgClass) {
		return nil
	}

	// When either side is a key, only rule 1 applies (§6.6 keyed-issuer
	// carve-out).
	if issClass == identifiers.ClassPubKey || orgClass == identifiers.ClassPubKey {
		return fmt.Errorf("%w: keyed issuer requires iss == org", ErrNotAuthorized)
	}

	// Rules 2 + 3 require both sides to be domains.
	if identifiers.IsSubdomainOf(iss, org) {
		return nil
	}

	delegates, err := a.lookupDelegates(ctx, org)
	if err != nil {
		return fmt.Errorf("%w: delegate lookup: %v", ErrNotAuthorized, err)
	}
	issLower := strings.ToLower(strings.TrimSuffix(iss, "."))
	for _, d := range delegates {
		if strings.ToLower(strings.TrimSuffix(d, ".")) == issLower {
			return nil
		}
	}
	return fmt.Errorf("%w: iss=%q org=%q", ErrNotAuthorized, iss, org)
}

func matchesIssEqOrg(iss string, issClass identifiers.Class, org string, orgClass identifiers.Class) bool {
	if issClass != orgClass {
		return false
	}
	switch issClass {
	case identifiers.ClassDomain:
		c1, e1 := identifiers.CanonicalDomain(iss)
		c2, e2 := identifiers.CanonicalDomain(org)
		return e1 == nil && e2 == nil && c1 == c2
	case identifiers.ClassPubKey:
		return iss == org
	default:
		return false
	}
}

func (a *Authorizer) lookupDelegates(ctx context.Context, org string) ([]string, error) {
	canonicalOrg, err := identifiers.CanonicalDomain(org)
	if err != nil {
		return nil, err
	}

	now := a.cfg.Now()
	a.mu.Lock()
	if entry, ok := a.pos[canonicalOrg]; ok && entry.expiresAt.After(now) {
		a.mu.Unlock()
		return entry.delegates, nil
	}
	if expiry, ok := a.neg[canonicalOrg]; ok && expiry.After(now) {
		a.mu.Unlock()
		return nil, errors.New("recent negative cache: no delegates / lookup failed")
	}
	a.mu.Unlock()

	lookupCtx, cancel := context.WithTimeout(ctx, a.cfg.LookupTimeout)
	defer cancel()
	txt, err := a.cfg.Resolver.LookupTXT(lookupCtx, "_shadownet."+canonicalOrg)
	if err != nil {
		a.mu.Lock()
		a.neg[canonicalOrg] = now.Add(a.cfg.NegativeCacheTTL)
		a.mu.Unlock()
		return nil, err
	}
	delegates := extractDelegates(txt)
	a.mu.Lock()
	a.pos[canonicalOrg] = cacheEntry{
		delegates: delegates,
		expiresAt: now.Add(a.cfg.PositiveCacheTTL),
	}
	a.mu.Unlock()
	return delegates, nil
}

// extractDelegates walks the TXT chunks for `delegate=` key/value pairs.
// Multiple delegates may appear; the value of each pair is one entry.
// Comments and unrecognized keys are skipped.
func extractDelegates(txt []string) []string {
	var out []string
	for _, line := range txt {
		// TXT chunks may be the concatenation of multiple v=, ep=, pk=,
		// delegate= tokens separated by `;` or whitespace.
		for _, token := range splitTokens(line) {
			if strings.HasPrefix(token, "delegate=") {
				out = append(out, strings.TrimPrefix(token, "delegate="))
			}
		}
	}
	return out
}

func splitTokens(s string) []string {
	// Tokens are `;`-separated per RFC 0001 §4.2 (matching the provider
	// TXT format). Whitespace around each is trimmed.
	parts := strings.Split(s, ";")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}
