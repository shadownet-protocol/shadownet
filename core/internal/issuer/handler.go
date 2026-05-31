// SPDX-License-Identifier: MIT

package issuer

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/agentcard"
	"github.com/shadownet-protocol/shadownet/core/internal/credential"
	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/csr"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/status"
	"github.com/shadownet-protocol/shadownet/core/internal/wellknown"
)

// HandlerConfig is the input to NewHandler.
type HandlerConfig struct {
	Mode         Mode
	Store        Store
	Hook         Hook
	Authz        *Authorizer
	Signer       ed25519.PrivateKey
	Logger       *slog.Logger
	Now          func() time.Time
	MaxBodyBytes int64 // 0 → 64 KiB cap on CSR body size

	// IssuerIdentifier is what receivers see as the CSR's `aud` and the
	// credential's `iss`. In ModeDomain this is the issuer's domain
	// (e.g. "acme.example"). In ModeKeyed this is the multibase pubkey
	// (z6Mk…) of the issuer's signing key.
	IssuerIdentifier string

	// StatusCacheSeconds is the Cache-Control max-age on status responses.
	// 0 → 300 (RFC 0001 §6.4 RECOMMENDED).
	StatusCacheSeconds int

	// Keyed mode fields. Required when Mode == ModeKeyed.
	KeyedAgentCardSubject KeyedAgentCardConfig
}

// KeyedAgentCardConfig is the description the keyed-Hub Issuer self-serves
// at /.well-known/agent-card.json.
type KeyedAgentCardConfig struct {
	Name           string
	Description    string
	Version        string
	A2AURL         string
	IssueURL       string // becomes shadownet:issueEndpoint
	StatusListBase string // becomes shadownet:statusListBase
}

// Handler is the HTTP handler the Issuer server exposes.
type Handler struct {
	cfg              HandlerConfig
	logger           *slog.Logger
	now              func() time.Time
	maxBody          int64
	signerPubMB      string
	statusMaxAgeSecs int
}

// NewHandler constructs a Handler from cfg. Returns an error if required
// configuration is missing or inconsistent with the chosen Mode.
func NewHandler(cfg HandlerConfig) (*Handler, error) {
	if cfg.Store == nil {
		return nil, errors.New("issuer: HandlerConfig.Store required")
	}
	if cfg.Hook == nil {
		return nil, errors.New("issuer: HandlerConfig.Hook required")
	}
	if cfg.Authz == nil {
		return nil, errors.New("issuer: HandlerConfig.Authz required")
	}
	if len(cfg.Signer) != ed25519.PrivateKeySize {
		return nil, errors.New("issuer: HandlerConfig.Signer is not an Ed25519 private key")
	}
	if cfg.IssuerIdentifier == "" {
		return nil, errors.New("issuer: HandlerConfig.IssuerIdentifier required")
	}
	if cfg.Logger == nil {
		return nil, errors.New("issuer: HandlerConfig.Logger required")
	}
	if cfg.Mode == ModeKeyed {
		if cfg.KeyedAgentCardSubject.A2AURL == "" || cfg.KeyedAgentCardSubject.IssueURL == "" || cfg.KeyedAgentCardSubject.StatusListBase == "" {
			return nil, errors.New("issuer: keyed mode requires A2AURL + IssueURL + StatusListBase")
		}
		if identifiers.Classify(cfg.IssuerIdentifier) != identifiers.ClassPubKey {
			return nil, errors.New("issuer: keyed mode requires IssuerIdentifier to be a multibase pubkey")
		}
	} else {
		if identifiers.Classify(cfg.IssuerIdentifier) != identifiers.ClassDomain {
			return nil, errors.New("issuer: domain mode requires IssuerIdentifier to be a domain")
		}
	}
	now := cfg.Now
	if now == nil {
		now = time.Now
	}
	maxBody := cfg.MaxBodyBytes
	if maxBody == 0 {
		maxBody = 64 * 1024
	}
	statusMaxAge := cfg.StatusCacheSeconds
	if statusMaxAge == 0 {
		statusMaxAge = 300
	}
	signerPubMB, err := identifiers.EncodePubKey(cfg.Signer.Public().(ed25519.PublicKey))
	if err != nil {
		return nil, fmt.Errorf("issuer: encode signer pub: %w", err)
	}
	return &Handler{
		cfg:              cfg,
		logger:           cfg.Logger,
		now:              now,
		maxBody:          maxBody,
		signerPubMB:      signerPubMB,
		statusMaxAgeSecs: statusMaxAge,
	}, nil
}

// Routes returns a mux with the Issuer's endpoints registered. The routing
// shape depends on Mode.
func (h *Handler) Routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", h.healthz)
	mux.HandleFunc("GET /livez", h.livez)
	mux.HandleFunc("GET /readyz", h.readyz)

	switch h.cfg.Mode {
	case ModeDomain:
		mux.HandleFunc("POST "+wellknown.IssuePath, h.serveIssue)
		mux.HandleFunc("GET "+wellknown.StatusPathPrefix+"{epoch}", h.serveStatus)
	case ModeKeyed:
		mux.HandleFunc("GET "+wellknown.DirectAgentCardPath, h.serveAgentCard)
		issuePath := stripScheme(h.cfg.KeyedAgentCardSubject.IssueURL)
		statusBasePath := stripScheme(h.cfg.KeyedAgentCardSubject.StatusListBase)
		mux.HandleFunc("POST "+issuePath, h.serveIssue)
		mux.HandleFunc("GET "+statusBasePath+"/{epoch}", h.serveStatus)
	}
	return mux
}

// stripScheme returns the path component of a URL string, dropping the
// scheme + host + port. The result is suitable for ServeMux pattern
// registration.
func stripScheme(u string) string {
	// We don't want to drag net/url + parse error paths into the routing
	// happy path; this small helper extracts the path slice.
	if i := strings.Index(u, "://"); i >= 0 {
		u = u[i+3:]
	}
	if i := strings.IndexByte(u, '/'); i >= 0 {
		return u[i:]
	}
	return "/"
}

func (h *Handler) serveAgentCard(w http.ResponseWriter, _ *http.Request) {
	body, err := agentcard.Build(agentcard.Body{
		Name:            h.cfg.KeyedAgentCardSubject.Name,
		Description:     h.cfg.KeyedAgentCardSubject.Description,
		Version:         h.cfg.KeyedAgentCardSubject.Version,
		A2AURL:          h.cfg.KeyedAgentCardSubject.A2AURL,
		ShadowPublicKey: h.signerPubMB,
		IssueEndpoint:   h.cfg.KeyedAgentCardSubject.IssueURL,
		StatusListBase:  h.cfg.KeyedAgentCardSubject.StatusListBase,
	})
	if err != nil {
		h.logger.Error("issuer: build agentcard", "err", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	signed, err := agentcard.Sign(body, h.cfg.Signer, agentcard.ModeDirect, "", h.signerPubMB)
	if err != nil {
		h.logger.Error("issuer: sign agentcard", "err", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	bytes, err := json.Marshal(signed)
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", wellknown.MediaA2AJSON)
	w.Header().Set("Cache-Control", "max-age=3600")
	_, _ = w.Write(bytes)
}

func (h *Handler) serveIssue(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBody))
	if err != nil {
		h.writeProblem(w, http.StatusBadRequest, "parse_error", "could not read body")
		return
	}
	token := strings.TrimSpace(string(body))
	if token == "" {
		h.writeProblem(w, http.StatusBadRequest, "parse_error", "empty CSR")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	parsed, err := h.parseAndVerifyCSR(token)
	if err != nil {
		h.writeProblem(w, http.StatusBadRequest, "parse_error", err.Error())
		return
	}

	// Build the canonical req payload for idempotency. JSON object order
	// in the wire CSR is not stable; rebuild the canonical form here.
	idem, err := IdempotencyKey(parsed.Iss, parsed.Aud, map[string]any{
		"kind": parsed.Req.Kind,
		"org":  parsed.Req.Org,
	})
	if err != nil {
		h.writeProblem(w, http.StatusBadRequest, "parse_error", err.Error())
		return
	}

	// Idempotent re-POST: an existing credential under this key MUST be
	// returned verbatim (RFC 0001 §6.5).
	if existing, err := h.cfg.Store.GetByIdempotencyKey(ctx, idem); err == nil {
		h.writeCredential(w, existing.JWS)
		return
	} else if !errors.Is(err, ErrNotFound) {
		h.logger.Error("issuer: GetByIdempotencyKey", "err", err)
		h.writeProblem(w, http.StatusInternalServerError, "parse_error", "store error")
		return
	}

	// Audience check before authorization to avoid leaking authorization
	// outcomes through the issuer's identifier.
	if parsed.Aud != h.cfg.IssuerIdentifier {
		h.writeProblem(w, http.StatusForbidden, "policy", fmt.Sprintf("aud=%q does not match this issuer", parsed.Aud))
		return
	}

	// §6.6: this Issuer must be authorized to attest for parsed.Req.Org.
	// The candidate issuer identifier is OUR identifier (the iss we'd
	// put on the minted credential), not the CSR Subject's iss claim.
	if err := h.cfg.Authz.Authorize(ctx, h.cfg.IssuerIdentifier, parsed.Req.Org); err != nil {
		h.writeProblem(w, http.StatusForbidden, "policy", err.Error())
		return
	}

	subjectPub, err := h.subjectPubFromCSR(parsed)
	if err != nil {
		h.writeProblem(w, http.StatusBadRequest, "parse_error", err.Error())
		return
	}

	decision, err := h.cfg.Hook.Evaluate(ctx, CSRView{
		Iss:      parsed.Iss,
		Aud:      parsed.Aud,
		Kind:     parsed.Req.Kind,
		Org:      parsed.Req.Org,
		IssuedAt: time.Unix(parsed.Iat, 0),
		Expiry:   time.Unix(parsed.Exp, 0),
	}, subjectPub)
	if err != nil {
		h.logger.Error("issuer: hook", "err", err)
		h.writeProblem(w, http.StatusInternalServerError, "parse_error", "hook failed")
		return
	}

	switch decision.Outcome {
	case OutcomeApprove:
		h.handleApprove(ctx, w, parsed, idem)
	case OutcomePending:
		h.writeCeremonyPending(w, decision.NextURL)
	case OutcomeReject:
		h.writeCeremonyFailed(w, decision.Reason)
	default:
		h.writeProblem(w, http.StatusInternalServerError, "parse_error", "unknown hook outcome")
	}
}

func (h *Handler) handleApprove(ctx context.Context, w http.ResponseWriter, parsed csr.Payload, idem string) {
	now := h.now()
	credExp := now.Add(7 * 24 * time.Hour)
	epoch, idx, err := h.cfg.Store.AllocateIndex(ctx, credExp)
	if err != nil {
		h.logger.Error("issuer: alloc idx", "err", err)
		h.writeProblem(w, http.StatusInternalServerError, "parse_error", "alloc failed")
		return
	}
	issuerKP := crypto.KeyPair{
		Private: h.cfg.Signer,
		Public:  h.cfg.Signer.Public().(ed25519.PublicKey),
	}
	jws, err := credential.Mint(credential.Payload{
		Iss:  h.cfg.IssuerIdentifier,
		Sub:  parsed.Iss,
		Kind: credential.KindOrgAffiliation,
		Org:  parsed.Req.Org,
		Iat:  now.Unix(),
		Exp:  credExp.Unix(),
		Rev:  credential.Revocation{Epoch: strconv.FormatUint(epoch, 10), Idx: idx},
	}, issuerKP)
	if err != nil {
		h.logger.Error("issuer: mint credential", "err", err)
		h.writeProblem(w, http.StatusInternalServerError, "parse_error", "mint failed")
		return
	}
	if err := h.cfg.Store.PutCredential(ctx, Credential{
		IdempotencyKey: idem,
		JWS:            jws,
		Iss:            h.cfg.IssuerIdentifier,
		Sub:            parsed.Iss,
		Org:            parsed.Req.Org,
		Epoch:          epoch,
		Idx:            idx,
		IssuedAt:       now,
		ExpiresAt:      credExp,
	}); err != nil {
		// Concurrent re-POST raced ahead of us — re-read and serve theirs.
		if existing, derr := h.cfg.Store.GetByIdempotencyKey(ctx, idem); derr == nil {
			h.writeCredential(w, existing.JWS)
			return
		}
		h.logger.Error("issuer: store credential", "err", err)
		h.writeProblem(w, http.StatusInternalServerError, "parse_error", "store failed")
		return
	}
	h.writeCredential(w, jws)
}

func (h *Handler) parseAndVerifyCSR(token string) (csr.Payload, error) {
	subjectPub, err := h.subjectPubFromTokenUnverified(token)
	if err != nil {
		return csr.Payload{}, err
	}
	p, err := csr.Verify(token, csr.VerifyOptions{
		Now:              h.now,
		ExpectedAudience: h.cfg.IssuerIdentifier,
		ResolveSubjectKey: func(string) (ed25519.PublicKey, error) {
			return subjectPub, nil
		},
	})
	if err != nil {
		return csr.Payload{}, err
	}
	return p, nil
}

// subjectPubFromCSR pulls the public key out of the parsed CSR's iss
// claim when it's keyed; otherwise the caller MUST resolve it via the
// Subject's AgentCard (out of scope for v0.2 — Shadowname-mode Subjects
// are expected to also be discoverable through DNS).
func (h *Handler) subjectPubFromCSR(p csr.Payload) (ed25519.PublicKey, error) {
	if identifiers.Classify(p.Iss) == identifiers.ClassPubKey {
		return identifiers.DecodePubKey(p.Iss)
	}
	// For Shadowname-mode Subjects, the Subject's AgentCard fetch is the
	// canonical way to learn the key; that requires a Provider lookup
	// the Issuer doesn't directly own. We surface a clear error so
	// deployments know they need to plug in a key resolver.
	return nil, fmt.Errorf("issuer: Shadowname-mode CSR subject key resolution is out of scope for this build (iss=%q)", p.Iss)
}

// subjectPubFromTokenUnverified parses the CSR header's kid claim to
// recover the Subject's public key for the initial signature verification.
// For keyed Subjects kid is the pubkey directly; for Shadowname Subjects
// kid is the Shadowname (and we surface a not-implemented error to match
// subjectPubFromCSR).
func (h *Handler) subjectPubFromTokenUnverified(token string) (ed25519.PublicKey, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil, errors.New("issuer: malformed CSR JWS")
	}
	header, err := decodeSegment(parts[0])
	if err != nil {
		return nil, fmt.Errorf("issuer: CSR header: %w", err)
	}
	var hdr struct {
		Alg string `json:"alg"`
		Typ string `json:"typ"`
		Kid string `json:"kid"`
	}
	if err := json.Unmarshal(header, &hdr); err != nil {
		return nil, fmt.Errorf("issuer: CSR header json: %w", err)
	}
	if hdr.Kid == "" {
		return nil, errors.New("issuer: CSR header missing kid")
	}
	if identifiers.Classify(hdr.Kid) == identifiers.ClassPubKey {
		return identifiers.DecodePubKey(hdr.Kid)
	}
	return nil, fmt.Errorf("issuer: Shadowname-mode CSR subject key resolution is out of scope for this build (kid=%q)", hdr.Kid)
}

// decodeSegment is a small base64url decode helper for JWS segments
// (header / payload). We keep it local rather than dragging in a shared
// helper because the call sites are few and the wire form is fixed.
func decodeSegment(seg string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(seg)
}

func (h *Handler) serveStatus(w http.ResponseWriter, r *http.Request) {
	epochStr := r.PathValue("epoch")
	epoch, err := strconv.ParseUint(epochStr, 10, 64)
	if err != nil {
		http.Error(w, "bad epoch", http.StatusBadRequest)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	bits, _, err := h.cfg.Store.LoadStatusBits(ctx, epoch)
	switch {
	case errors.Is(err, ErrNotFound):
		http.Error(w, "unknown epoch", http.StatusNotFound)
		return
	case err != nil:
		h.logger.Error("issuer: load status bits", "epoch", epoch, "err", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	var list status.List
	if len(bits) > 0 {
		list = status.FromRaw(bits)
	} else {
		list = status.Empty(8)
	}
	encoded, err := list.Encode()
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", wellknown.MediaTextPlain)
	w.Header().Set("Cache-Control", "max-age="+strconv.Itoa(h.statusMaxAgeSecs))
	_, _ = io.WriteString(w, encoded)
}

func (h *Handler) writeCredential(w http.ResponseWriter, jws string) {
	w.Header().Set("Content-Type", wellknown.MediaJOSE)
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, jws)
}

func (h *Handler) writeProblem(w http.ResponseWriter, status int, code, detail string) {
	body := map[string]any{
		"type":   wellknown.ErrorURNPrefix + code,
		"title":  code,
		"status": status,
		"detail": detail,
	}
	bytes, _ := json.Marshal(body)
	w.Header().Set("Content-Type", wellknown.MediaProblemJSON)
	w.WriteHeader(status)
	_, _ = w.Write(bytes)
}

func (h *Handler) writeCeremonyPending(w http.ResponseWriter, next string) {
	body := map[string]any{"next": next}
	bytes, _ := json.Marshal(body)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusConflict)
	_, _ = w.Write(bytes)
}

func (h *Handler) writeCeremonyFailed(w http.ResponseWriter, reason string) {
	body := map[string]any{
		"type":   wellknown.ErrorURNPrefix + "policy",
		"title":  "ceremony_failed",
		"status": http.StatusForbidden,
		"detail": reason,
	}
	bytes, _ := json.Marshal(body)
	w.Header().Set("Content-Type", wellknown.MediaProblemJSON)
	w.WriteHeader(http.StatusForbidden)
	_, _ = w.Write(bytes)
}

func (h *Handler) healthz(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, "ok\n")
}

func (h *Handler) livez(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, "alive\n")
}

func (h *Handler) readyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := h.cfg.Store.Ping(ctx); err != nil {
		http.Error(w, "store not ready", http.StatusServiceUnavailable)
		return
	}
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, "ready\n")
}
