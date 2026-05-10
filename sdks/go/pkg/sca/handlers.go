// SPDX-License-Identifier: MIT

package sca

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/vc"
)

// Wire request/response types per RFC-0004.

// ProofStartRequest is the body of POST /proof/start.
type ProofStartRequest struct {
	Version     string `json:"shadownet:v"`
	Subject     string `json:"subject"`
	Level       string `json:"level"`
	CallbackURL string `json:"callbackUrl,omitempty"`
}

// ProofStartResponse is the body of POST /proof/start (200 OK).
type ProofStartResponse struct {
	Version   string   `json:"shadownet:v"`
	SessionID string   `json:"sessionId"`
	ExpiresAt int64    `json:"expiresAt"`
	Method    string   `json:"method"`
	Next      NextStep `json:"next"`
}

// ProofStatusRequest is the body of POST /proof/status.
type ProofStatusRequest struct {
	Version   string `json:"shadownet:v"`
	SessionID string `json:"sessionId"`
}

// ProofStatusResponse is the body of POST /proof/status (200 OK).
type ProofStatusResponse struct {
	Version   string `json:"shadownet:v"`
	SessionID string `json:"sessionId"`
	Status    string `json:"status"`
}

// IssuanceRequest is the body of POST /issuance.
type IssuanceRequest struct {
	Version   string `json:"shadownet:v"`
	CSR       string `json:"csr"`
	SessionID string `json:"sessionId"`
}

// IssuanceResponse is the body of POST /issuance (200 OK).
type IssuanceResponse struct {
	Version    string `json:"shadownet:v"`
	Credential string `json:"credential"`
}

// FreshnessRequest is the body of POST /freshness.
type FreshnessRequest struct {
	Version       string `json:"shadownet:v"`
	CredentialJTI string `json:"credentialJti"`
}

// FreshnessResponse is the body of POST /freshness (200 OK).
type FreshnessResponse struct {
	Version        string `json:"shadownet:v"`
	FreshnessProof string `json:"freshnessProof"`
}

// ErrorBody is the JSON body of an error response, matching RFC-0006 §Errors.
type ErrorBody struct {
	Error   string `json:"error"`
	Detail  string `json:"detail,omitempty"`
	Version string `json:"shadownet:v"`
}

// MaxRequestBytes caps any single SCA request body. Keeps malformed clients
// from chewing through memory.
const MaxRequestBytes = 64 * 1024

// Handler returns an http.Handler that serves all RFC-0004 endpoints. Mount
// it on the SCA server's listener; routes are method+path matched on the
// stdlib ServeMux.
func (i *Issuer) Handler() http.Handler {
	mux := http.NewServeMux()
	i.RegisterRoutes(mux)
	return mux
}

// RegisterRoutes attaches the SCA endpoints to mux.
func (i *Issuer) RegisterRoutes(mux *http.ServeMux) {
	mux.Handle("GET /.well-known/did.json", http.HandlerFunc(i.serveDIDDocument))
	mux.Handle("GET /.well-known/sca/policy.json", http.HandlerFunc(i.servePolicy))
	mux.Handle("POST /proof/start", i.authenticated(i.serveProofStart))
	mux.Handle("POST /proof/status", i.authenticated(i.serveProofStatus))
	mux.Handle("POST /issuance", i.authenticated(i.serveIssuance))
	mux.Handle("POST /freshness", i.authenticated(i.serveFreshness))
	mux.Handle("GET /status/{listID}", http.HandlerFunc(i.serveStatusList))

	// Operational probes: /healthz aliases /livez for tools that only know
	// one path. /readyz invokes ReadyCheck (e.g. a DB ping) and reports 503
	// when the dependency is unreachable.
	mux.Handle("GET /healthz", http.HandlerFunc(serveLive))
	mux.Handle("GET /livez", http.HandlerFunc(serveLive))
	mux.Handle("GET /readyz", http.HandlerFunc(i.serveReady))
}

func serveLive(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_, _ = w.Write([]byte(`{"status":"ok","shadownet:v":"0.1"}`))
}

func (i *Issuer) serveReady(w http.ResponseWriter, r *http.Request) {
	if i.ReadyCheck != nil {
		if err := i.ReadyCheck(r.Context()); err != nil {
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

// authHandler is an http handler that requires a valid subject-auth JWT.
type authHandler func(w http.ResponseWriter, r *http.Request, auth *SubjectAuth)

// authenticated wraps an SCA handler that requires a valid subject-auth JWT.
func (i *Issuer) authenticated(next authHandler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth, err := VerifySubjectAuth(r.Context(), i.Resolver, r.Header.Get("Authorization"), i.DID, i.now())
		if err != nil {
			writeError(w, err)
			return
		}
		next(w, r, auth)
	})
}

func (i *Issuer) servePolicy(w http.ResponseWriter, _ *http.Request) {
	policy := i.Policy
	if policy.Issuer == "" {
		policy.Issuer = i.DID
	}
	if policy.Version == "" {
		policy.Version = vc.Version
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "max-age=300")
	_ = json.NewEncoder(w).Encode(policy)
}

func (i *Issuer) serveDIDDocument(w http.ResponseWriter, _ *http.Request) {
	jwk, err := crypto.PublicJWK(i.Key.Public, i.KeyID)
	if err != nil {
		writeError(w, New(http.StatusInternalServerError, CodeUnauthorized, "build did document").wrap(err))
		return
	}
	doc := map[string]any{
		"id": i.DID,
		"verificationMethod": []map[string]any{{
			"id":           i.KeyID,
			"type":         "JsonWebKey2020",
			"controller":   i.DID,
			"publicKeyJwk": jwk,
		}},
		"authentication":  []string{i.KeyID},
		"assertionMethod": []string{i.KeyID},
	}
	w.Header().Set("Content-Type", "application/did+json")
	w.Header().Set("Cache-Control", "max-age=3600")
	_ = json.NewEncoder(w).Encode(doc)
}

func (i *Issuer) serveProofStart(w http.ResponseWriter, r *http.Request, auth *SubjectAuth) {
	var req ProofStartRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, err)
		return
	}
	resp, err := i.StartProof(r.Context(), auth, req)
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, resp)
}

func (i *Issuer) serveProofStatus(w http.ResponseWriter, r *http.Request, auth *SubjectAuth) {
	var req ProofStatusRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, err)
		return
	}
	resp, err := i.StatusProof(r.Context(), auth, req)
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, resp)
}

func (i *Issuer) serveIssuance(w http.ResponseWriter, r *http.Request, auth *SubjectAuth) {
	var req IssuanceRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, err)
		return
	}
	resp, err := i.IssueCredential(r.Context(), auth, req)
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, resp)
}

func (i *Issuer) serveFreshness(w http.ResponseWriter, r *http.Request, auth *SubjectAuth) {
	var req FreshnessRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, err)
		return
	}
	resp, err := i.IssueFreshness(r.Context(), auth, req)
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, resp)
}

func (i *Issuer) serveStatusList(w http.ResponseWriter, r *http.Request) {
	listID := r.PathValue("listID")
	if listID == "" {
		writeError(w, New(http.StatusBadRequest, CodeRevoked, "missing listID"))
		return
	}
	jwt, maxAge, err := i.StatusList(r.Context(), listID)
	if err != nil {
		writeError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/jwt")
	w.Header().Set("Cache-Control", fmt.Sprintf("max-age=%d", int(maxAge.Seconds())))
	_, _ = io.WriteString(w, jwt)
}

func decodeJSON(r *http.Request, dst any) error {
	body := http.MaxBytesReader(nil, r.Body, MaxRequestBytes)
	defer body.Close()
	dec := json.NewDecoder(body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return New(http.StatusBadRequest, CodeCSRInvalid, "decode request body").wrap(err)
	}
	if dec.More() {
		return New(http.StatusBadRequest, CodeCSRInvalid, "request body must be a single JSON object")
	}
	return nil
}

func writeJSON(w http.ResponseWriter, body any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_ = json.NewEncoder(w).Encode(body)
}

func writeError(w http.ResponseWriter, err error) {
	var e *Error
	if !errors.As(err, &e) {
		e = New(http.StatusInternalServerError, CodeUnauthorized, "internal error")
	}
	body := ErrorBody{Error: e.Code, Detail: e.Detail, Version: vc.Version}
	if body.Detail == "" && e.Cause != nil {
		body.Detail = strings.TrimPrefix(e.Cause.Error(), "sca: ")
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(e.HTTPStatus)
	_ = json.NewEncoder(w).Encode(body)
}

// ShutdownTimeout is the amount of time http servers built atop this package
// SHOULD allow for graceful shutdown. Re-exported for callers in cmd/.
const ShutdownTimeout = 15 * time.Second
