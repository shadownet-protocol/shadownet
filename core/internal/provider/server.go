// SPDX-License-Identifier: MIT

package provider

import (
	"context"
	"crypto/ed25519"
	"crypto/tls"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/httpx"
)

// RunConfig is the input to Run. Caller is responsible for opening the
// store, loading the signing key, and supplying a logger.
type RunConfig struct {
	// ListenAddr is the host:port the HTTP server binds on (e.g.
	// "0.0.0.0:8443" or "127.0.0.1:8444").
	ListenAddr string

	// ProviderDomain is the apex domain this Provider operates as. It is
	// not validated against the listen address — TLS termination may be
	// upstream.
	ProviderDomain string

	// Signer is the Provider's Ed25519 private key used to sign all
	// AgentCards. The corresponding public key MUST be published in the
	// provider DNS TXT (see TXTRecord).
	Signer ed25519.PrivateKey

	// Store is the Record store (sqlitestore by default; pgstore for
	// production).
	Store Store

	// CacheMaxAge is the max-age (seconds) for the Cache-Control header on
	// AgentCard responses. 0 → 3600 (RFC 0001 §5.5 RECOMMENDED).
	CacheMaxAge int

	// ShutdownTimeout bounds graceful shutdown. 0 → 15s.
	ShutdownTimeout time.Duration

	// Logger is the slog.Logger used for request and lifecycle log lines.
	Logger *slog.Logger

	// TLSConfig is the optional TLS configuration. When nil, the server
	// runs plaintext HTTP — useful for local development behind a reverse
	// proxy. Production deployments SHOULD supply a TLS config (built via
	// httpx.TLSConfig or equivalent) or terminate TLS upstream.
	TLSConfig *tls.Config
}

// Run boots the Provider HTTP server and blocks until ctx is canceled.
// Returns nil on graceful shutdown, an error on bind failure or unclean
// shutdown.
func Run(ctx context.Context, cfg RunConfig) error {
	if cfg.Logger == nil {
		return errors.New("provider: RunConfig.Logger required")
	}
	if cfg.Store == nil {
		return errors.New("provider: RunConfig.Store required")
	}
	if len(cfg.Signer) != ed25519.PrivateKeySize {
		return errors.New("provider: RunConfig.Signer is not an Ed25519 private key")
	}
	if cfg.ProviderDomain == "" {
		return errors.New("provider: RunConfig.ProviderDomain required")
	}
	if cfg.ListenAddr == "" {
		return errors.New("provider: RunConfig.ListenAddr required")
	}
	if cfg.ShutdownTimeout == 0 {
		cfg.ShutdownTimeout = 15 * time.Second
	}

	h := &Handler{
		Store:          cfg.Store,
		Signer:         cfg.Signer,
		ProviderDomain: cfg.ProviderDomain,
		CacheMaxAge:    cfg.CacheMaxAge,
		Logger:         cfg.Logger,
	}

	mux := h.Routes()
	chain := httpx.RequestID(httpx.AccessLog(cfg.Logger)(httpx.Recover(cfg.Logger)(mux)))

	srv := httpx.NewServer(chain, httpx.ServerOptions{
		Addr:      cfg.ListenAddr,
		Logger:    cfg.Logger,
		TLSConfig: cfg.TLSConfig,
	})
	httpx.WarnIfNotLoopback(cfg.Logger, cfg.ListenAddr, cfg.TLSConfig != nil)

	errCh := make(chan error, 1)
	go func() { errCh <- httpx.ListenAndServe(ctx, srv) }()

	select {
	case err := <-errCh:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			return fmt.Errorf("provider: serve: %w", err)
		}
		return nil
	case <-ctx.Done():
		shutCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
		defer cancel()
		if err := srv.Shutdown(shutCtx); err != nil {
			return fmt.Errorf("provider: shutdown: %w", err)
		}
		return nil
	}
}
