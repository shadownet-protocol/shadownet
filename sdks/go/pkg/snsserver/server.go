// SPDX-License-Identifier: MIT

package snsserver

import (
	"context"
	"crypto/tls"
	"errors"
	"log/slog"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/httpx"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sns"
)

// RunConfig wires a configured *sns.Server to a hardened HTTP listener.
type RunConfig struct {
	// Server is the already-validated SNS handler. Run does NOT call
	// Server.Validate; callers should before passing it in.
	Server *sns.Server

	// Listen is the host:port to bind.
	Listen string

	// TLS is the TLS configuration. nil = plaintext HTTP.
	TLS *tls.Config

	// Logger receives access logs, panic recoveries, and the startup line.
	Logger *slog.Logger
}

// Validate asserts the run-config is internally consistent.
func (c *RunConfig) Validate() error {
	switch {
	case c.Server == nil:
		return errors.New("snsserver: RunConfig.Server required")
	case c.Listen == "":
		return errors.New("snsserver: RunConfig.Listen required")
	}
	return nil
}

// Run starts the SNS HTTP server, blocking until ctx is canceled or the
// listener fails. Graceful shutdown (15-second drain) on ctx cancellation.
func Run(ctx context.Context, cfg RunConfig) error {
	if err := cfg.Validate(); err != nil {
		return err
	}
	logger := cfg.Logger
	if logger == nil {
		logger = slog.Default()
	}
	httpx.WarnIfNotLoopback(logger, cfg.Listen, cfg.TLS != nil)

	srv := httpx.NewServer(cfg.Server.Handler(), httpx.ServerOptions{
		Addr:      cfg.Listen,
		TLSConfig: cfg.TLS,
		Logger:    logger,
	})
	logger.Info(
		"sns-server listening",
		slog.String("did", cfg.Server.ProviderDID),
		slog.String("provider", cfg.Server.Provider),
		slog.String("listen", cfg.Listen),
		slog.Bool("tls", cfg.TLS != nil),
	)
	return httpx.ListenAndServe(ctx, srv)
}
