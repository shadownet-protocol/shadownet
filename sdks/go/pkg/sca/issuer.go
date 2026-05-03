// SPDX-License-Identifier: MIT

package sca

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
)

// DefaultListID is the listID assigned by RevocationStores that don't shard.
const DefaultListID = "main"

// Issuer is the SCA orchestrator. It owns the signing key, the proof-method
// registry, and the persistence handles.
type Issuer struct {
	DID        string         // SCA DID (e.g. did:web:sca.sh4dow.org)
	KeyID      string         // DID URL with key fragment, e.g. <DID>#k1
	Key        crypto.KeyPair // signing key matching KeyID
	Resolver   did.Resolver
	Sessions   SessionStore
	Issuance   IssuanceStore
	Revocation RevocationStore
	Methods    map[string]ProofMethod // keyed by ProofMethod.Name()
	Policy     Policy
	Now        func() time.Time

	// ReadyCheck is invoked by /readyz. nil = always ready.
	// Implementations typically ping their backing store.
	ReadyCheck func(context.Context) error
}

// Validate confirms an Issuer has all dependencies wired.
func (i *Issuer) Validate() error {
	switch {
	case i.DID == "":
		return errors.New("sca: Issuer.DID required")
	case i.KeyID == "":
		return errors.New("sca: Issuer.KeyID required")
	case i.Resolver == nil:
		return errors.New("sca: Issuer.Resolver required")
	case i.Sessions == nil:
		return errors.New("sca: Issuer.Sessions required")
	case i.Issuance == nil:
		return errors.New("sca: Issuer.Issuance required")
	case i.Revocation == nil:
		return errors.New("sca: Issuer.Revocation required")
	case len(i.Methods) == 0:
		return errors.New("sca: Issuer.Methods must contain ≥1 ProofMethod")
	}
	if d, _ := did.SplitDIDURL(i.KeyID); d != i.DID {
		return fmt.Errorf("sca: Issuer.KeyID %q does not match Issuer.DID %q", i.KeyID, i.DID)
	}
	if i.Policy.Issuer != "" && i.Policy.Issuer != i.DID {
		return fmt.Errorf("sca: Policy.Issuer %q does not match Issuer.DID %q", i.Policy.Issuer, i.DID)
	}
	return nil
}

func (i *Issuer) now() time.Time {
	if i.Now != nil {
		return i.Now()
	}
	return time.Now().UTC()
}

// StartProof handles POST /proof/start.
func (i *Issuer) StartProof(ctx context.Context, auth *SubjectAuth, req ProofStartRequest) (*ProofStartResponse, error) {
	if req.Subject == "" {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "subject required")
	}
	if req.Subject != auth.Subject {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "subject does not match Authorization JWT")
	}
	level, ok := i.Policy.FindLevel(req.Level)
	if !ok {
		return nil, New(http.StatusBadRequest, CodeInvalidLevel, fmt.Sprintf("level %q not offered", req.Level))
	}
	method, ok := i.Methods[level.Method]
	if !ok {
		return nil, New(http.StatusInternalServerError, CodeInvalidLevel, fmt.Sprintf("policy advertises method %q but it is not registered", level.Method))
	}

	now := i.now()
	sess := Session{
		ID:          newID("ses"),
		Subject:     req.Subject,
		Level:       req.Level,
		Method:      method.Name(),
		State:       StatePending,
		CallbackURL: req.CallbackURL,
		CreatedAt:   now,
		ExpiresAt:   now.Add(PendingTTL),
	}
	next, readyAt, err := method.Start(ctx, sess)
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeInvalidLevel, "proof method failed").wrap(err)
	}
	sess.Next = next
	if err := i.Sessions.Put(ctx, sess); err != nil {
		return nil, New(http.StatusInternalServerError, CodeUnauthorized, "persist session").wrap(err)
	}
	if readyAt != nil {
		if err := i.Sessions.MarkReady(ctx, sess.ID, *readyAt); err != nil {
			return nil, New(http.StatusInternalServerError, CodeUnauthorized, "mark ready").wrap(err)
		}
	}
	return &ProofStartResponse{
		Version:   vc.Version,
		SessionID: sess.ID,
		ExpiresAt: sess.ExpiresAt.Unix(),
		Method:    method.Name(),
		Next:      next,
	}, nil
}

// StatusProof handles POST /proof/status.
func (i *Issuer) StatusProof(ctx context.Context, auth *SubjectAuth, req ProofStatusRequest) (*ProofStatusResponse, error) {
	sess, err := i.Sessions.Get(ctx, req.SessionID)
	if errors.Is(err, ErrSessionNotFound) {
		return nil, New(http.StatusNotFound, CodeSessionMismatch, "unknown session")
	}
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeSessionMismatch, "fetch session").wrap(err)
	}
	if sess.Subject != auth.Subject {
		return nil, New(http.StatusForbidden, CodeSessionMismatch, "session subject does not match Authorization JWT")
	}
	// Lazy expiry: if elapsed past TTL, mark expired before returning.
	now := i.now()
	if sess.State == StatePending && now.After(sess.ExpiresAt) {
		_ = i.Sessions.Fail(ctx, sess.ID)
		sess.State = StateExpired
	}
	return &ProofStatusResponse{
		Version:   vc.Version,
		SessionID: sess.ID,
		Status:    string(sess.State),
	}, nil
}

// IssueCredential handles POST /issuance.
func (i *Issuer) IssueCredential(ctx context.Context, auth *SubjectAuth, req IssuanceRequest) (*IssuanceResponse, error) {
	csr, err := VerifyCSR(ctx, i.Resolver, req.CSR, i.DID, i.now())
	if err != nil {
		return nil, err
	}
	if csr.Subject != auth.Subject {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR.iss does not match Authorization JWT")
	}
	sess, err := i.Sessions.Get(ctx, req.SessionID)
	if errors.Is(err, ErrSessionNotFound) {
		return nil, New(http.StatusNotFound, CodeSessionMismatch, "unknown session")
	}
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeSessionMismatch, "fetch session").wrap(err)
	}
	if sess.Subject != csr.Subject {
		return nil, New(http.StatusBadRequest, CodeSessionMismatch, "CSR subject does not match session subject")
	}
	if sess.Level != csr.Level {
		return nil, New(http.StatusBadRequest, CodeSessionMismatch, "CSR level does not match session level")
	}
	now := i.now()
	switch sess.State {
	case StateReady:
		// ok
	case StateConsumed:
		return nil, New(http.StatusGone, CodeSessionConsumed, "session already used")
	case StateExpired, StateFailed:
		return nil, New(http.StatusConflict, CodeSessionNotReady, fmt.Sprintf("session %s", sess.State))
	case StatePending:
		if now.After(sess.ExpiresAt) {
			_ = i.Sessions.Fail(ctx, sess.ID)
			return nil, New(http.StatusConflict, CodeSessionNotReady, "session expired")
		}
		return nil, New(http.StatusConflict, CodeSessionNotReady, "session not ready")
	default:
		return nil, New(http.StatusConflict, CodeSessionNotReady, fmt.Sprintf("unexpected session state %q", sess.State))
	}

	level, ok := i.Policy.FindLevel(csr.Level)
	if !ok {
		return nil, New(http.StatusBadRequest, CodeInvalidLevel, "level no longer offered")
	}

	listID, idx, err := i.Revocation.AssignIndex(ctx)
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeRevoked, "assign status index").wrap(err)
	}
	jti := newJTI()
	expires := now.Add(time.Duration(level.CredentialLifetimeDays) * 24 * time.Hour)
	if expires.Sub(now) > vc.MaxCredentialLifetime {
		expires = now.Add(vc.MaxCredentialLifetime)
	}

	cred := vc.Credential{
		Issuer:      i.DID,
		Subject:     csr.Subject,
		JTI:         jti,
		IssuedAt:    now,
		Expires:     expires,
		Level:       csr.Level,
		SubjectType: csr.SubjectType,
		Status: &vc.Status{
			StatusListIndex:      idx,
			StatusListCredential: i.Policy.StatusListBase + listID,
		},
	}
	jwt, err := vc.IssueCredential(i.Key, cred, vc.IssueOptions{IssuerKeyID: i.KeyID})
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeCSRInvalid, "issue credential").wrap(err)
	}
	if err := i.Sessions.Consume(ctx, sess.ID); err != nil {
		if errors.Is(err, ErrSessionState) {
			return nil, New(http.StatusGone, CodeSessionConsumed, "session already used")
		}
		return nil, New(http.StatusInternalServerError, CodeSessionConsumed, "consume session").wrap(err)
	}
	if err := i.Issuance.Put(ctx, IssuedCredential{
		JTI:             jti,
		Issuer:          i.DID,
		Subject:         csr.Subject,
		Level:           csr.Level,
		SubjectType:     csr.SubjectType,
		JWT:             jwt,
		StatusListID:    listID,
		StatusListIndex: idx,
		IssuedAt:        now,
		Expires:         expires,
	}); err != nil {
		return nil, New(http.StatusInternalServerError, CodeCSRInvalid, "persist credential").wrap(err)
	}

	return &IssuanceResponse{Version: vc.Version, Credential: jwt}, nil
}

// IssueFreshness handles POST /freshness.
func (i *Issuer) IssueFreshness(ctx context.Context, auth *SubjectAuth, req FreshnessRequest) (*FreshnessResponse, error) {
	c, err := i.Issuance.Get(ctx, req.CredentialJTI)
	if errors.Is(err, ErrJTINotFound) {
		return nil, New(http.StatusNotFound, CodeUnknownJTI, "unknown jti")
	}
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeUnknownJTI, "fetch credential").wrap(err)
	}
	if c.Subject != auth.Subject {
		return nil, New(http.StatusForbidden, CodeNotHolder, "subject-auth iss is not the credential holder")
	}
	list, err := i.Revocation.Snapshot(ctx, c.StatusListID)
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeRevoked, "fetch status list").wrap(err)
	}
	revoked, err := list.Get(c.StatusListIndex)
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeRevoked, "status list lookup").wrap(err)
	}
	if revoked {
		return nil, New(http.StatusForbidden, CodeRevoked, "credential is revoked")
	}
	now := i.now()
	exp := now.Add(time.Duration(i.Policy.FreshnessWindowSeconds) * time.Second)
	if i.Policy.FreshnessWindowSeconds == 0 {
		exp = now.Add(vc.MaxFreshnessLifetime)
	}
	jwt, err := vc.IssueFreshness(i.Key, i.DID, i.KeyID, c.JTI, now, exp)
	if err != nil {
		return nil, New(http.StatusInternalServerError, CodeRevoked, "issue freshness").wrap(err)
	}
	return &FreshnessResponse{Version: vc.Version, FreshnessProof: jwt}, nil
}

// StatusList handles GET /status/<list-id>. Returns the publication JWT plus
// a reasonable Cache-Control header.
func (i *Issuer) StatusList(ctx context.Context, listID string) (jwt string, maxAge time.Duration, err error) {
	list, err := i.Revocation.Snapshot(ctx, listID)
	if err != nil {
		return "", 0, New(http.StatusNotFound, CodeRevoked, "unknown status list").wrap(err)
	}
	encoded, err := list.Encode()
	if err != nil {
		return "", 0, New(http.StatusInternalServerError, CodeRevoked, "encode status list").wrap(err)
	}
	now := i.now()
	jwt, err = vc.IssueStatusListCredential(i.Key, vc.StatusListPublication{
		ID:            i.Policy.StatusListBase + listID,
		Issuer:        i.DID,
		StatusPurpose: vc.StatusPurposeRevocation,
		EncodedList:   encoded,
		IssuedAt:      now,
		Expires:       now.Add(5 * time.Minute),
	}, i.KeyID)
	if err != nil {
		return "", 0, New(http.StatusInternalServerError, CodeRevoked, "issue status list").wrap(err)
	}
	return jwt, 5 * time.Minute, nil
}

// Revoke marks the credential identified by jti as revoked. Used by operator
// tooling and (later) by the SCA's admin surface; not exposed as a public
// HTTP endpoint at v0.1.
func (i *Issuer) Revoke(ctx context.Context, jti string) error {
	c, err := i.Issuance.Get(ctx, jti)
	if errors.Is(err, ErrJTINotFound) {
		return New(http.StatusNotFound, CodeUnknownJTI, "unknown jti")
	}
	if err != nil {
		return err
	}
	return i.Revocation.Revoke(ctx, c.StatusListID, c.StatusListIndex)
}

// newID returns an opaque session id with the given prefix.
func newID(prefix string) string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic("sca: rand.Read failed: " + err.Error())
	}
	return prefix + "-" + hex.EncodeToString(b[:])
}

// newJTI returns a urn:uuid:-style identifier (16 random bytes, hex-encoded).
// Strict UUIDv4 layout is not required by RFC-0003 — opacity + uniqueness is.
func newJTI() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic("sca: rand.Read failed: " + err.Error())
	}
	return "urn:uuid:" + hex.EncodeToString(b[:])
}
