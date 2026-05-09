// SPDX-License-Identifier: MIT

package scaserver

import (
	"context"
	"crypto/tls"
	"errors"
	"log/slog"

	"github.com/shadownet-protocol/shadownet-go/pkg/httpx"
	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
)

// RunConfig wires a configured *sca.Issuer to a hardened HTTP listener.
type RunConfig struct {
	// Issuer is the already-validated SCA orchestrator. Run does NOT call
	// Issuer.Validate; callers should before passing it in.
	Issuer *sca.Issuer

	// Listen is the host:port to bind. ":8443" or "127.0.0.1:8443" are
	// typical. An empty host listens on every interface; the loopback
	// warning fires when TLS is also nil.
	Listen string

	// TLS is the TLS configuration. nil = plaintext HTTP, which is only
	// appropriate behind a TLS-terminating reverse proxy or for explicit
	// loopback dev binds.
	TLS *tls.Config

	// Logger receives access logs, panic recoveries, and the startup line.
	// nil → slog.Default().
	Logger *slog.Logger
}

// Validate asserts the run-config is internally consistent.
func (c *RunConfig) Validate() error {
	switch {
	case c.Issuer == nil:
		return errors.New("scaserver: RunConfig.Issuer required")
	case c.Listen == "":
		return errors.New("scaserver: RunConfig.Listen required")
	}
	return nil
}

// Run starts the SCA HTTP server, blocking until ctx is canceled or the
// listener fails. On ctx cancellation it gracefully drains in-flight
// requests with a 15-second deadline.
//
// Run emits a Warn log line when binding plaintext on a non-loopback address.
func Run(ctx context.Context, cfg RunConfig) error {
	if err := cfg.Validate(); err != nil {
		return err
	}
	logger := cfg.Logger
	if logger == nil {
		logger = slog.Default()
	}
	httpx.WarnIfNotLoopback(logger, cfg.Listen, cfg.TLS != nil)

	srv := httpx.NewServer(cfg.Issuer.Handler(), httpx.ServerOptions{
		Addr:      cfg.Listen,
		TLSConfig: cfg.TLS,
		Logger:    logger,
	})
	logger.Info(
		"sca-server listening",
		slog.String("did", cfg.Issuer.DID),
		slog.String("listen", cfg.Listen),
		slog.Bool("tls", cfg.TLS != nil),
	)
	return httpx.ListenAndServe(ctx, srv)
}
