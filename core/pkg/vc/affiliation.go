// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
)

// ShadownetAffiliationCredentialType is the second entry in the VC type array
// for an AffiliationCredential (the first is VerifiableCredential).
const ShadownetAffiliationCredentialType = "ShadownetAffiliationCredential"

// MaxAffiliationLifetime is the SHOULD-cap from RFC-0003 §AffiliationCredential
// §Lifetime: affiliation changes more frequently than personhood, so its
// credential lifetime is held to 30 days where personhood is held to 90.
const MaxAffiliationLifetime = 30 * 24 * time.Hour

// AffiliationCredential is the structured view of an AffiliationCredential
// (RFC-0003 §AffiliationCredential). It asserts that a Subject is affiliated
// with an organization; trust derives from the org's did:web domain control.
type AffiliationCredential struct {
	Issuer      string // did:web:... (org or org-controlled SCA)
	Subject     string // did:key:... (individual) or did:web:... (sub-org)
	JTI         string // urn:uuid:...
	IssuedAt    time.Time
	Expires     time.Time
	Affiliation string // did:web:... (the org being asserted)
	Role        string // optional, operator-defined (e.g. "member", "admin")
	Groups      []string
	Status      *Status // optional revocation pointer
}

// IssueAffiliationCredential signs an AffiliationCredential JWT using kp
// (the issuer's keypair). The Issuer field MUST be the same DID whose key
// signs the JWT, and MUST be either the affiliation org itself or a DID the
// affiliation org's DID document lists as a delegated issuer.
func IssueAffiliationCredential(kp crypto.KeyPair, c AffiliationCredential, opts IssueOptions) (string, error) {
	if c.Issuer == "" {
		return "", errors.New("vc: affiliation.Issuer required")
	}
	if !strings.HasPrefix(c.Issuer, "did:web:") {
		return "", errors.New("vc: affiliation.Issuer must be a did:web (RFC-0003)")
	}
	if c.Subject == "" {
		return "", errors.New("vc: affiliation.Subject required")
	}
	if !strings.HasPrefix(c.Subject, "did:key:") && !strings.HasPrefix(c.Subject, "did:web:") {
		return "", errors.New("vc: affiliation.Subject must be did:key (individual) or did:web (sub-org)")
	}
	if c.JTI == "" {
		return "", errors.New("vc: affiliation.JTI required")
	}
	if c.Affiliation == "" {
		return "", errors.New("vc: affiliation.Affiliation required")
	}
	if !strings.HasPrefix(c.Affiliation, "did:web:") {
		return "", errors.New("vc: affiliation.Affiliation must be a did:web (RFC-0003)")
	}
	if c.IssuedAt.IsZero() || c.Expires.IsZero() {
		return "", errors.New("vc: IssuedAt and Expires required")
	}
	if !c.Expires.After(c.IssuedAt) {
		return "", errors.New("vc: Expires must be after IssuedAt")
	}
	if c.Expires.Sub(c.IssuedAt) > MaxAffiliationLifetime {
		return "", fmt.Errorf("vc: affiliation lifetime %v exceeds RFC-0003 maximum %v", c.Expires.Sub(c.IssuedAt), MaxAffiliationLifetime)
	}
	if opts.IssuerKeyID == "" {
		return "", errors.New("vc: IssueOptions.IssuerKeyID required")
	}
	issDID, _ := did.SplitDIDURL(opts.IssuerKeyID)
	if issDID != c.Issuer {
		return "", fmt.Errorf("vc: kid DID %q must match affiliation.Issuer %q", issDID, c.Issuer)
	}

	claims := wireAffiliationCredential{
		Iss:     c.Issuer,
		Sub:     c.Subject,
		Iat:     c.IssuedAt.Unix(),
		Exp:     c.Expires.Unix(),
		Jti:     c.JTI,
		Version: Version,
		VC: wireAffiliationVCBody{
			Context: []string{ContextW3CCredentialsV2, ContextShadownetV1},
			Type:    []string{CredentialType, ShadownetAffiliationCredentialType},
			CredentialSubject: wireAffiliationSubject{
				ID:          c.Subject,
				Affiliation: c.Affiliation,
				Role:        c.Role,
				Groups:      c.Groups,
			},
		},
	}
	if c.Status != nil {
		claims.VC.CredentialStatus = &wireStatus{
			Type:                 StatusEntryTypeBitstring,
			StatusListIndex:      strconv.FormatUint(c.Status.StatusListIndex, 10),
			StatusListCredential: c.Status.StatusListCredential,
		}
	}
	return crypto.SignJWT(kp.Private, claims, crypto.SignerOptions{
		KeyID: opts.IssuerKeyID,
		Type:  TypVCJWT,
	})
}

// VerifyAffiliationCredential parses an AffiliationCredential JWT, verifies
// its signature against the issuer's DID document, runs the structural
// checks from RFC-0003, and confirms domain control: when iss != affiliation,
// the affiliation org's DID document MUST list iss in shadownet:delegatedIssuers.
//
// VerifyAffiliationCredential does NOT consult any trust store or status
// list — those are higher-level concerns handled by Verifier.
func VerifyAffiliationCredential(ctx context.Context, r did.Resolver, compact string, now time.Time) (*AffiliationCredential, error) {
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, err
	}
	if hdr.Typ != TypVCJWT {
		return nil, fmt.Errorf("vc: typ = %q, want %q", hdr.Typ, TypVCJWT)
	}
	if hdr.Kid == "" {
		return nil, errors.New("vc: affiliation JWS missing kid")
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, fmt.Errorf("vc: resolve affiliation issuer key: %w", err)
	}
	var w wireAffiliationCredential
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, err
	}
	out, err := validateAffiliationCredential(&w, hdr.Kid, now)
	if err != nil {
		return nil, err
	}
	if out.Issuer != out.Affiliation {
		doc, err := r.Resolve(ctx, out.Affiliation)
		if err != nil {
			return nil, fmt.Errorf("vc: resolve affiliation org %q: %w", out.Affiliation, err)
		}
		if !doc.IsDelegatedIssuer(out.Issuer) {
			return nil, fmt.Errorf("vc: issuer %q is not listed in %q shadownet:delegatedIssuers", out.Issuer, out.Affiliation)
		}
	}
	return out, nil
}

func validateAffiliationCredential(w *wireAffiliationCredential, kid string, now time.Time) (*AffiliationCredential, error) {
	if w.Version != Version {
		return nil, fmt.Errorf("vc: shadownet:v = %q, want %q", w.Version, Version)
	}
	if w.Iss == "" || w.Sub == "" || w.Jti == "" {
		return nil, errors.New("vc: affiliation missing iss/sub/jti")
	}
	if !strings.HasPrefix(w.Iss, "did:web:") {
		return nil, fmt.Errorf("vc: affiliation iss = %q, must be did:web", w.Iss)
	}
	issDID, _ := did.SplitDIDURL(kid)
	if issDID != w.Iss {
		return nil, fmt.Errorf("vc: kid DID %q does not match iss %q", issDID, w.Iss)
	}
	if w.Iat == 0 || w.Exp == 0 {
		return nil, errors.New("vc: affiliation missing iat or exp")
	}
	if w.Exp <= w.Iat {
		return nil, errors.New("vc: affiliation exp must be after iat")
	}
	if !now.IsZero() && now.Unix() >= w.Exp {
		return nil, fmt.Errorf("vc: affiliation expired at %d (now %d)", w.Exp, now.Unix())
	}
	cs := w.VC.CredentialSubject
	if cs.ID == "" || cs.Affiliation == "" {
		return nil, errors.New("vc: affiliation credentialSubject missing id or affiliation")
	}
	if cs.ID != w.Sub {
		return nil, fmt.Errorf("vc: credentialSubject.id %q does not match sub %q", cs.ID, w.Sub)
	}
	if !strings.HasPrefix(cs.ID, "did:key:") && !strings.HasPrefix(cs.ID, "did:web:") {
		return nil, fmt.Errorf("vc: affiliation subject %q must be did:key or did:web", cs.ID)
	}
	if !strings.HasPrefix(cs.Affiliation, "did:web:") {
		return nil, fmt.Errorf("vc: credentialSubject.affiliation %q must be did:web", cs.Affiliation)
	}
	if !containsString(w.VC.Context, ContextW3CCredentialsV2) {
		return nil, fmt.Errorf("vc: @context missing %q", ContextW3CCredentialsV2)
	}
	if !containsString(w.VC.Type, CredentialType) || !containsString(w.VC.Type, ShadownetAffiliationCredentialType) {
		return nil, fmt.Errorf("vc: type must include %q and %q", CredentialType, ShadownetAffiliationCredentialType)
	}
	out := &AffiliationCredential{
		Issuer:      w.Iss,
		Subject:     w.Sub,
		JTI:         w.Jti,
		IssuedAt:    time.Unix(w.Iat, 0).UTC(),
		Expires:     time.Unix(w.Exp, 0).UTC(),
		Affiliation: cs.Affiliation,
		Role:        cs.Role,
		Groups:      append([]string(nil), cs.Groups...),
	}
	if w.VC.CredentialStatus != nil {
		if w.VC.CredentialStatus.Type != StatusEntryTypeBitstring {
			return nil, fmt.Errorf("vc: credentialStatus.type = %q, want %q", w.VC.CredentialStatus.Type, StatusEntryTypeBitstring)
		}
		idx, err := strconv.ParseUint(w.VC.CredentialStatus.StatusListIndex, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("vc: parse statusListIndex: %w", err)
		}
		if w.VC.CredentialStatus.StatusListCredential == "" {
			return nil, errors.New("vc: credentialStatus.statusListCredential required")
		}
		out.Status = &Status{
			StatusListIndex:      idx,
			StatusListCredential: w.VC.CredentialStatus.StatusListCredential,
		}
	}
	return out, nil
}

// wireAffiliationCredential is the on-wire JSON shape of an
// AffiliationCredential VC-JWT payload.
type wireAffiliationCredential struct {
	Iss     string                `json:"iss"`
	Sub     string                `json:"sub"`
	Iat     int64                 `json:"iat"`
	Exp     int64                 `json:"exp"`
	Jti     string                `json:"jti"`
	Version string                `json:"shadownet:v"`
	VC      wireAffiliationVCBody `json:"vc"`
}

type wireAffiliationVCBody struct {
	Context           []string               `json:"@context"`
	Type              []string               `json:"type"`
	CredentialSubject wireAffiliationSubject `json:"credentialSubject"`
	CredentialStatus  *wireStatus            `json:"credentialStatus,omitempty"`
}

type wireAffiliationSubject struct {
	ID          string   `json:"id"`
	Affiliation string   `json:"affiliation"`
	Role        string   `json:"role,omitempty"`
	Groups      []string `json:"groups,omitempty"`
}
