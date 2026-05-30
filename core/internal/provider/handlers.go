// SPDX-License-Identifier: MIT

package provider

import (
	"context"
	"crypto/ed25519"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/wellknown"
)

// Handler is the HTTP handler the server exposes. It implements:
//
//   - GET <prefix>/identity/{local} → signed AgentCard
//   - GET /healthz                  → 200 if process is up
//   - GET /livez                    → 200 if process is up (cheap)
//   - GET /readyz                   → 200 if Store.Ping succeeds
//
// The /identity/ prefix is configurable so the same binary can host the
// canonical "<ep>/identity/<local>" shape or sit behind a path-stripping
// reverse proxy.
type Handler struct {
	Store          Store
	Signer         ed25519.PrivateKey
	ProviderDomain string
	CacheMaxAge    int // seconds, RECOMMENDED 3600 per RFC 0001 §5.5
	Logger         *slog.Logger
}

// Routes returns a mux with the Handler's endpoints registered.
func (h *Handler) Routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /identity/{local}", h.serveIdentity)
	mux.HandleFunc("GET /healthz", h.healthz)
	mux.HandleFunc("GET /livez", h.livez)
	mux.HandleFunc("GET /readyz", h.readyz)
	return mux
}

func (h *Handler) serveIdentity(w http.ResponseWriter, r *http.Request) {
	local := strings.ToLower(r.PathValue("local"))
	if local == "" {
		http.Error(w, "missing local", http.StatusBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	rec, err := h.Store.GetRecord(ctx, local)
	switch {
	case errors.Is(err, ErrNotFound):
		http.Error(w, "unknown shadowname", http.StatusNotFound)
		return
	case err != nil:
		h.Logger.Error("provider: store lookup failed", "local", local, "err", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	body, err := SignCard(rec, h.ProviderDomain, h.Signer)
	if err != nil {
		h.Logger.Error("provider: sign card failed", "local", local, "err", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	etag := ETag(body)
	if match := r.Header.Get("If-None-Match"); match != "" && match == etag {
		w.Header().Set("ETag", etag)
		w.WriteHeader(http.StatusNotModified)
		return
	}

	maxAge := h.CacheMaxAge
	if maxAge <= 0 {
		maxAge = 3600
	}
	w.Header().Set("Content-Type", wellknown.MediaA2AJSON)
	w.Header().Set("Cache-Control", cacheControlHeader(maxAge))
	w.Header().Set("ETag", etag)
	_, _ = w.Write(body)
}

func cacheControlHeader(maxAgeSeconds int) string {
	// max-age=<n> per RFC 7234.
	return "max-age=" + itoa(maxAgeSeconds)
}

// itoa is a tiny stdlib-free integer formatter — we don't want strconv in
// the hot path of the AgentCard handler. fmt.Sprintf is also fine; this
// just avoids a heap allocation for the small-value case.
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

func (h *Handler) healthz(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok\n"))
}

func (h *Handler) livez(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("alive\n"))
}

func (h *Handler) readyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := h.Store.Ping(ctx); err != nil {
		http.Error(w, "store not ready", http.StatusServiceUnavailable)
		return
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ready\n"))
}
