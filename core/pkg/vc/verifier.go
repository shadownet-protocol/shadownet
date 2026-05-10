// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
)

// DefaultFreshnessWindow is RFC-0003's default of 24 h.
const DefaultFreshnessWindow = 24 * time.Hour

// Reason codes lifted from RFC-0006 §Errors / RFC-0004 §Trust evaluation.
const (
	ReasonPresentationInvalid = "presentation_invalid"
	ReasonLevelInsufficient   = "level_insufficient"
	ReasonRevoked             = "revoked"
	ReasonFreshnessStale      = "freshness_stale"
	ReasonUntrustedIssuer     = "untrusted_issuer"
)

// Error carries a stable RFC error code alongside a wrapped cause.
type Error struct {
	Code   string
	Detail string
	Cause  error
}

func (e *Error) Error() string {
	switch {
	case e.Cause != nil && e.Detail != "":
		return fmt.Sprintf("vc: %s: %s: %v", e.Code, e.Detail, e.Cause)
	case e.Cause != nil:
		return fmt.Sprintf("vc: %s: %v", e.Code, e.Cause)
	case e.Detail != "":
		return fmt.Sprintf("vc: %s: %s", e.Code, e.Detail)
	default:
		return "vc: " + e.Code
	}
}

func (e *Error) Unwrap() error { return e.Cause }

// errCode helper.
func errCode(code, detail string, cause error) error {
	return &Error{Code: code, Detail: detail, Cause: cause}
}

// Verifier composes the protocol-defined checks (signature, trust, freshness,
// revocation) into a single high-level operation. Callers MUST set Resolver
// and TrustStore; StatusFetcher and FreshnessWindow are optional.
type Verifier struct {
	Resolver        did.Resolver
	TrustStore      TrustStore
	StatusFetcher   StatusFetcher
	FreshnessWindow time.Duration
	Now             func() time.Time
}

// VerifiedPresentation is the output of a successful VerifyPresentation call.
type VerifiedPresentation struct {
	Presentation *Presentation
	Credentials  []*VerifiedCredential
}

// VerifiedCredential pairs a parsed credential with the freshness proof that
// validated it (Freshness == nil when the credential is within 24 h of `iat`
// and no proof was required).
type VerifiedCredential struct {
	Credential *Credential
	Freshness  *Freshness
}

// VerifyPresentation parses compact, validates the VP signature, then
// verifies every embedded credential against the trust store, freshness
// window, and revocation list.
//
// Untrusted credentials (issuer not in the trust store, or the level not
// accepted) are silently dropped. Hard failures (signature, expired, revoked,
// stale freshness) abort with an *Error carrying the RFC code.
func (v *Verifier) VerifyPresentation(ctx context.Context, compact, expectedAud, expectedNonce string) (*VerifiedPresentation, error) {
	if v.Resolver == nil {
		return nil, errors.New("vc: Verifier.Resolver required")
	}
	if v.TrustStore == nil {
		return nil, errors.New("vc: Verifier.TrustStore required")
	}
	now := v.now()

	pres, err := VerifyPresentation(ctx, v.Resolver, compact, expectedAud, expectedNonce, now)
	if err != nil {
		return nil, errCode(ReasonPresentationInvalid, "verify VP", err)
	}

	creds, freshByJTI, err := v.splitVPContents(ctx, pres, now)
	if err != nil {
		return nil, err
	}

	out := &VerifiedPresentation{Presentation: pres}
	for _, c := range creds {
		if c.Subject != pres.Holder {
			return nil, errCode(ReasonPresentationInvalid, "credential subject does not match VP holder", nil)
		}
		entry, ok := v.TrustStore.Lookup(c.Issuer)
		if !ok || !entry.LevelAccepted(c.Level) {
			continue // silently filter untrusted credentials
		}

		f, err := v.checkFreshness(c, freshByJTI, now)
		if err != nil {
			return nil, err
		}
		if err := v.checkRevocation(ctx, c); err != nil {
			return nil, err
		}
		out.Credentials = append(out.Credentials, &VerifiedCredential{Credential: c, Freshness: f})
	}
	return out, nil
}

// EvaluatePredicate runs an RFC-0004 predicate against the verified
// credentials. Returns *Error{Code: ReasonLevelInsufficient} when the
// predicate is unsatisfied.
func (v *VerifiedPresentation) EvaluatePredicate(p *Predicate) error {
	if p == nil {
		return nil
	}
	creds := make([]*Credential, len(v.Credentials))
	for i, vc := range v.Credentials {
		creds[i] = vc.Credential
	}
	if !p.Match(creds) {
		return errCode(ReasonLevelInsufficient, "", nil)
	}
	return nil
}

func (v *Verifier) now() time.Time {
	if v.Now != nil {
		return v.Now()
	}
	return time.Now().UTC()
}

func (v *Verifier) freshnessWindow() time.Duration {
	if v.FreshnessWindow > 0 {
		return v.FreshnessWindow
	}
	return DefaultFreshnessWindow
}

// splitVPContents inspects each embedded JWT and verifies it as either a VC
// or a freshness proof, keyed by the credential JTI it attests.
func (v *Verifier) splitVPContents(ctx context.Context, pres *Presentation, now time.Time) ([]*Credential, map[string]*Freshness, error) {
	var creds []*Credential
	fresh := map[string]*Freshness{}
	for _, raw := range pres.Credentials {
		hdr, err := crypto.PeekHeader(raw)
		if err != nil {
			return nil, nil, errCode(ReasonPresentationInvalid, "peek embedded JWS", err)
		}
		switch hdr.Typ {
		case TypVCJWT:
			c, err := VerifyCredential(ctx, v.Resolver, raw, now)
			if err != nil {
				return nil, nil, errCode(ReasonPresentationInvalid, "verify embedded credential", err)
			}
			creds = append(creds, c)
		case "JWT", "":
			// Default treatment of an embedded "JWT" inside a VP is freshness.
			f, err := VerifyFreshness(ctx, v.Resolver, raw, now)
			if err != nil {
				return nil, nil, errCode(ReasonFreshnessStale, "verify freshness", err)
			}
			fresh[f.CredentialJTI] = f
		default:
			return nil, nil, errCode(ReasonPresentationInvalid, fmt.Sprintf("unexpected typ %q in VP", hdr.Typ), nil)
		}
	}
	return creds, fresh, nil
}

func (v *Verifier) checkFreshness(c *Credential, freshByJTI map[string]*Freshness, now time.Time) (*Freshness, error) {
	if now.Sub(c.IssuedAt) <= v.freshnessWindow() {
		return nil, nil // within initial window — proof optional
	}
	f, ok := freshByJTI[c.JTI]
	if !ok {
		return nil, errCode(ReasonFreshnessStale, "credential outside initial window with no freshness proof", nil)
	}
	if f.Issuer != c.Issuer {
		return nil, errCode(ReasonFreshnessStale, "freshness issuer does not match credential issuer", nil)
	}
	if now.Sub(f.IssuedAt) > v.freshnessWindow() {
		return nil, errCode(ReasonFreshnessStale, "freshness proof outside window", nil)
	}
	return f, nil
}

func (v *Verifier) checkRevocation(ctx context.Context, c *Credential) error {
	if c.Status == nil {
		return nil
	}
	if v.StatusFetcher == nil {
		return errCode(ReasonRevoked, "credentialStatus present but no StatusFetcher configured", nil)
	}
	list, err := v.StatusFetcher.Fetch(ctx, c.Status.StatusListCredential)
	if err != nil {
		return errCode(ReasonRevoked, "fetch status list", err)
	}
	revoked, err := list.Get(c.Status.StatusListIndex)
	if err != nil {
		return errCode(ReasonRevoked, "status list index", err)
	}
	if revoked {
		return errCode(ReasonRevoked, "", nil)
	}
	return nil
}
