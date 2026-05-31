// SPDX-License-Identifier: MIT

package issuer

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/httpx"
)

// RunConfig wraps HandlerConfig with the server-side knobs (listen
// address, TLS config, shutdown timeout).
type RunConfig struct {
	HandlerConfig HandlerConfig

	ListenAddr      string
	ShutdownTimeout time.Duration
	TLSConfig       *tls.Config
}

// Run boots the Issuer HTTP server. Blocks until ctx is canceled or the
// listener returns. Returns nil on graceful shutdown.
func Run(ctx context.Context, cfg RunConfig) error {
	if cfg.ListenAddr == "" {
		return errors.New("issuer: RunConfig.ListenAddr required")
	}
	if cfg.HandlerConfig.Logger == nil {
		return errors.New("issuer: HandlerConfig.Logger required")
	}
	if cfg.ShutdownTimeout == 0 {
		cfg.ShutdownTimeout = 15 * time.Second
	}

	h, err := NewHandler(cfg.HandlerConfig)
	if err != nil {
		return err
	}
	chain := httpx.RequestID(httpx.AccessLog(cfg.HandlerConfig.Logger)(httpx.Recover(cfg.HandlerConfig.Logger)(h.Routes())))
	srv := httpx.NewServer(chain, httpx.ServerOptions{
		Addr:      cfg.ListenAddr,
		Logger:    cfg.HandlerConfig.Logger,
		TLSConfig: cfg.TLSConfig,
	})
	httpx.WarnIfNotLoopback(cfg.HandlerConfig.Logger, cfg.ListenAddr, cfg.TLSConfig != nil)

	errCh := make(chan error, 1)
	go func() { errCh <- httpx.ListenAndServe(ctx, srv) }()

	select {
	case err := <-errCh:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			return fmt.Errorf("issuer: serve: %w", err)
		}
		return nil
	case <-ctx.Done():
		shutCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
		defer cancel()
		if err := srv.Shutdown(shutCtx); err != nil {
			return fmt.Errorf("issuer: shutdown: %w", err)
		}
		return nil
	}
}
