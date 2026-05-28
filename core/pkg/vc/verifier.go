// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
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
// and TrustStore; InstitutionalStore, StatusFetcher, and FreshnessWindow are
// optional — when InstitutionalStore is nil, the reference default policy
// (accept any resolving did:web) is used.
type Verifier struct {
	Resolver           did.Resolver
	TrustStore         TrustStore
	InstitutionalStore InstitutionalStore
	StatusFetcher      StatusFetcher
	FreshnessWindow    time.Duration
	Now                func() time.Time
}

// VerifiedPresentation is the output of a successful VerifyPresentation call.
type VerifiedPresentation struct {
	Presentation           *Presentation
	SubjectCredentials     []*VerifiedSubjectCredential
	AffiliationCredentials []*VerifiedAffiliationCredential
}

// VerifiedSubjectCredential pairs a parsed SubjectCredential with the
// freshness proof that validated it (Freshness == nil when the credential is
// within 24 h of `iat` and no proof was required).
type VerifiedSubjectCredential struct {
	Credential *SubjectCredential
	Freshness  *Freshness
}

// VerifiedAffiliationCredential pairs a parsed AffiliationCredential with its
// freshness proof.
type VerifiedAffiliationCredential struct {
	Credential *AffiliationCredential
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

	subjects, affs, freshByJTI, err := v.splitVPContents(ctx, pres, now)
	if err != nil {
		return nil, err
	}

	out := &VerifiedPresentation{Presentation: pres}
	for _, c := range subjects {
		if c.Subject != pres.Holder {
			return nil, errCode(ReasonPresentationInvalid, "subject credential subject does not match VP holder", nil)
		}
		entry, ok := v.TrustStore.Lookup(c.Issuer)
		if !ok || !entry.LevelAccepted(c.Level) {
			continue // silently filter untrusted credentials
		}

		f, err := v.checkSubjectFreshness(c, freshByJTI, now)
		if err != nil {
			return nil, err
		}
		if err := v.checkSubjectRevocation(ctx, c); err != nil {
			return nil, err
		}
		out.SubjectCredentials = append(out.SubjectCredentials, &VerifiedSubjectCredential{Credential: c, Freshness: f})
	}
	for _, c := range affs {
		if c.Subject != pres.Holder {
			return nil, errCode(ReasonPresentationInvalid, "affiliation credential subject does not match VP holder", nil)
		}
		if !v.institutionalAccepts(c.Affiliation) {
			continue // silently filter institutional-deny / unaccepted orgs
		}
		f, err := v.checkAffiliationFreshness(c, freshByJTI, now)
		if err != nil {
			return nil, err
		}
		if err := v.checkAffiliationRevocation(ctx, c); err != nil {
			return nil, err
		}
		out.AffiliationCredentials = append(out.AffiliationCredentials, &VerifiedAffiliationCredential{Credential: c, Freshness: f})
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
	subjects := make([]*SubjectCredential, len(v.SubjectCredentials))
	for i, vc := range v.SubjectCredentials {
		subjects[i] = vc.Credential
	}
	affs := make([]*AffiliationCredential, len(v.AffiliationCredentials))
	for i, vc := range v.AffiliationCredentials {
		affs[i] = vc.Credential
	}
	if !p.Match(subjects, affs) {
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

// splitVPContents inspects each embedded JWT and verifies it as a
// SubjectCredential, an AffiliationCredential, or a freshness proof,
// dispatching by JWT typ and (for vc+jwt) the vc.type discriminator.
func (v *Verifier) splitVPContents(ctx context.Context, pres *Presentation, now time.Time) (
	[]*SubjectCredential, []*AffiliationCredential, map[string]*Freshness, error,
) {
	var subjects []*SubjectCredential
	var affs []*AffiliationCredential
	fresh := map[string]*Freshness{}
	for _, raw := range pres.Credentials {
		hdr, err := crypto.PeekHeader(raw)
		if err != nil {
			return nil, nil, nil, errCode(ReasonPresentationInvalid, "peek embedded JWS", err)
		}
		switch hdr.Typ {
		case TypVCJWT:
			isAffiliation, err := peekIsAffiliation(raw)
			if err != nil {
				return nil, nil, nil, errCode(ReasonPresentationInvalid, "peek vc.type", err)
			}
			if isAffiliation {
				ac, err := VerifyAffiliationCredential(ctx, v.Resolver, raw, now)
				if err != nil {
					return nil, nil, nil, errCode(ReasonPresentationInvalid, "verify embedded affiliation credential", err)
				}
				affs = append(affs, ac)
			} else {
				sc, err := VerifySubjectCredential(ctx, v.Resolver, raw, now)
				if err != nil {
					return nil, nil, nil, errCode(ReasonPresentationInvalid, "verify embedded subject credential", err)
				}
				subjects = append(subjects, sc)
			}
		case "JWT", "":
			f, err := VerifyFreshness(ctx, v.Resolver, raw, now)
			if err != nil {
				return nil, nil, nil, errCode(ReasonFreshnessStale, "verify freshness", err)
			}
			fresh[f.CredentialJTI] = f
		default:
			return nil, nil, nil, errCode(ReasonPresentationInvalid, fmt.Sprintf("unexpected typ %q in VP", hdr.Typ), nil)
		}
	}
	return subjects, affs, fresh, nil
}

// peekIsAffiliation decodes the JWT payload without verifying signature and
// returns true iff the vc.type array contains ShadownetAffiliationCredentialType.
// Used only as a dispatch hint; the chosen verifier still validates everything.
func peekIsAffiliation(compact string) (bool, error) {
	parts := strings.Split(compact, ".")
	if len(parts) < 2 {
		return false, fmt.Errorf("vc: JWS has %d parts, want 3", len(parts))
	}
	body, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return false, fmt.Errorf("vc: decode JWS payload: %w", err)
	}
	var peek struct {
		VC struct {
			Type []string `json:"type"`
		} `json:"vc"`
	}
	if err := json.Unmarshal(body, &peek); err != nil {
		return false, fmt.Errorf("vc: parse JWS payload: %w", err)
	}
	return containsString(peek.VC.Type, ShadownetAffiliationCredentialType), nil
}

func (v *Verifier) institutionalAccepts(org string) bool {
	if v.InstitutionalStore != nil {
		if p, ok := v.InstitutionalStore.Lookup(org); ok {
			return !p.DenyListed
		}
		return v.InstitutionalStore.Default().AcceptDomainControlled
	}
	return DefaultInstitutionalPolicy().AcceptDomainControlled
}

func (v *Verifier) checkSubjectFreshness(c *SubjectCredential, freshByJTI map[string]*Freshness, now time.Time) (*Freshness, error) {
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

func (v *Verifier) checkAffiliationFreshness(c *AffiliationCredential, freshByJTI map[string]*Freshness, now time.Time) (*Freshness, error) {
	if now.Sub(c.IssuedAt) <= v.freshnessWindow() {
		return nil, nil
	}
	f, ok := freshByJTI[c.JTI]
	if !ok {
		return nil, errCode(ReasonFreshnessStale, "affiliation outside initial window with no freshness proof", nil)
	}
	if f.Issuer != c.Issuer {
		return nil, errCode(ReasonFreshnessStale, "freshness issuer does not match affiliation issuer", nil)
	}
	if now.Sub(f.IssuedAt) > v.freshnessWindow() {
		return nil, errCode(ReasonFreshnessStale, "freshness proof outside window", nil)
	}
	return f, nil
}

func (v *Verifier) checkSubjectRevocation(ctx context.Context, c *SubjectCredential) error {
	return v.checkRevocationStatus(ctx, c.Status)
}

func (v *Verifier) checkAffiliationRevocation(ctx context.Context, c *AffiliationCredential) error {
	return v.checkRevocationStatus(ctx, c.Status)
}

func (v *Verifier) checkRevocationStatus(ctx context.Context, s *Status) error {
	if s == nil {
		return nil
	}
	if v.StatusFetcher == nil {
		return errCode(ReasonRevoked, "credentialStatus present but no StatusFetcher configured", nil)
	}
	list, err := v.StatusFetcher.Fetch(ctx, s.StatusListCredential)
	if err != nil {
		return errCode(ReasonRevoked, "fetch status list", err)
	}
	revoked, err := list.Get(s.StatusListIndex)
	if err != nil {
		return errCode(ReasonRevoked, "status list index", err)
	}
	if revoked {
		return errCode(ReasonRevoked, "", nil)
	}
	return nil
}
