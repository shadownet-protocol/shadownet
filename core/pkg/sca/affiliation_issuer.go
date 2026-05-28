// SPDX-License-Identifier: MIT

package sca

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

// AffiliationIssuanceRequest is the body of POST /issuance/affiliation.
//
// Subject MUST be a did:key (employee/member) or did:web (sub-org).
// Affiliation MUST be a did:web — the org being asserted. When the SCA's
// Policy.AffiliationOrg is set, Affiliation MUST equal it; otherwise the
// SCA accepts any affiliation it has domain-control authority for (per the
// affiliation org's DID document shadownet:delegatedIssuers list).
type AffiliationIssuanceRequest struct {
	Version     string   `json:"shadownet:v"`
	Subject     string   `json:"subject"`
	Affiliation string   `json:"affiliation"`
	Role        string   `json:"role,omitempty"`
	Groups      []string `json:"groups,omitempty"`
}

// AffiliationIssuanceResponse is the body of POST /issuance/affiliation (200 OK).
// It reuses the issuance shape so clients can treat the surfaces uniformly.
type AffiliationIssuanceResponse struct {
	Version    string `json:"shadownet:v"`
	Credential string `json:"credential"`
}

// IssueAffiliationCredential issues an AffiliationCredential for the
// authenticated Subject. Operators that drive issuance directly from Go
// (HR integration, batch onboarding) can call this method without going
// through the HTTP surface.
func (i *Issuer) IssueAffiliationCredential(ctx context.Context, auth *SubjectAuth, req AffiliationIssuanceRequest) (*AffiliationIssuanceResponse, error) {
	if !i.Policy.IssuesAffiliation() {
		return nil, New(http.StatusForbidden, CodeModeNotEnabled, "SCA is not configured to issue AffiliationCredentials")
	}
	if req.Subject == "" {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "subject required")
	}
	if req.Subject != auth.Subject {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "subject does not match Authorization JWT")
	}
	if req.Affiliation == "" {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "affiliation required")
	}
	if !strings.HasPrefix(req.Affiliation, "did:web:") {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "affiliation must be a did:web")
	}
	if i.Policy.AffiliationOrg != "" && i.Policy.AffiliationOrg != req.Affiliation {
		return nil, New(http.StatusForbidden, CodeAffiliationOrg, "this SCA does not issue for the requested affiliation")
	}

	now := i.now()
	lifetimeDays := i.Policy.AffiliationLifetimeDays
	if lifetimeDays <= 0 {
		lifetimeDays = 30
	}
	expires := now.Add(time.Duration(lifetimeDays) * 24 * time.Hour)
	if expires.Sub(now) > vc.MaxAffiliationLifetime {
		expires = now.Add(vc.MaxAffiliationLifetime)
	}

	listID, idx, err := i.AffiliationRevocation.AssignIndex(ctx)
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeRevoked, "assign status index").wrap(err)
	}
	jti := newJTI()
	base := i.Policy.AffiliationStatusListBase
	if base == "" {
		base = i.Policy.StatusListBase
	}
	cred := vc.AffiliationCredential{
		Issuer:      i.DID,
		Subject:     req.Subject,
		JTI:         jti,
		IssuedAt:    now,
		Expires:     expires,
		Affiliation: req.Affiliation,
		Role:        req.Role,
		Groups:      append([]string(nil), req.Groups...),
		Status: &vc.Status{
			StatusListIndex:      idx,
			StatusListCredential: base + listID,
		},
	}
	jwt, err := vc.IssueAffiliationCredential(i.Key, cred, vc.IssueOptions{IssuerKeyID: i.KeyID})
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeCSRInvalid, "issue affiliation credential").wrap(err)
	}
	if err := i.Issuance.Put(ctx, IssuedCredential{
		JTI:             jti,
		Issuer:          i.DID,
		Subject:         req.Subject,
		Kind:            KindAffiliation,
		Affiliation:     req.Affiliation,
		Role:            req.Role,
		Groups:          append([]string(nil), req.Groups...),
		JWT:             jwt,
		StatusListID:    listID,
		StatusListIndex: idx,
		IssuedAt:        now,
		Expires:         expires,
	}); err != nil {
		return nil, New(http.StatusInternalServerError, CodeCSRInvalid, "persist credential").wrap(err)
	}
	return &AffiliationIssuanceResponse{Version: vc.Version, Credential: jwt}, nil
}
