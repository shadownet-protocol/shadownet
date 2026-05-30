// SPDX-License-Identifier: MIT

// Package credential mints and parses shadownet-cred+jwt — the
// org_affiliation credential JWS the Issuer hands out in response to a
// valid CSR + ceremony approval.
//
// The credential's identifiers (iss, sub, org) are a union of three forms
// per RFC 0001 §6.1: a domain (Shadowname-mode issuer / org / subject's
// provider), a Shadowname (the attested subject), or a multibase Ed25519
// public key (direct-mode issuer, keyed Hub, or direct-mode subject).
//
// Spec: RFC 0001 §6.
//
// Schema mirror: shadownet-specs/schemas/credentials/credential.schema.json.
package credential

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/wellknown"
)

// Kind is the credential discriminator. v0.2 defines exactly one
// (RFC 0001 §6.2); future kinds slot in here.
type Kind string

const (
	// KindOrgAffiliation asserts the subject is a member of (or otherwise
	// acts for) the named organization.
	KindOrgAffiliation Kind = "org_affiliation"

	// MaxOrgAffiliationLifetime is the §6.3 cap on exp - iat for
	// org_affiliation credentials (30 days).
	MaxOrgAffiliationLifetime = 30 * 24 * time.Hour

	// DefaultLeeway is the ±60 s clock-skew tolerance per RFC 0001 §2.
	DefaultLeeway = 60 * time.Second
)

// Errors surfaced by mint / verify. Callers MAY wrap them with their own
// context (e.g. wellknown.ErrorURNPrefix + "creds_rejected" for HTTP) but
// the spec-level reason MUST stay attached.
var (
	ErrInvalid          = errors.New("credential: invalid")
	ErrUnknownKind      = errors.New("credential: unknown kind")
	ErrLifetimeExceeded = errors.New("credential: lifetime exceeds 30 days")
	ErrSignature        = errors.New("credential: signature did not verify")
	ErrExpired          = errors.New("credential: expired")
	ErrIATInFuture      = errors.New("credential: iat in the future")
	ErrIssuerUnauthd    = errors.New("credential: issuer not authorized for org")
)

// Revocation is the per-credential pointer into the issuer's status list
// (RFC 0001 §6.4). Both fields are required.
type Revocation struct {
	Epoch string `json:"epoch"`
	Idx   uint64 `json:"idx"`
}

// Payload is the decoded claims of a shadownet-cred+jwt.
type Payload struct {
	Iss  string     `json:"iss"`
	Sub  string     `json:"sub"`
	Kind Kind       `json:"kind"`
	Org  string     `json:"org"`
	Iat  int64      `json:"iat"`
	Exp  int64      `json:"exp"`
	Rev  Revocation `json:"rev"`
}

// Mint signs `p` with the issuer's key, producing a JWS-compact
// shadownet-cred+jwt. Per §6.3 only org_affiliation credentials are
// allowed and exp - iat MUST NOT exceed 30 days.
func Mint(p Payload, issuer crypto.KeyPair) (string, error) {
	if p.Kind != KindOrgAffiliation {
		return "", fmt.Errorf("%w: %q", ErrUnknownKind, p.Kind)
	}
	if p.Exp <= p.Iat {
		return "", fmt.Errorf("%w: exp must be greater than iat", ErrInvalid)
	}
	if time.Duration(p.Exp-p.Iat)*time.Second > MaxOrgAffiliationLifetime {
		return "", fmt.Errorf("%w: %ds", ErrLifetimeExceeded, p.Exp-p.Iat)
	}
	if err := validateIdentifier(p.Iss); err != nil {
		return "", fmt.Errorf("%w: iss: %v", ErrInvalid, err)
	}
	if err := validateIdentifier(p.Sub); err != nil {
		return "", fmt.Errorf("%w: sub: %v", ErrInvalid, err)
	}
	if err := validateIdentifier(p.Org); err != nil {
		return "", fmt.Errorf("%w: org: %v", ErrInvalid, err)
	}
	if p.Rev.Epoch == "" {
		return "", fmt.Errorf("%w: rev.epoch required", ErrInvalid)
	}
	pubMB, err := identifiers.EncodePubKey(issuer.Public)
	if err != nil {
		return "", fmt.Errorf("credential: encode issuer pubkey: %w", err)
	}
	return crypto.SignJWT(issuer.Private, p, crypto.SignerOptions{
		Type:  wellknown.TypShadownetCredJWT,
		KeyID: kidFromIssuer(p.Iss, pubMB),
	})
}

// kidFromIssuer chooses a kid header value based on the issuer
// identifier's form. Domain-mode issuers use the AgentCard-style
// "shadownet@<domain>" kid; keyed issuers use the multibase pubkey form
// directly (matching the §5.3 direct-mode kid convention).
func kidFromIssuer(iss, issuerPubMultibase string) string {
	switch identifiers.Classify(iss) {
	case identifiers.ClassPubKey:
		return iss
	case identifiers.ClassDomain:
		return "shadownet@" + iss
	default:
		// Shadowname-issued credentials are not defined by §6, but fall
		// back to the pubkey form for safety.
		return issuerPubMultibase
	}
}

// VerifyOptions threads the resolver callbacks the caller controls (DNS
// lookup of the issuer's domain provider record, status-list fetch, etc.).
type VerifyOptions struct {
	// Now overrides time.Now for deterministic testing.
	Now func() time.Time

	// Leeway tolerates iat/exp clock skew. Defaults to DefaultLeeway when
	// zero.
	Leeway time.Duration

	// ResolveIssuerKey returns the verification key for the given iss
	// identifier. Caller-supplied so the credential package stays free of
	// DNS / HTTP plumbing. For keyed issuers, the identifier IS the key;
	// callers MAY just return identifiers.DecodePubKey(iss) directly.
	ResolveIssuerKey func(iss string) (ed25519.PublicKey, error)

	// AuthorizeIssuerForOrg implements §6.6. The default rules: iss == org;
	// iss is a sub-domain of org (only meaningful when both are domains);
	// iss appears under delegate= in `_shadownet.<org>`'s TXT record (only
	// meaningful when org is a domain). The keyed-issuer carve-out
	// (iss == org only) is enforced by the implementation supplied here.
	AuthorizeIssuerForOrg func(iss, org string) error
}

// Verify parses, validates, and authenticates a shadownet-cred+jwt against
// the verification key returned by opts.ResolveIssuerKey. It implements
// §6 validation steps 1-6 (header typ + alg, payload shape, lifetime,
// signature, issuer-for-org authorization). Revocation (step 7) and
// trust-store evaluation (step 8) are left to the caller; see RFC 0001 §6,
// §7.
func Verify(token string, opts VerifyOptions) (Payload, error) {
	hdr, err := crypto.PeekHeader(token)
	if err != nil {
		return Payload{}, fmt.Errorf("%w: header: %v", ErrInvalid, err)
	}
	if hdr.Typ != wellknown.TypShadownetCredJWT {
		return Payload{}, fmt.Errorf("%w: typ %q, want %q", ErrInvalid, hdr.Typ, wellknown.TypShadownetCredJWT)
	}
	if hdr.Alg != "EdDSA" {
		return Payload{}, fmt.Errorf("%w: alg %q, want EdDSA", ErrInvalid, hdr.Alg)
	}

	// Decode claims unverified first so we can resolve the issuer key.
	var raw struct {
		Iss  string          `json:"iss"`
		Sub  string          `json:"sub"`
		Kind string          `json:"kind"`
		Org  string          `json:"org"`
		Iat  int64           `json:"iat"`
		Exp  int64           `json:"exp"`
		Rev  json.RawMessage `json:"rev"`
	}
	parts, payload, err := peekClaims(token)
	if err != nil {
		return Payload{}, err
	}
	if err := json.Unmarshal(payload, &raw); err != nil {
		return Payload{}, fmt.Errorf("%w: payload: %v", ErrInvalid, err)
	}
	if raw.Kind != string(KindOrgAffiliation) {
		return Payload{}, fmt.Errorf("%w: %q", ErrUnknownKind, raw.Kind)
	}
	var rev Revocation
	if len(raw.Rev) == 0 {
		return Payload{}, fmt.Errorf("%w: rev required", ErrInvalid)
	}
	if err := json.Unmarshal(raw.Rev, &rev); err != nil {
		return Payload{}, fmt.Errorf("%w: rev: %v", ErrInvalid, err)
	}
	if rev.Epoch == "" {
		return Payload{}, fmt.Errorf("%w: rev.epoch required", ErrInvalid)
	}
	if raw.Exp <= raw.Iat {
		return Payload{}, fmt.Errorf("%w: exp must be greater than iat", ErrInvalid)
	}
	if time.Duration(raw.Exp-raw.Iat)*time.Second > MaxOrgAffiliationLifetime {
		return Payload{}, ErrLifetimeExceeded
	}
	if err := validateIdentifier(raw.Iss); err != nil {
		return Payload{}, fmt.Errorf("%w: iss: %v", ErrInvalid, err)
	}
	if err := validateIdentifier(raw.Sub); err != nil {
		return Payload{}, fmt.Errorf("%w: sub: %v", ErrInvalid, err)
	}
	if err := validateIdentifier(raw.Org); err != nil {
		return Payload{}, fmt.Errorf("%w: org: %v", ErrInvalid, err)
	}

	now := time.Now
	if opts.Now != nil {
		now = opts.Now
	}
	leeway := opts.Leeway
	if leeway == 0 {
		leeway = DefaultLeeway
	}
	nowT := now()
	if nowT.Unix() > raw.Exp+int64(leeway/time.Second) {
		return Payload{}, ErrExpired
	}
	if nowT.Unix()+int64(leeway/time.Second) < raw.Iat {
		return Payload{}, ErrIATInFuture
	}

	if opts.ResolveIssuerKey == nil {
		return Payload{}, fmt.Errorf("%w: VerifyOptions.ResolveIssuerKey is nil", ErrInvalid)
	}
	pub, err := opts.ResolveIssuerKey(raw.Iss)
	if err != nil {
		return Payload{}, fmt.Errorf("credential: resolve issuer key for %q: %w", raw.Iss, err)
	}
	if _, _, err := crypto.VerifyJWS(pub, parts); err != nil {
		return Payload{}, fmt.Errorf("%w: %v", ErrSignature, err)
	}

	if opts.AuthorizeIssuerForOrg != nil {
		if err := opts.AuthorizeIssuerForOrg(raw.Iss, raw.Org); err != nil {
			return Payload{}, fmt.Errorf("%w: %v", ErrIssuerUnauthd, err)
		}
	}

	return Payload{
		Iss:  raw.Iss,
		Sub:  raw.Sub,
		Kind: Kind(raw.Kind),
		Org:  raw.Org,
		Iat:  raw.Iat,
		Exp:  raw.Exp,
		Rev:  rev,
	}, nil
}

// peekClaims returns the token (unchanged) and its base64url-decoded
// payload segment without verifying the signature.
func peekClaims(token string) (string, []byte, error) {
	segs := strings.Split(token, ".")
	if len(segs) != 3 {
		return "", nil, fmt.Errorf("%w: malformed compact serialization", ErrInvalid)
	}
	payload, err := base64.RawURLEncoding.DecodeString(segs[1])
	if err != nil {
		return "", nil, fmt.Errorf("%w: payload base64: %v", ErrInvalid, err)
	}
	return token, payload, nil
}

// validateIdentifier accepts any of the three union forms.
func validateIdentifier(s string) error {
	if identifiers.Classify(s) == identifiers.ClassUnknown {
		return fmt.Errorf("%w: %q is not a domain, shadowname, or multibase pubkey", ErrInvalid, s)
	}
	return nil
}
