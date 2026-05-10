// SPDX-License-Identifier: MIT

package a2a

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
)

// MaxRequestBytes caps an inbound JSON-RPC body.
const MaxRequestBytes = 256 * 1024

// HandlerFunc is the application's hook into the server.
//
// It is invoked after handshake validation succeeds. The implementation
// typically persists the inbound message into its own inbox and returns a
// freshly-built Task. For long-running negotiations the returned task is
// often in the "submitted" or "working" state; the application updates the
// task asynchronously, and callers re-poll via task:get.
type HandlerFunc func(ctx context.Context, caller InboundCaller, msg Message) (Task, error)

// InboundCaller groups the validated handshake state passed to a HandlerFunc.
type InboundCaller struct {
	Session      *SessionToken
	Presentation *vc.VerifiedPresentation // nil when the caller is using a cached VP — Server populates this from cache when applicable
}

// CardOptions configures the agent card the Server publishes.
type CardOptions struct {
	Name string
	URL  string
}

// Server implements the A2A surface required by RFC-0006.
//
// One Server instance serves one Shadow's identity; multi-tenant Sidecars
// build N Servers (one per tenant) and route by URL path.
type Server struct {
	DID         string
	KeyID       string
	Key         crypto.KeyPair
	DIDResolver did.Resolver
	Verifier    *vc.Verifier
	Tasks       TaskStore
	Handler     HandlerFunc
	Card        CardOptions
	Now         func() time.Time

	mu       sync.Mutex
	vps      map[string]cachedVP // keyed by callerDID
	vpWrites uint64              // counter; every vpSweepInterval writes triggers a sweep
}

type cachedVP struct {
	pres      *vc.VerifiedPresentation
	nonce     string // last issued challenge nonce, if a challenge was sent
	expiresAt time.Time
}

// vpSweepInterval is the cache-write count after which we sweep expired
// entries. Picked so the per-write cost stays O(1) amortized while bounding
// live-set growth on a busy multi-tenant Sidecar that sees many distinct
// caller DIDs.
const vpSweepInterval = 100

// Validate confirms a Server has all dependencies wired.
func (s *Server) Validate() error {
	switch {
	case s.DID == "":
		return errors.New("a2a: Server.DID required")
	case s.KeyID == "":
		return errors.New("a2a: Server.KeyID required")
	case s.DIDResolver == nil:
		return errors.New("a2a: Server.DIDResolver required")
	case s.Verifier == nil:
		return errors.New("a2a: Server.Verifier required")
	case s.Tasks == nil:
		return errors.New("a2a: Server.Tasks required")
	case s.Handler == nil:
		return errors.New("a2a: Server.Handler required")
	case s.Card.Name == "" || s.Card.URL == "":
		return errors.New("a2a: Server.Card.Name and Card.URL required")
	}
	if d, _ := did.SplitDIDURL(s.KeyID); d != s.DID {
		return fmt.Errorf("a2a: KeyID %q does not match DID %q", s.KeyID, s.DID)
	}
	return nil
}

func (s *Server) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now().UTC()
}

// HTTPHandler returns an http.Handler with the A2A routes attached.
func (s *Server) HTTPHandler() http.Handler {
	mux := http.NewServeMux()
	s.RegisterRoutes(mux)
	return mux
}

// RegisterRoutes attaches the A2A endpoints to mux at the standard paths.
//
// Multi-tenant deployments may instead call RegisterRoutesAt with a prefix.
func (s *Server) RegisterRoutes(mux *http.ServeMux) {
	s.RegisterRoutesAt(mux, "")
}

// RegisterRoutesAt attaches the A2A endpoints to mux under the given prefix
// (e.g. "/u/alice"). The prefix must NOT include a trailing slash.
func (s *Server) RegisterRoutesAt(mux *http.ServeMux, prefix string) {
	mux.Handle("GET "+prefix+AgentCardPath, http.HandlerFunc(s.serveAgentCard))
	mux.Handle("POST "+prefix+"/a2a", http.HandlerFunc(s.serveJSONRPC))
}

func (s *Server) serveAgentCard(w http.ResponseWriter, _ *http.Request) {
	jwk, err := crypto.PublicJWK(s.Key.Public, s.KeyID)
	if err != nil {
		http.Error(w, "internal", http.StatusInternalServerError)
		return
	}
	card := AgentCard{
		Name:      s.Card.Name,
		URL:       s.Card.URL,
		DID:       s.DID,
		PublicKey: jwk,
		Version:   "0.1",
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "max-age=300")
	_ = json.NewEncoder(w).Encode(card)
}

// jsonrpcRequest mirrors a JSON-RPC 2.0 request envelope.
type jsonrpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

// jsonrpcResponse mirrors a JSON-RPC 2.0 response envelope.
type jsonrpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Result  any             `json:"result,omitempty"`
	Error   *jsonrpcError   `json:"error,omitempty"`
}

type jsonrpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

const (
	jsonrpcParseError     = -32700
	jsonrpcInvalidRequest = -32600
	jsonrpcMethodNotFound = -32601
)

func (s *Server) serveJSONRPC(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxRequestBytes))
	if err != nil {
		writeRPCError(w, nil, jsonrpcParseError, "read body")
		return
	}
	defer r.Body.Close()
	var req jsonrpcRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeRPCError(w, nil, jsonrpcParseError, "parse JSON-RPC: "+err.Error())
		return
	}
	if req.JSONRPC != "2.0" {
		writeRPCError(w, req.ID, jsonrpcInvalidRequest, "jsonrpc must be \"2.0\"")
		return
	}

	// Handshake: verify session token and (when present) VP, then dispatch.
	auth := r.Header.Get("Authorization")
	sess, err := VerifySessionToken(r.Context(), s.DIDResolver, auth, s.DID, s.now())
	if err != nil {
		writeShadownetError(w, &Error{Code: CodePresentationInvalid, Detail: err.Error(), Cause: err})
		return
	}

	caller, err := s.handshake(r, sess)
	if err != nil {
		var aerr *Error
		if errors.As(err, &aerr) {
			writeShadownetError(w, aerr)
			return
		}
		writeRPCError(w, req.ID, jsonrpcInvalidRequest, err.Error())
		return
	}

	switch req.Method {
	case MethodMessageSend:
		s.handleMessageSend(w, r, &req, caller)
	case MethodMessageStream:
		s.handleMessageStream(w, r, &req, caller)
	case MethodTaskGet:
		s.handleTaskGet(w, r, &req, caller)
	case MethodTaskCancel:
		s.handleTaskCancel(w, r, &req, caller)
	default:
		writeRPCError(w, req.ID, jsonrpcMethodNotFound, "method not found: "+req.Method)
	}
}

// handshake performs the VP validation step. If a fresh VP is present in the
// header, it is verified and cached. Otherwise a cached VP is reused if one
// is fresh; if not, an *Error{Code: presentation_required, Nonce: …} is
// returned and the caller MUST retry with a VP bound to that nonce.
func (s *Server) handshake(r *http.Request, sess *SessionToken) (InboundCaller, error) {
	header := r.Header.Get("X-Shadownet-Presentation")
	if header != "" {
		// Determine the nonce we must require: if we previously challenged,
		// the cached entry holds the nonce.
		var requiredNonce string
		s.mu.Lock()
		if c, ok := s.vps[sess.Issuer]; ok {
			requiredNonce = c.nonce
		}
		s.mu.Unlock()

		pres, verr := s.Verifier.VerifyPresentation(r.Context(), header, s.DID, requiredNonce)
		if verr != nil {
			var vcErr *vc.Error
			code := CodePresentationInvalid
			if errors.As(verr, &vcErr) {
				switch vcErr.Code {
				case vc.ReasonLevelInsufficient:
					code = CodeLevelInsufficient
				case vc.ReasonRevoked:
					code = CodeRevoked
				case vc.ReasonFreshnessStale:
					code = CodeFreshnessStale
				}
			}
			return InboundCaller{}, &Error{Code: code, Detail: verr.Error(), Cause: verr}
		}
		s.cacheVP(sess.Issuer, pres)
		return InboundCaller{Session: sess, Presentation: pres}, nil
	}

	// No VP supplied: see if we have a fresh one cached.
	if pres, ok := s.lookupCachedVP(sess.Issuer); ok {
		return InboundCaller{Session: sess, Presentation: pres}, nil
	}
	// Issue a fresh challenge nonce.
	nonce := newNonce()
	s.mu.Lock()
	if s.vps == nil {
		s.vps = make(map[string]cachedVP)
	}
	s.vps[sess.Issuer] = cachedVP{nonce: nonce, expiresAt: s.now().Add(2 * time.Minute)}
	s.maybeSweepLocked()
	s.mu.Unlock()
	return InboundCaller{}, &Error{Code: CodePresentationRequired, Nonce: nonce}
}

func (s *Server) cacheVP(callerDID string, p *vc.VerifiedPresentation) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.vps == nil {
		s.vps = make(map[string]cachedVP)
	}
	s.vps[callerDID] = cachedVP{pres: p, expiresAt: s.now().Add(s.Verifier.FreshnessWindow)}
	s.maybeSweepLocked()
}

func (s *Server) lookupCachedVP(callerDID string) (*vc.VerifiedPresentation, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.vps[callerDID]
	if !ok || c.pres == nil {
		return nil, false
	}
	if c.expiresAt.Before(s.now()) {
		delete(s.vps, callerDID)
		return nil, false
	}
	return c.pres, true
}

// maybeSweepLocked is called from cache-write paths under s.mu. Every
// vpSweepInterval writes it walks the map and removes expired entries.
// The amortized cost is O(1) per write; live-set growth is bounded by
// (active callers within freshness window) + at most vpSweepInterval-1.
func (s *Server) maybeSweepLocked() {
	s.vpWrites++
	if s.vpWrites%vpSweepInterval != 0 {
		return
	}
	s.sweepLocked(s.now())
}

func (s *Server) sweepLocked(now time.Time) {
	for did, c := range s.vps {
		if c.expiresAt.Before(now) {
			delete(s.vps, did)
		}
	}
}

// SweepVPCache evicts every cached VP and challenge-nonce entry whose
// expiry has passed. The cache is also swept automatically every
// vpSweepInterval writes; SweepVPCache is exposed for tests and for any
// operator who wants deterministic eviction at a checkpoint.
func (s *Server) SweepVPCache() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sweepLocked(s.now())
}

// messageSendParams is the params object of message:send / message:stream.
type messageSendParams struct {
	Message Message `json:"message"`
}

func (s *Server) handleMessageSend(w http.ResponseWriter, r *http.Request, req *jsonrpcRequest, caller InboundCaller) {
	var p messageSendParams
	if err := json.Unmarshal(req.Params, &p); err != nil {
		writeRPCError(w, req.ID, jsonrpcInvalidRequest, "decode params: "+err.Error())
		return
	}
	if p.Message.MessageID == "" {
		p.Message.MessageID = "msg-" + newID()
	}
	task, err := s.Handler(r.Context(), caller, p.Message)
	if err != nil {
		writeRPCAppError(w, req.ID, err)
		return
	}
	writeRPCResult(w, req.ID, task)
}

func (s *Server) handleTaskGet(w http.ResponseWriter, r *http.Request, req *jsonrpcRequest, _ InboundCaller) {
	var p struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.ID == "" {
		writeRPCError(w, req.ID, jsonrpcInvalidRequest, "params.id required")
		return
	}
	task, err := s.Tasks.Get(r.Context(), p.ID)
	if errors.Is(err, ErrTaskNotFound) {
		writeShadownetError(w, &Error{Code: CodeUnknownIntent, Detail: "unknown task id"})
		return
	}
	if err != nil {
		writeRPCError(w, req.ID, jsonrpcInvalidRequest, "store: "+err.Error())
		return
	}
	writeRPCResult(w, req.ID, task)
}

func (s *Server) handleTaskCancel(w http.ResponseWriter, r *http.Request, req *jsonrpcRequest, _ InboundCaller) {
	var p struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(req.Params, &p); err != nil || p.ID == "" {
		writeRPCError(w, req.ID, jsonrpcInvalidRequest, "params.id required")
		return
	}
	task, err := s.Tasks.Cancel(r.Context(), p.ID)
	if errors.Is(err, ErrTaskNotFound) {
		writeShadownetError(w, &Error{Code: CodeUnknownIntent, Detail: "unknown task id"})
		return
	}
	if err != nil {
		writeRPCError(w, req.ID, jsonrpcInvalidRequest, "store: "+err.Error())
		return
	}
	writeRPCResult(w, req.ID, task)
}

// writeRPCResult writes a JSON-RPC 2.0 success response.
func writeRPCResult(w http.ResponseWriter, id json.RawMessage, result any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_ = json.NewEncoder(w).Encode(jsonrpcResponse{JSONRPC: "2.0", ID: id, Result: result})
}

// writeRPCError writes a JSON-RPC 2.0 error response with the given code.
func writeRPCError(w http.ResponseWriter, id json.RawMessage, code int, message string) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_ = json.NewEncoder(w).Encode(jsonrpcResponse{
		JSONRPC: "2.0", ID: id, Error: &jsonrpcError{Code: code, Message: message},
	})
}

// writeRPCAppError handles an error returned by the application's HandlerFunc.
// If the error is an *Error with a known Shadownet code, the response is the
// canonical RFC-0006 envelope at the right HTTP status; otherwise it is a
// JSON-RPC error.
func writeRPCAppError(w http.ResponseWriter, id json.RawMessage, err error) {
	if e, ok := AsError(err); ok {
		writeShadownetError(w, e)
		return
	}
	writeRPCError(w, id, jsonrpcInvalidRequest, err.Error())
}

// writeShadownetError serializes the RFC-0006 §Errors envelope. The body is
// the bare JSON object documented by the RFC, NOT a JSON-RPC error — RFC-0006
// is explicit about the wire shape and conformance expects it as-is.
func writeShadownetError(w http.ResponseWriter, e *Error) {
	body := map[string]any{
		"error":       e.Code,
		"shadownet:v": "0.1",
	}
	if e.Detail != "" {
		body["detail"] = e.Detail
	}
	if e.Code == CodePresentationRequired && e.Nonce != "" {
		body["nonce"] = e.Nonce
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(e.HTTPStatus())
	_ = json.NewEncoder(w).Encode(body)
}

func newID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic("a2a: rand.Read: " + err.Error())
	}
	return hex.EncodeToString(b[:])
}

func newNonce() string {
	var b [32]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic("a2a: rand.Read: " + err.Error())
	}
	return hex.EncodeToString(b[:])
}
