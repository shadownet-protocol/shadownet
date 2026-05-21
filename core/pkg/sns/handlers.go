// SPDX-License-Identifier: MIT

package sns

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

// MaxRequestBytes caps an inbound JSON body.
const MaxRequestBytes = 64 * 1024

// MaxSubjectAuthLifetime caps the (exp - iat) of the subject-auth JWT used
// for record updates. Mirrors RFC-0004 §Common (60s) for consistency.
const MaxSubjectAuthLifetime = 60 * time.Second

// Server is the SNS provider's request handler.
type Server struct {
	ProviderDID string
	ProviderKID string
	// Provider is the host portion that appears in shadownames this server
	// authoritatively serves (e.g. "sh4dow.org"). For did:web-based
	// SNS deployments this is typically the did:web body. Required.
	Provider    string
	Key         crypto.KeyPair
	Records     RecordStore
	DIDResolver did.Resolver
	DefaultTTL  int
	Now         func() time.Time

	// ReadyCheck is invoked by /readyz. nil = always ready.
	// Implementations typically ping their backing store.
	ReadyCheck func(context.Context) error

	// Logger receives internal error details that the response body
	// deliberately omits. nil → slog.Default(). Operators get the full
	// failure reason in their log aggregator while clients see a stable,
	// sanitized message.
	Logger *slog.Logger
}

// Validate confirms a Server has all dependencies wired.
func (s *Server) Validate() error {
	switch {
	case s.ProviderDID == "":
		return errors.New("sns: Server.ProviderDID required")
	case s.ProviderKID == "":
		return errors.New("sns: Server.ProviderKID required")
	case s.Provider == "":
		return errors.New("sns: Server.Provider required")
	case s.Records == nil:
		return errors.New("sns: Server.Records required")
	case s.DIDResolver == nil:
		return errors.New("sns: Server.DIDResolver required")
	}
	if d, _ := did.SplitDIDURL(s.ProviderKID); d != s.ProviderDID {
		return fmt.Errorf("sns: ProviderKID %q does not match ProviderDID %q", s.ProviderKID, s.ProviderDID)
	}
	if s.DefaultTTL == 0 {
		s.DefaultTTL = 3600
	}
	if s.DefaultTTL < MinTTL || s.DefaultTTL > MaxTTL {
		return fmt.Errorf("sns: DefaultTTL %d out of [%d,%d]", s.DefaultTTL, MinTTL, MaxTTL)
	}
	return nil
}

func (s *Server) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now().UTC()
}

func (s *Server) logger() *slog.Logger {
	if s.Logger != nil {
		return s.Logger
	}
	return slog.Default()
}

// logInternal records the full failure reason server-side. Callers then
// return a stable, sanitized message to clients via writeErr.
func (s *Server) logInternal(r *http.Request, level slog.Level, what string, err error) {
	s.logger().LogAttrs(
		r.Context(), level, "sns: "+what,
		slog.String("path", r.URL.Path),
		slog.String("err", err.Error()),
	)
}

// Handler returns an http.Handler with SNS routes registered.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	s.RegisterRoutes(mux)
	return mux
}

// RegisterRoutes attaches the SNS endpoints to mux.
func (s *Server) RegisterRoutes(mux *http.ServeMux) {
	mux.Handle("GET /.well-known/did.json", http.HandlerFunc(s.serveDIDDocument))
	mux.Handle("GET "+ResolvePath, http.HandlerFunc(s.serveResolve))
	mux.Handle("PUT /v1/records/{local}", http.HandlerFunc(s.serveUpdate))
	mux.Handle("DELETE /v1/records/{local}", http.HandlerFunc(s.serveDelete))

	mux.Handle("GET /healthz", http.HandlerFunc(serveLive))
	mux.Handle("GET /livez", http.HandlerFunc(serveLive))
	mux.Handle("GET /readyz", http.HandlerFunc(s.serveReady))
}

func serveLive(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_, _ = w.Write([]byte(`{"status":"ok","shadownet:v":"0.1"}`))
}

func (s *Server) serveReady(w http.ResponseWriter, r *http.Request) {
	if s.ReadyCheck != nil {
		if err := s.ReadyCheck(r.Context()); err != nil {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(http.StatusServiceUnavailable)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "not-ready", "detail": err.Error(), "shadownet:v": "0.1",
			})
			return
		}
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_, _ = w.Write([]byte(`{"status":"ready","shadownet:v":"0.1"}`))
}

func (s *Server) serveDIDDocument(w http.ResponseWriter, _ *http.Request) {
	jwk, err := crypto.PublicJWK(s.Key.Public, s.ProviderKID)
	if err != nil {
		http.Error(w, "internal", http.StatusInternalServerError)
		return
	}
	doc := map[string]any{
		"id": s.ProviderDID,
		"verificationMethod": []map[string]any{{
			"id":           s.ProviderKID,
			"type":         "JsonWebKey2020",
			"controller":   s.ProviderDID,
			"publicKeyJwk": jwk,
		}},
		"authentication":  []string{s.ProviderKID},
		"assertionMethod": []string{s.ProviderKID},
	}
	w.Header().Set("Content-Type", "application/did+json")
	w.Header().Set("Cache-Control", "max-age=3600")
	_ = json.NewEncoder(w).Encode(doc)
}

func (s *Server) serveResolve(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")
	if name == "" {
		writeErr(w, http.StatusBadRequest, "missing name")
		return
	}
	canon, err := ParseShadowname(name)
	if err != nil {
		s.logInternal(r, slog.LevelWarn, "parse name", err)
		writeErr(w, http.StatusBadRequest, "invalid name")
		return
	}
	if !strings.EqualFold(canon.Provider, s.Provider) {
		writeErr(w, http.StatusBadRequest, fmt.Sprintf("provider %q does not match this SNS server (%q)", canon.Provider, s.Provider))
		return
	}
	rec, err := s.Records.Get(r.Context(), canon.Local)
	switch {
	case errors.Is(err, ErrRecordNotFound):
		writeErr(w, http.StatusNotFound, "no such shadowname")
		return
	case errors.Is(err, ErrRecordTombstoned):
		writeErr(w, http.StatusGone, "shadowname tombstoned")
		return
	case err != nil:
		s.logInternal(r, slog.LevelError, "store lookup on resolve", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	if rec.Shadowname == "" {
		rec.Shadowname = canon.String()
	}
	now := s.now()
	jwt, err := IssueRecord(s.Key, s.ProviderDID, s.ProviderKID, rec, now)
	if err != nil {
		s.logInternal(r, slog.LevelError, "issue record", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/jwt")
	w.Header().Set("Cache-Control", fmt.Sprintf("max-age=%d", rec.TTL))
	_, _ = io.WriteString(w, jwt)
}

// UpdateRequest is the body of PUT /v1/records/{local}.
//
// RFC-0005 leaves the precise wire shape of the update endpoint to the
// implementation; this is the shape `cmd/sns-server` uses. It is signed
// implicitly by the Authorization Bearer JWT (which signs the request).
type UpdateRequest struct {
	Version           string         `json:"shadownet:v"`
	DID               string         `json:"did"`
	Endpoint          string         `json:"endpoint"`
	PublicKey         crypto.JWK     `json:"publicKey"`
	SubjectType       vc.SubjectType `json:"subjectType"`
	TTL               int            `json:"ttl,omitempty"`
	RotationStatement string         `json:"rotationStatement,omitempty"`
}

func (s *Server) serveUpdate(w http.ResponseWriter, r *http.Request) {
	local := strings.ToLower(r.PathValue("local"))
	if local == "" {
		writeErr(w, http.StatusBadRequest, "missing local")
		return
	}
	auth, err := s.verifySubjectAuth(r)
	if err != nil {
		s.logInternal(r, slog.LevelWarn, "subject auth on update", err)
		writeErr(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	defer r.Body.Close()
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxRequestBytes))
	if err != nil {
		s.logInternal(r, slog.LevelWarn, "read body on update", err)
		writeErr(w, http.StatusBadRequest, "invalid request body")
		return
	}
	var req UpdateRequest
	if err := json.Unmarshal(body, &req); err != nil {
		s.logInternal(r, slog.LevelWarn, "decode body on update", err)
		writeErr(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if req.DID != auth.subject {
		writeErr(w, http.StatusBadRequest, "body.did does not match Authorization JWT subject")
		return
	}
	if req.Endpoint == "" || req.PublicKey.X == "" {
		writeErr(w, http.StatusBadRequest, "did, endpoint, publicKey required")
		return
	}
	ttl := req.TTL
	if ttl == 0 {
		ttl = s.DefaultTTL
	}
	if ttl < MinTTL || ttl > MaxTTL {
		writeErr(w, http.StatusBadRequest, fmt.Sprintf("ttl %d out of [%d,%d]", ttl, MinTTL, MaxTTL))
		return
	}

	// If a record already exists, the update must come from the same DID OR
	// from a new DID that proves rotation from the existing one.
	prior, priorErr := s.Records.Get(r.Context(), local)
	switch {
	case errors.Is(priorErr, ErrRecordNotFound):
		// fresh registration; no rotation required
	case errors.Is(priorErr, ErrRecordTombstoned):
		writeErr(w, http.StatusGone, "shadowname tombstoned")
		return
	case priorErr != nil:
		s.logInternal(r, slog.LevelError, "store lookup on update", priorErr)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	default:
		if prior.DID != req.DID {
			if req.RotationStatement == "" {
				writeErr(w, http.StatusBadRequest, "key rotation requires rotationStatement")
				return
			}
			stmt, err := did.VerifyKeyRotation(r.Context(), s.DIDResolver, req.RotationStatement)
			if err != nil {
				s.logInternal(r, slog.LevelWarn, "rotation statement verification", err)
				writeErr(w, http.StatusBadRequest, "rotation statement invalid")
				return
			}
			if stmt.Issuer != prior.DID || stmt.Subject != req.DID {
				writeErr(w, http.StatusBadRequest, "rotation statement does not chain prior DID to new DID")
				return
			}
		}
	}

	canon := Shadowname{Local: local, Provider: s.Provider}
	record := Record{
		Shadowname:  canon.String(),
		DID:         req.DID,
		Endpoint:    req.Endpoint,
		PublicKey:   req.PublicKey,
		SubjectType: req.SubjectType,
		TTL:         ttl,
		IssuedAt:    s.now(),
	}
	if err := s.Records.Put(r.Context(), record); err != nil {
		s.logInternal(r, slog.LevelError, "store put on update", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]any{"shadownet:v": Version, "shadowname": canon.String()})
}

func (s *Server) serveDelete(w http.ResponseWriter, r *http.Request) {
	local := strings.ToLower(r.PathValue("local"))
	auth, err := s.verifySubjectAuth(r)
	if err != nil {
		s.logInternal(r, slog.LevelWarn, "subject auth on delete", err)
		writeErr(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	prior, perr := s.Records.Get(r.Context(), local)
	if errors.Is(perr, ErrRecordNotFound) {
		writeErr(w, http.StatusNotFound, "no such record")
		return
	}
	if perr != nil && !errors.Is(perr, ErrRecordTombstoned) {
		s.logInternal(r, slog.LevelError, "store lookup on delete", perr)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	if !errors.Is(perr, ErrRecordTombstoned) && prior.DID != auth.subject {
		writeErr(w, http.StatusForbidden, "Authorization DID is not the record owner")
		return
	}
	if err := s.Records.Delete(r.Context(), local); err != nil {
		s.logInternal(r, slog.LevelError, "store delete", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

type subjectAuthClaims struct {
	subject string
}

func (s *Server) verifySubjectAuth(r *http.Request) (*subjectAuthClaims, error) {
	const prefix = "Bearer "
	hv := r.Header.Get("Authorization")
	if !strings.HasPrefix(hv, prefix) {
		return nil, errors.New("missing or non-bearer Authorization")
	}
	compact := strings.TrimSpace(hv[len(prefix):])
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, fmt.Errorf("parse auth: %w", err)
	}
	if hdr.Kid == "" {
		return nil, errors.New("auth missing kid")
	}
	pub, err := did.LookupKey(r.Context(), s.DIDResolver, hdr.Kid)
	if err != nil {
		return nil, fmt.Errorf("resolve auth key: %w", err)
	}
	var w struct {
		Iss     string `json:"iss"`
		Aud     string `json:"aud"`
		Iat     int64  `json:"iat"`
		Exp     int64  `json:"exp"`
		Purpose string `json:"purpose"`
		Version string `json:"shadownet:v"`
	}
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, fmt.Errorf("verify auth: %w", err)
	}
	if w.Iss == "" || w.Aud == "" {
		return nil, errors.New("auth missing iss/aud")
	}
	if w.Aud != s.ProviderDID {
		return nil, fmt.Errorf("auth aud %q does not match SNS DID %q", w.Aud, s.ProviderDID)
	}
	if d, _ := did.SplitDIDURL(hdr.Kid); d != w.Iss {
		return nil, errors.New("auth kid DID does not match iss")
	}
	if w.Iat == 0 || w.Exp == 0 || w.Exp <= w.Iat {
		return nil, errors.New("auth iat/exp invalid")
	}
	if time.Duration(w.Exp-w.Iat)*time.Second > MaxSubjectAuthLifetime {
		return nil, errors.New("auth lifetime > 60s")
	}
	if !s.now().IsZero() && s.now().Unix() >= w.Exp {
		return nil, errors.New("auth expired")
	}
	if w.Purpose != "sns-update" {
		return nil, fmt.Errorf("auth purpose %q != sns-update", w.Purpose)
	}
	return &subjectAuthClaims{subject: w.Iss}, nil
}

// IssueSubjectAuth helps clients (CLI, agent SDK consumers) build the
// Authorization JWT for SNS update/delete requests.
func IssueSubjectAuth(kp crypto.KeyPair, subject, subjectKeyID, audience string, iat, exp time.Time) (string, error) {
	if subject == "" || subjectKeyID == "" || audience == "" {
		return "", errors.New("sns: subject, subjectKeyID, audience required")
	}
	if !exp.After(iat) {
		return "", errors.New("sns: exp must be after iat")
	}
	if exp.Sub(iat) > MaxSubjectAuthLifetime {
		return "", fmt.Errorf("sns: subject-auth lifetime %v exceeds %v", exp.Sub(iat), MaxSubjectAuthLifetime)
	}
	return crypto.SignJWT(kp.Private, struct {
		Iss     string `json:"iss"`
		Aud     string `json:"aud"`
		Iat     int64  `json:"iat"`
		Exp     int64  `json:"exp"`
		Purpose string `json:"purpose"`
		Version string `json:"shadownet:v"`
	}{
		Iss: subject, Aud: audience, Iat: iat.Unix(), Exp: exp.Unix(),
		Purpose: "sns-update", Version: Version,
	}, crypto.SignerOptions{KeyID: subjectKeyID, Type: "JWT"})
}

func writeErr(w http.ResponseWriter, status int, detail string) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": http.StatusText(status), "detail": detail, "shadownet:v": Version})
}
